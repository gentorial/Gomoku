import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { root } from "./process.mjs";
import createModule from "../packages/engine-wasm/generated/gomoku-engine.mjs";

const pin = JSON.parse(await readFile(resolve(root, "models/web-model.json"), "utf8"));
const path = resolve(root, "apps/web/public/models", pin.sha256, "weights.gnn");
const referencePath = resolve(root, `tests/fixtures/nnue-${pin.id}.json`);
const reference = JSON.parse(await readFile(referencePath, "utf8"));
const bytes = await readFile(path);
assert.equal(bytes.length, pin.bytes);
assert.equal(createHash("sha256").update(bytes).digest("hex"), pin.sha256);
const native = JSON.parse(
  execFileSync(
    resolve(root, "build/dev/bin/gomoku-nnue-check" + (process.platform === "win32" ? ".exe" : "")),
    [path, referencePath],
    { encoding: "utf8", timeout: 180000, windowsHide: true },
  ),
);
const engine = await createModule({
  wasmBinary: await readFile(resolve(root, "packages/engine-wasm/generated/gomoku-engine.wasm")),
});
function install(bytes) {
  const pointer = engine._malloc(bytes.length);
  assert.ok(pointer);
  try {
    engine.HEAPU8.set(bytes, pointer);
    return engine.ccall(
      "gomoku_load_model",
      "string",
      ["number", "number"],
      [pointer, bytes.length],
    );
  } finally {
    engine._free(pointer);
  }
}
assert.equal(install(bytes), "");
let values = 0,
  policies = 0;
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
          size: pin.size,
          rule: pin.rule,
        }),
      ],
    ),
  );
  assert.deepEqual(
    result.valueLogits,
    vector.valueLogits,
    "WASM value must exactly match Python int64 reference",
  );
  assert.deepEqual(
    result.policyLogits,
    vector.policyLogits,
    "WASM policy must exactly match Python int64 reference",
  );
  values += 3;
  policies += vector.policyLogits.length;
}
let serial = 0;
const request = (payload) => {
  const response = JSON.parse(
    engine.ccall(
      "gomoku_request",
      "string",
      ["string"],
      [JSON.stringify({ v: 1, id: String(++serial), ...payload })],
    ),
  );
  assert.equal(response.ok, true, response.error?.message);
  return response.result;
};
// Malformed replacement must fail atomically, leaving the validated model usable.
assert.notEqual(install(bytes.subarray(0, 55)), "");
assert.equal(request({ method: "about" }).evaluator, "line11-nnue-v1");
const fixture = JSON.parse(
  await readFile(resolve(root, "tests/fixtures/winning-move.json"), "utf8"),
);
const tactical = request({
  method: "analyze",
  evaluator: "nnue",
  position: fixture.position,
  limits: { timeMs: 2000, maxDepth: 2 },
});
assert.deepEqual(tactical.bestMove, fixture.bestMove);
assert.equal(tactical.evaluator, "line11-nnue-v1");
const unsupported = JSON.parse(
  engine.ccall(
    "gomoku_request",
    "string",
    ["string"],
    [
      JSON.stringify({
        v: 1,
        id: "unsupported",
        method: "analyze",
        evaluator: "nnue",
        position: { size: 20, rule: "standard", moves: [] },
        limits: { timeMs: 0 },
      }),
    ],
  ),
);
assert.equal(unsupported.ok, false, "Explicit NNUE must reject incompatible rules/size");
let position = { size: 15, rule: "freestyle", moves: [] };
let status = "playing",
  nodes = 0,
  milliseconds = 0;
for (let ply = 0; ply < 225 && status === "playing"; ++ply) {
  const result = request({
    method: "analyze",
    evaluator: "nnue",
    position,
    limits: { timeMs: 100, maxDepth: 2, maxNodes: 64 },
  });
  assert.equal(result.evaluator, "line11-nnue-v1");
  assert.ok(result.bestMove);
  nodes += result.nodes;
  milliseconds += result.elapsedMs;
  const next = request({ method: "play", position, move: result.bestMove });
  assert.equal(next.moves.length, position.moves.length + 1);
  status = next.status;
  position = { size: next.size, rule: next.rule, moves: next.moves };
}
assert.notEqual(status, "playing", "A full NNUE vs NNUE game must terminate");
const baseline = request({
  method: "analyze",
  evaluator: "handcrafted",
  position: { size: 15, rule: "freestyle", moves: [] },
  limits: { timeMs: 0 },
});
assert.equal(baseline.evaluator, "handcrafted-v1");
const report = {
  model: pin.id,
  sha256: pin.sha256,
  native,
  wasm: {
    vectors: reference.vectors.length,
    valueLogits: values,
    policyLogits: policies,
    maxAbsoluteError: 0,
  },
  selfPlay: { status, plies: position.moves.length, nodes, searchMilliseconds: milliseconds },
  tacticalWin: true,
  invalidReplacementPreservesModel: true,
  incompatibleModelRejected: true,
};
await mkdir(resolve(root, "artifacts/runtime"), { recursive: true });
await writeFile(
  resolve(root, `artifacts/runtime/${pin.id}.json`),
  JSON.stringify(report, null, 2) + "\n",
);
console.log(JSON.stringify(report, null, 2));
