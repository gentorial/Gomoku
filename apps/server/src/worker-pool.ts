import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import {
  WorkerRequestSchema,
  WorkerResponseSchema,
  type WorkerPayload,
  type WorkerRequest,
  type WorkerResult,
  type Engine,
} from "@gomoku/contracts";
export type { Engine } from "@gomoku/contracts";

export const defaultWorkerPath = fileURLToPath(
  new URL(
    "../../../build/dev/bin/gomoku-worker" + (process.platform === "win32" ? ".exe" : ""),
    import.meta.url,
  ),
);
export class EngineError extends Error {
  constructor(
    public readonly code: string,
    message: string,
  ) {
    super(message);
  }
}
class Slot {
  busy = false;
  private process?: ChildProcessWithoutNullStreams;
  private pending?: {
    id: string;
    resolve: (value: WorkerResult) => void;
    reject: (error: Error) => void;
  };
  constructor(private readonly executable: string) {}
  private start() {
    const child = spawn(this.executable, [], { stdio: "pipe", windowsHide: true });
    this.process = child;
    child.stdin.on("error", (error: Error) => {
      if (this.process !== child) return;
      this.pending?.reject(new EngineError("ENGINE_UNAVAILABLE", error.message));
      this.destroy();
    });
    const output = createInterface({ input: child.stdout });
    let diagnostic = "";
    child.stderr.on("data", (chunk: Buffer) => {
      diagnostic = (diagnostic + chunk.toString("utf8")).slice(-4096);
    });
    output.on("line", (line) => {
      if (this.process !== child || !this.pending) return;
      try {
        if (line.length > 65536) throw new Error("Worker response too large");
        const response = WorkerResponseSchema.parse(JSON.parse(line));
        if (response.id !== this.pending.id) throw new Error("Worker response id mismatch");
        if (response.ok) this.pending.resolve(response.result);
        else this.pending.reject(new EngineError(response.error.code, response.error.message));
      } catch (error) {
        this.pending?.reject(new EngineError("PROTOCOL_ERROR", String(error)));
        this.destroy();
      }
    });
    child.on("error", (error) => {
      if (this.process !== child) return;
      this.pending?.reject(new EngineError("ENGINE_UNAVAILABLE", error.message));
      this.destroy();
    });
    child.on("exit", (code) => {
      if (this.process !== child) return;
      this.pending?.reject(
        new EngineError("ENGINE_EXITED", "Worker exited (" + code + "). " + diagnostic),
      );
      this.destroy();
    });
  }
  async run(request: WorkerRequest, signal?: AbortSignal): Promise<WorkerResult> {
    if (signal?.aborted) throw new EngineError("CANCELLED", "Search cancelled");
    if (!this.process) this.start();
    const budget = request.method === "analyze" ? request.limits.timeMs + 3000 : 5000;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const abort = () => {
      this.pending?.reject(new EngineError("CANCELLED", "Search cancelled"));
      this.destroy();
    };
    try {
      return await new Promise<WorkerResult>((resolve, reject) => {
        this.pending = { id: request.id, resolve, reject };
        signal?.addEventListener("abort", abort, { once: true });
        timer = setTimeout(() => {
          reject(new EngineError("ENGINE_TIMEOUT", "Engine response deadline exceeded"));
          this.destroy();
        }, budget);
        this.process!.stdin.write(JSON.stringify(request) + "\n", (error) => {
          if (error) {
            reject(new EngineError("ENGINE_UNAVAILABLE", error.message));
            this.destroy();
          }
        });
      });
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      this.pending = undefined;
    }
  }
  destroy() {
    const child = this.process;
    this.process = undefined;
    child?.kill();
  }
  close() {
    this.pending?.reject(new EngineError("ENGINE_CLOSED", "Engine pool closed"));
    this.destroy();
  }
}
type Job = {
  request: WorkerRequest;
  signal?: AbortSignal;
  resolve: (result: WorkerResult) => void;
  reject: (error: Error) => void;
  abort: () => void;
};
export class WorkerPool implements Engine {
  private readonly slots: Slot[];
  private queue: Job[] = [];
  private closed = false;
  constructor(executable = defaultWorkerPath, size = 2) {
    if (!Number.isInteger(size) || size < 1 || size > 16)
      throw new Error("Worker count must be 1..16");
    this.slots = Array.from({ length: size }, () => new Slot(executable));
  }
  request(payload: WorkerPayload, signal?: AbortSignal): Promise<WorkerResult> {
    if (this.closed) return Promise.reject(new EngineError("ENGINE_CLOSED", "Engine pool closed"));
    if (signal?.aborted) return Promise.reject(new EngineError("CANCELLED", "Search cancelled"));
    if (this.queue.length >= 64)
      return Promise.reject(new EngineError("ENGINE_BUSY", "Engine queue full"));
    const request = WorkerRequestSchema.parse({ ...payload, v: 1, id: randomUUID() });
    return new Promise((resolve, reject) => {
      const job: Job = {
        request,
        signal,
        resolve,
        reject,
        abort: () => {
          this.queue = this.queue.filter((item) => item !== job);
          reject(new EngineError("CANCELLED", "Search cancelled"));
        },
      };
      signal?.addEventListener("abort", job.abort, { once: true });
      this.queue.push(job);
      this.drain();
    });
  }
  private drain() {
    for (const slot of this.slots) {
      if (slot.busy || this.closed) continue;
      const job = this.queue.shift();
      if (!job) break;
      slot.busy = true;
      job.signal?.removeEventListener("abort", job.abort);
      void slot
        .run(job.request, job.signal)
        .then(job.resolve, job.reject)
        .finally(() => {
          slot.busy = false;
          this.drain();
        });
    }
  }
  close() {
    this.closed = true;
    for (const job of this.queue) {
      job.signal?.removeEventListener("abort", job.abort);
      job.reject(new EngineError("ENGINE_CLOSED", "Engine pool closed"));
    }
    this.queue = [];
    for (const slot of this.slots) slot.close();
  }
}
