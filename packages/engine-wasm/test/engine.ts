import { readFile } from "node:fs/promises";
import {
  WorkerRequestSchema,
  WorkerResponseSchema,
  type Engine,
  type WorkerPayload,
} from "@gomoku/contracts";
import createGomokuModule from "../generated/gomoku-engine.mjs";

export async function loadEngine() {
  return createGomokuModule({
    wasmBinary: await readFile(new URL("../generated/gomoku-engine.wasm", import.meta.url)),
  });
}
export async function createTestEngine(): Promise<Engine> {
  const module = await loadEngine();
  let serial = 0;
  return {
    async request(payload: WorkerPayload, signal?: AbortSignal) {
      signal?.throwIfAborted();
      const request = WorkerRequestSchema.parse({ ...payload, v: 1, id: String(++serial) });
      const response = WorkerResponseSchema.parse(
        JSON.parse(module.ccall("gomoku_request", "string", ["string"], [JSON.stringify(request)])),
      );
      if (!response.ok) throw new Error(response.error.message);
      return response.result;
    },
    close() {},
  };
}
