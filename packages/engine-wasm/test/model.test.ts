import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { installModel, loadBrowserModel, parseModel } from "../src/model.js";
import { BrowserEngine } from "../src/index.js";
import type { GomokuModule } from "../generated/gomoku-engine.mjs";

const bytes = new Uint8Array(64).fill(7);
const hash = createHash("sha256").update(bytes).digest("hex");
const manifest = {
  formatVersion: 1,
  architecture: "line11-dual-v1",
  id: "test-v1",
  label: "Test",
  size: 15,
  rule: "freestyle",
  bytes: bytes.length,
  sha256: hash,
  weights: hash + "/weights.gnn",
};
const url = "https://example.test/Gomoku/models/active.json";
const weightUrl = new URL(manifest.weights, url).href;
function engine() {
  let freed = 0,
    installed = 0;
  const module = {
    HEAPU8: new Uint8Array(256),
    _malloc() {
      return 16;
    },
    _free() {
      ++freed;
    },
    ccall(name: string) {
      if (name === "gomoku_load_model") {
        ++installed;
        return "";
      }
      return JSON.stringify({ ok: true, result: { nnue: { size: 15, rule: "freestyle" } } });
    },
  } as unknown as GomokuModule;
  return { module, counts: () => ({ freed, installed }) };
}

test("verified weights use the Pages subpath, persist in cache, and reload without downloading", async (t) => {
  const stored = new Map<string, Response>();
  const previous = Object.getOwnPropertyDescriptor(globalThis, "caches");
  Object.defineProperty(globalThis, "caches", {
    configurable: true,
    value: {
      open: async () => ({
        match: async (key: string) => stored.get(key)?.clone(),
        put: async (key: string, value: Response) => {
          stored.set(key, value);
        },
        delete: async (key: string) => stored.delete(key),
      }),
    },
  });
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, "caches", previous);
    else Reflect.deleteProperty(globalThis, "caches");
  });
  let downloads = 0;
  t.mock.method(globalThis, "fetch", async (input: string) => {
    if (input === url) return Response.json(manifest);
    assert.equal(input, weightUrl);
    ++downloads;
    return new Response(bytes);
  });
  const first = engine();
  const phases: string[] = [];
  assert.equal(
    (await loadBrowserModel(first.module, url, (state) => phases.push(state.phase))).id,
    "test-v1",
  );
  assert.deepEqual(first.counts(), { freed: 1, installed: 1 });
  assert.ok(
    phases.includes("downloading") && phases.includes("verifying") && phases.at(-1) === "ready",
  );
  await loadBrowserModel(engine().module, url, () => {});
  assert.equal(downloads, 1);
  stored.set(weightUrl, new Response(new Uint8Array(64)));
  await loadBrowserModel(engine().module, url, () => {});
  assert.equal(downloads, 2, "Corrupt cache must be discarded and downloaded again");
});

test("integrity failures never reach the WASM loader or silently use the baseline", async (t) => {
  const target = engine();
  t.mock.method(globalThis, "fetch", async (input: string) =>
    input === url ? Response.json(manifest) : new Response(new Uint8Array(64)),
  );
  await assert.rejects(
    loadBrowserModel(target.module, url, () => {}),
    /校验失败/,
  );
  assert.equal(target.counts().installed, 0);
  t.mock.method(globalThis, "fetch", async (input: string) =>
    input === url ? Response.json(manifest) : new Response(new Uint8Array(65)),
  );
  await assert.rejects(
    loadBrowserModel(target.module, url, () => {}),
    /长度错误/,
  );
});

test("model manifest validation and rejected installation preserve memory ownership", () => {
  for (const patch of [
    { weights: "../../weights.gnn" },
    { bytes: 2 ** 40 },
    { rule: "renju" },
    { formatVersion: 2 },
  ])
    assert.throws(() => parseModel({ ...manifest, ...patch }));
  const target = engine();
  target.module.ccall = (() => "Malformed tensor") as GomokuModule["ccall"];
  assert.throws(() => installModel(target.module, bytes), /Malformed tensor/);
  assert.equal(target.counts().freed, 1);
});

test("aborting a model load terminates its worker, ignores stale progress, and permits a new game", async (t) => {
  const workers: FakeWorker[] = [];
  class FakeWorker {
    onmessage?: (event: { data: unknown }) => void;
    onerror?: unknown;
    onmessageerror?: unknown;
    messages: { request: { id: string } }[] = [];
    terminated = false;
    constructor() {
      workers.push(this);
    }
    postMessage(message: { request: { id: string } }) {
      this.messages.push(message);
    }
    terminate() {
      this.terminated = true;
    }
  }
  const previous = Object.getOwnPropertyDescriptor(globalThis, "Worker");
  Object.defineProperty(globalThis, "Worker", { configurable: true, value: FakeWorker });
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, "Worker", previous);
    else Reflect.deleteProperty(globalThis, "Worker");
  });
  const phases: unknown[] = [];
  const client = new BrowserEngine({
    modelManifestUrl: url,
    onModelProgress: (state) => phases.push(state),
  });
  t.after(() => client.close());
  const abort = new AbortController();
  const request = client.request(
    {
      method: "analyze",
      evaluator: "nnue",
      position: { size: 15, rule: "freestyle", moves: [] },
      limits: { timeMs: 300 },
    },
    abort.signal,
  );
  const rejection = assert.rejects(request, { name: "AbortError" });
  abort.abort();
  await rejection;
  assert.ok(workers[0]!.terminated);
  const count = phases.length;
  workers[0]!.onmessage?.({
    data: { type: "model-progress", id: "1", phase: "ready", loaded: 64, total: 64 },
  });
  assert.equal(phases.length, count);
  const next = client.request({ method: "about" });
  workers[1]!.onmessage?.({
    data: {
      v: 1,
      id: workers[1]!.messages[0]!.request.id,
      ok: true,
      result: {
        kind: "about",
        name: "test",
        version: "1",
        protocolVersion: 1,
        rules: ["freestyle"],
        sizes: [15],
        evaluator: "handcrafted-v1",
      },
    },
  });
  assert.equal((await next).kind, "about");
});
