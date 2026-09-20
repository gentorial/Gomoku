import type { GomokuModule } from "../generated/gomoku-engine.mjs";

export type ModelProgress = {
  phase: "downloading" | "verifying" | "initializing" | "ready";
  loaded: number;
  total: number;
};
export type BrowserModel = {
  formatVersion: 1;
  id: string;
  label: string;
  architecture: "line11-dual-v1";
  size: 15 | 20;
  rule: "freestyle" | "standard";
  weights: string;
  bytes: number;
  sha256: string;
};

export function parseModel(value: unknown): BrowserModel {
  const model = value as BrowserModel;
  if (
    !model ||
    model.formatVersion !== 1 ||
    model.architecture !== "line11-dual-v1" ||
    typeof model.id !== "string" ||
    !/^[a-z0-9-]+$/.test(model.id) ||
    typeof model.label !== "string" ||
    model.label.length > 80 ||
    ![15, 20].includes(model.size) ||
    !["freestyle", "standard"].includes(model.rule) ||
    !Number.isSafeInteger(model.bytes) ||
    model.bytes < 56 ||
    model.bytes > 512 * 1024 * 1024 ||
    typeof model.sha256 !== "string" ||
    !/^[a-f0-9]{64}$/.test(model.sha256) ||
    model.weights !== model.sha256 + "/weights.gnn"
  )
    throw new Error("无效的 NNUE 模型清单");
  return model;
}

export function installModel(engine: GomokuModule, bytes: Uint8Array) {
  const pointer = engine._malloc(bytes.byteLength);
  if (!pointer) throw new Error("内存不足，无法加载 NNUE");
  try {
    // malloc may grow the WASM memory; read HEAPU8 after allocation.
    engine.HEAPU8.set(bytes, pointer);
    const error = engine.ccall(
      "gomoku_load_model",
      "string",
      ["number", "number"],
      [pointer, bytes.byteLength],
    );
    if (error) throw new Error("NNUE 加载失败：" + error);
  } finally {
    engine._free(pointer);
  }
}

export async function loadBrowserModel(
  engine: GomokuModule,
  manifestUrl: string,
  progress: (state: ModelProgress) => void,
): Promise<BrowserModel> {
  const response = await fetch(manifestUrl, {
    cache: "no-cache",
    signal: AbortSignal.timeout(30000),
  });
  if (!response.ok) throw new Error("NNUE 模型清单加载失败，请重试");
  const model = parseModel(await response.json());
  const url = new URL(model.weights, manifestUrl).href;
  let cache: Cache | undefined;
  try {
    cache = await caches.open("gomoku-nnue-v1");
  } catch {
    /* Private browsing/quota: memory still works. */
  }
  const report = (phase: ModelProgress["phase"], loaded = model.bytes) =>
    progress({ phase, loaded, total: model.bytes });
  async function read(response: Response, downloading: boolean) {
    if (!response.ok || !response.body) throw new Error("NNUE 权重下载失败，请重试");
    const bytes = new Uint8Array(model.bytes);
    const reader = response.body.getReader();
    let received = 0;
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        if (received + value.length > bytes.length) throw new Error("NNUE 权重长度错误");
        bytes.set(value, received);
        received += value.length;
        if (downloading) report("downloading", received);
      }
    } finally {
      await reader.cancel().catch(() => {});
    }
    if (received !== model.bytes) throw new Error("NNUE 权重下载不完整，请重试");
    report("verifying");
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    const sha256 = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join(
      "",
    );
    if (sha256 !== model.sha256) throw new Error("NNUE 权重校验失败，请重试");
    return bytes;
  }
  let bytes: Uint8Array | undefined;
  const cached = await cache?.match(url).catch(() => undefined);
  if (cached) {
    try {
      bytes = await read(cached, false);
    } catch {
      await cache?.delete(url).catch(() => {});
    }
  }
  if (!bytes) {
    report("downloading", 0);
    bytes = await read(await fetch(url, { signal: AbortSignal.timeout(300000) }), true);
    try {
      await cache?.put(url, new Response(bytes as Uint8Array<ArrayBuffer>));
    } catch {
      /* A full cache must not prevent a verified model from running. */
    }
  }
  report("initializing");
  installModel(engine, bytes);
  // The loader must agree with the manifest's board/rule before the AI is used.
  const about = JSON.parse(
    engine.ccall(
      "gomoku_request",
      "string",
      ["string"],
      [JSON.stringify({ v: 1, id: "model-check", method: "about" })],
    ),
  );
  if (!about.ok || about.result.nnue?.size !== model.size || about.result.nnue?.rule !== model.rule)
    throw new Error("NNUE 权重与清单的棋盘或规则不一致");
  report("ready");
  return model;
}
