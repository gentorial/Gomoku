import { WorkerRequestSchema } from "@gomoku/contracts";
import createGomokuModule from "../generated/gomoku-engine.mjs";
import { loadBrowserModel, type BrowserModel } from "./model.js";

// Vite copies this asset next to the worker bundle, including under a URL base.
const wasmUrl = new URL("../generated/gomoku-engine.wasm", import.meta.url).href;
const module = createGomokuModule({ locateFile: () => wasmUrl });
// Attach a handler immediately, including when loading fails before the first message.
void module.catch(() => {});
let queue = Promise.resolve();
let loadedModel: BrowserModel | null = null;
self.onmessage = (event: MessageEvent<{ request: unknown; modelManifestUrl?: string }>) => {
  queue = queue.then(async () => {
    let id = "invalid-request";
    try {
      const request = WorkerRequestSchema.parse(event.data.request);
      id = request.id;
      const engine = await module;
      if (request.method === "analyze" && request.evaluator === "nnue") {
        if (!event.data.modelManifestUrl)
          throw new Error("未配置 NNUE 权重，请在对局设置中选择基础引擎");
        if (!loadedModel)
          loadedModel = await loadBrowserModel(engine, event.data.modelManifestUrl, (state) => {
            self.postMessage({ type: "model-progress", id, ...state });
          });
        if (
          loadedModel.size !== request.position.size ||
          loadedModel.rule !== request.position.rule
        )
          throw new Error(
            "该 NNUE 仅支持 " + loadedModel.size + "×" + loadedModel.size + " " + loadedModel.rule,
          );
      }
      // A loaded NNUE must never silently replace an explicitly selected baseline.
      const actual =
        request.method === "analyze"
          ? { ...request, evaluator: request.evaluator ?? "handcrafted" }
          : request;
      const response = engine.ccall(
        "gomoku_request",
        "string",
        ["string"],
        [JSON.stringify(actual)],
      );
      const result = JSON.parse(response);
      if (
        result.ok &&
        result.result.kind === "analysis" &&
        result.result.evaluator === "line11-nnue-v1" &&
        loadedModel
      ) {
        result.result.model = {
          id: loadedModel.id,
          label: loadedModel.label,
          sha256: loadedModel.sha256,
        };
      }
      self.postMessage(result);
    } catch (error) {
      self.postMessage({
        v: 1,
        id,
        ok: false,
        error: {
          code: "ENGINE_ERROR",
          message: error instanceof Error ? error.message : "Engine failed",
        },
      });
    }
  });
};
