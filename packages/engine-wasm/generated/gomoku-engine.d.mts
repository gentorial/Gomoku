export interface GomokuModule {
  HEAPU8: Uint8Array;
  _malloc(size: number): number;
  _free(pointer: number): void;
  ccall(name: "gomoku_request" | "gomoku_nnue_predict", result: "string", types: ["string"], args: [string]): string;
  ccall(name: "gomoku_load_model", result: "string", types: ["number", "number"], args: [number, number]): string;
}
export default function createGomokuModule(options?: {
  locateFile?: (path: string) => string;
  wasmBinary?: Uint8Array;
}): Promise<GomokuModule>;
