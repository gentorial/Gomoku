// Small CI-generated exports exercise the same complete architecture and ABI.
// Their expected values come from the independent Python integer reference.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import createModule from "../packages/engine-wasm/generated/gomoku-engine.mjs";

const [modelPath, referencePath] = process.argv.slice(2);
const bytes = await readFile(modelPath);
const size = bytes.readUInt32LE(40),
  rule = bytes.readUInt32LE(44) === 0 ? "freestyle" : "standard";
const engine = await createModule({
  wasmBinary: await readFile(
    new URL("../packages/engine-wasm/generated/gomoku-engine.wasm", import.meta.url),
  ),
});
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
const references = JSON.parse(await readFile(referencePath, "utf8"));
for (const vector of references.vectors) {
  const board = vector.board ?? Array.from({ length: size }, () => Array(size).fill(0));
  if (!vector.board)
    vector.moves.forEach(({ x, y }, i) => {
      board[y][x] = (i % 2) + 1;
    });
  const result = JSON.parse(
    engine.ccall(
      "gomoku_nnue_predict",
      "string",
      ["string"],
      [
        JSON.stringify({
          size,
          rule,
          board,
          toMove: vector.toMove ?? (vector.moves.length % 2) + 1,
        }),
      ],
    ),
  );
  assert.deepEqual(result.valueLogits, vector.valueLogits);
  assert.deepEqual(result.policyLogits, vector.policyLogits);
}
// Header, descriptor, range, truncation and trailing bytes are distinct rejection paths.
for (const [offset, value] of [
  [8, 2],
  [20, 0],
  [88, 4],
  [92, 0],
  [112, 32767],
  [bytes.length - 2, 32767],
]) {
  const bad = Buffer.from(bytes);
  bad.writeUInt16LE(value, offset);
  const pointer = engine._malloc(bad.length);
  try {
    engine.HEAPU8.set(bad, pointer);
    assert.notEqual(
      engine.ccall("gomoku_load_model", "string", ["number", "number"], [pointer, bad.length]),
      "",
    );
  } finally {
    engine._free(pointer);
  }
}
console.log(
  "WASM matches independent reference on " +
    references.vectors.length +
    " positions (" +
    size +
    ", " +
    rule +
    ")",
);
