import { readFile } from "node:fs/promises";
import {
  WorkerRequestSchema,
  WorkerResponseSchema,
  type Engine,
  type WorkerPayload,
} from "@gomoku/contracts";
import createGomokuModule from "@gomoku/engine-wasm/generated/gomoku-engine.mjs";

/**
 * In-process WASM rules engine for online games. It only inspects positions and
 * plays moves, which take microseconds, so it never blocks the event loop with a search.
 */
export async function createReferee(): Promise<Engine> {
  // A bundled deployment has no node_modules; it names the copied file instead.
  const wasm = process.env.GOMOKU_WASM
    ? process.env.GOMOKU_WASM
    : new URL(import.meta.resolve("@gomoku/engine-wasm/generated/gomoku-engine.wasm"));
  const module = await createGomokuModule({ wasmBinary: await readFile(wasm) });
  let serial = 0;
  return {
    async request(payload: WorkerPayload) {
      if (payload.method === "analyze") throw new Error("The referee does not search");
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
