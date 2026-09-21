import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { root } from "./process.mjs";
import createModule from "../packages/engine-wasm/generated/gomoku-engine.mjs";

const [modelArg, vectorsArg, outputArg] = process.argv.slice(2);
assert.ok(
  modelArg && vectorsArg && outputArg,
  "Usage: verify-candidate.mjs weights.gnn vectors.json report.json",
);
const model = resolve(modelArg);
const vectorsPath = resolve(vectorsArg);
const bytes = await readFile(model);
const vectorsBytes = await readFile(vectorsPath);
const reference = JSON.parse(vectorsBytes);
assert.equal(reference.format, "line-nnue-reference-v1");
assert.ok(reference.vectors.length >= 1);
const native = JSON.parse(
  execFileSync(
    resolve(root, "build/dev/bin/gomoku-nnue-check" + (process.platform === "win32" ? ".exe" : "")),
    [model, vectorsPath],
    { encoding: "utf8", timeout: 180000, windowsHide: true },
  ),
);
const wasmBytes = await readFile(
  resolve(root, "packages/engine-wasm/generated/gomoku-engine.wasm"),
);
const engine = await createModule({ wasmBinary: wasmBytes });
const pointer = engine._malloc(bytes.length);
assert.ok(pointer);
try {
  engine.HEAPU8.set(bytes, pointer);
  assert.equal(
    engine.ccall("gomoku_load_model", "string", ["number", "number"], [pointer, bytes.length]),
    "",
  );
} finally {
  engine._free(pointer);
}
for (const vector of reference.vectors) {
  const result = JSON.parse(
    engine.ccall(
      "gomoku_nnue_predict",
      "string",
      ["string"],
      [
        JSON.stringify({
          board: vector.board,
          toMove: vector.toMove,
          size: vector.board.length,
          rule: "freestyle",
        }),
      ],
    ),
  );
  assert.deepEqual(
    result.valueLogits,
    vector.valueLogits,
    "WASM value differs from integer reference",
  );
  assert.deepEqual(
    result.policyLogits,
    vector.policyLogits,
    "WASM policy differs from integer reference",
  );
}
const sha256 = (value) => createHash("sha256").update(value).digest("hex");
const report = {
  kind: "gomoku-candidate-runtime-v1",
  passed: true,
  modelSha256: sha256(bytes),
  referenceSha256: sha256(vectorsBytes),
  wasmSha256: sha256(wasmBytes),
  native,
  wasm: { positions: reference.vectors.length, maximumAbsoluteError: 0 },
};
await writeFile(resolve(outputArg), JSON.stringify(report, null, 2) + "\n");
console.log(JSON.stringify(report));
