import {
  WorkerRequestSchema,
  WorkerResponseSchema,
  type Engine,
  type WorkerPayload,
  type WorkerResult,
} from "@gomoku/contracts";
import type { ModelProgress } from "./model.js";
import {
  diagnosticLevel,
  emitDiagnostic,
  logDiagnostic,
  type DiagnosticRecord,
} from "./diagnostics.js";
export { configureDiagnostics, logDiagnostic } from "./diagnostics.js";
export type { DiagnosticRecord, LogLevel } from "./diagnostics.js";
export type { ModelProgress } from "./model.js";

type Pending = {
  method: string;
  started: number;
  resolve: (result: WorkerResult) => void;
  reject: (error: Error) => void;
  cleanup: () => void;
};

export class BrowserEngine implements Engine {
  private worker: Worker | null = null;
  private pending = new Map<string, Pending>();
  private serial = 0;
  private readonly session = Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
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
    logDiagnostic("gui-engine", "worker.created");
    worker.onmessage = (event: MessageEvent<unknown>) => {
      if (this.worker !== worker) return;
      const diagnostic = event.data as { type?: string; record?: DiagnosticRecord };
      if (diagnostic?.type === "diagnostic" && diagnostic.record) {
        emitDiagnostic(diagnostic.record);
        return;
      }
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
      if (!request) {
        logDiagnostic("gui-engine", "response.discarded", { requestId: response.id }, "debug");
        return;
      }
      this.pending.delete(response.id);
      request.cleanup();
      logDiagnostic(
        "gui-engine",
        response.ok ? "request.completed" : "request.failed",
        {
          requestId: response.id,
          method: request.method,
          elapsedMs: performance.now() - request.started,
          ...(response.ok ? { kind: response.result.kind } : { error: response.error }),
        },
        response.ok ? "info" : "error",
      );
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
    const id = this.session + "-" + String(++this.serial);
    const request = WorkerRequestSchema.parse({ ...payload, v: 1, id });
    logDiagnostic("gui-engine", "request.submitted", {
      requestId: id,
      method: request.method,
      ...("position" in request
        ? {
            plyCount: request.position.moves.length,
            size: request.position.size,
            rule: request.position.rule,
          }
        : {}),
      ...(request.method === "analyze"
        ? { evaluator: request.evaluator ?? "handcrafted", limits: request.limits }
        : {}),
    });
    const worker = this.getWorker();
    return new Promise((resolve, reject) => {
      // Terminating the worker interrupts synchronous WASM immediately. A fresh
      // module is created lazily; no late search can write into a newer game.
      const abort = () => {
        logDiagnostic("gui-engine", "request.cancelled", { requestId: id, method: request.method });
        this.reset(new DOMException("Cancelled", "AbortError"));
      };
      const timeout = setTimeout(
        () => {
          logDiagnostic(
            "gui-engine",
            "request.timeout",
            { requestId: id, method: request.method },
            "error",
          );
          this.reset(new Error("引擎响应超时，请重试"));
        },
        payload.method === "analyze" && payload.evaluator === "nnue" ? 360000 : 30000,
      );
      const cleanup = () => {
        clearTimeout(timeout);
        signal?.removeEventListener("abort", abort);
      };
      this.pending.set(id, {
        resolve,
        reject,
        cleanup,
        method: request.method,
        started: performance.now(),
      });
      signal?.addEventListener("abort", abort, { once: true });
      try {
        worker.postMessage({
          request,
          modelManifestUrl: this.options.modelManifestUrl,
          logLevel: diagnosticLevel(),
        });
      } catch (error) {
        this.reset(error instanceof Error ? error : new Error("引擎通信失败"));
      }
    });
  }

  private reset(error: Error) {
    logDiagnostic(
      "gui-engine",
      "worker.reset",
      { reason: error.message, pending: this.pending.size },
      error.name === "AbortError" ? "info" : "error",
    );
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
