import {
  WorkerRequestSchema,
  WorkerResponseSchema,
  type Engine,
  type WorkerPayload,
  type WorkerResult,
} from "@gomoku/contracts";
import type { ModelProgress } from "./model.js";
export type { ModelProgress } from "./model.js";

type Pending = {
  resolve: (result: WorkerResult) => void;
  reject: (error: Error) => void;
  cleanup: () => void;
};

export class BrowserEngine implements Engine {
  private worker: Worker | null = null;
  private pending = new Map<string, Pending>();
  private serial = 0;
  private closed = false;
  constructor(
    private options: {
      modelManifestUrl?: string;
      onModelProgress?: (state: ModelProgress | null) => void;
    } = {},
  ) {}

  private getWorker() {
    if (this.closed) throw new Error("Engine is closed");
    if (this.worker) return this.worker;
    const worker = new Worker(new URL("./engine.worker.js", import.meta.url), { type: "module" });
    worker.onmessage = (event: MessageEvent<unknown>) => {
      if (this.worker !== worker) return;
      const progress = event.data as ModelProgress & { type?: string; id?: string };
      if (progress?.type === "model-progress" && progress.id && this.pending.has(progress.id)) {
        this.options.onModelProgress?.(progress);
        return;
      }
      const parsed = WorkerResponseSchema.safeParse(event.data);
      if (!parsed.success) {
        this.reset(new Error("引擎返回了无效数据"));
        return;
      }
      const response = parsed.data;
      const request = this.pending.get(response.id);
      if (!request) return;
      this.pending.delete(response.id);
      request.cleanup();
      if (response.ok) request.resolve(response.result);
      else request.reject(new Error(response.error.message));
    };
    worker.onerror = (event) => {
      event.preventDefault();
      if (this.worker === worker) this.reset(new Error("引擎加载失败，请重试：" + event.message));
    };
    worker.onmessageerror = () => {
      if (this.worker === worker) this.reset(new Error("引擎通信失败，请重试"));
    };
    this.worker = worker;
    return worker;
  }

  async request(payload: WorkerPayload, signal?: AbortSignal): Promise<WorkerResult> {
    if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
    const id = String(++this.serial);
    const request = WorkerRequestSchema.parse({ ...payload, v: 1, id });
    const worker = this.getWorker();
    return new Promise((resolve, reject) => {
      // Terminating the worker interrupts synchronous WASM immediately. A fresh
      // module is created lazily; no late search can write into a newer game.
      const abort = () => this.reset(new DOMException("Cancelled", "AbortError"));
      const timeout = setTimeout(
        () => this.reset(new Error("引擎响应超时，请重试")),
        payload.method === "analyze" && payload.evaluator === "nnue" ? 360000 : 30000,
      );
      const cleanup = () => {
        clearTimeout(timeout);
        signal?.removeEventListener("abort", abort);
      };
      this.pending.set(id, { resolve, reject, cleanup });
      signal?.addEventListener("abort", abort, { once: true });
      try {
        worker.postMessage({ request, modelManifestUrl: this.options.modelManifestUrl });
      } catch (error) {
        this.reset(error instanceof Error ? error : new Error("引擎通信失败"));
      }
    });
  }

  private reset(error: Error) {
    this.worker?.terminate();
    this.worker = null;
    this.options.onModelProgress?.(null);
    for (const request of this.pending.values()) {
      request.cleanup();
      request.reject(error);
    }
    this.pending.clear();
  }

  close() {
    this.closed = true;
    this.reset(new DOMException("Closed", "AbortError"));
  }
}
