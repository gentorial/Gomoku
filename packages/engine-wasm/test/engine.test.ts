import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import type { Position, WorkerRequest } from "@gomoku/contracts";
import { createTestEngine, loadEngine } from "./engine.js";

const fixture = JSON.parse(
  await readFile(new URL("../../../tests/fixtures/winning-move.json", import.meta.url), "utf8"),
);
const empty: Position = { size: 15, rule: "freestyle", moves: [] };

test("WASM shares the native adapter's position and winning-move semantics", async () => {
  const engine = await createTestEngine();
  const analysis = await engine.request({
    method: "analyze",
    position: fixture.position,
    limits: { timeMs: 1000, maxDepth: 2 },
  });
  assert.equal(analysis.kind, "analysis");
  if (analysis.kind !== "analysis") return;
  assert.deepEqual(analysis.bestMove, fixture.bestMove);
  const command: WorkerRequest = {
    v: 1,
    id: "fixture",
    method: "play",
    position: fixture.position,
    move: fixture.bestMove,
  };
  const wasm = await engine.request({
    method: "play",
    position: fixture.position,
    move: fixture.bestMove,
  });
  const binary = fileURLToPath(
    new URL(
      "../../../build/dev/bin/gomoku-worker" + (process.platform === "win32" ? ".exe" : ""),
      import.meta.url,
    ),
  );
  const native = JSON.parse(
    execFileSync(binary, [], {
      input: JSON.stringify(command) + "\n",
      encoding: "utf8",
      timeout: 5000,
      windowsHide: true,
    }),
  );
  assert.deepEqual(wasm, native.result);
  assert.equal(wasm.kind === "position" && wasm.status, "black_win");
  if (wasm.kind !== "position") return;
  const terminal = await engine.request({
    method: "analyze",
    position: { size: wasm.size, rule: wasm.rule, moves: wasm.moves },
    limits: { timeMs: 0 },
  });
  assert.equal(terminal.kind === "analysis" && terminal.bestMove, null);
});

test("WASM catches malformed requests and remains usable after an exception", async () => {
  const module = await loadEngine();
  const call = (request: unknown) =>
    JSON.parse(module.ccall("gomoku_request", "string", ["string"], [JSON.stringify(request)]));
  for (const request of [
    { method: "inspect", position: { ...empty, size: 16 } },
    { method: "play", position: empty, move: { x: 1.5, y: 2 } },
    {
      method: "inspect",
      position: {
        ...empty,
        moves: [
          { x: 2, y: 2 },
          { x: 2, y: 2 },
        ],
      },
    },
    { method: "analyze", position: empty, limits: { timeMs: -1 } },
  ])
    assert.equal(call({ ...request, v: 1, id: "bad" }).ok, false);
  assert.equal(
    call({ v: 1, id: "next", method: "inspect", position: empty }).result.toMove,
    "black",
  );
});

test("zero budget and node interruption return legal fallback moves without corrupting WASM state", async () => {
  const engine = await createTestEngine();
  for (const limits of [{ timeMs: 0 }, { timeMs: 1000, maxDepth: 8, maxNodes: 10 }]) {
    const result = await engine.request({ method: "analyze", position: empty, limits });
    assert.equal(result.kind, "analysis");
    if (result.kind !== "analysis") return;
    assert.equal(result.reason, "limit");
    assert.ok(result.bestMove);
    const next = await engine.request({ method: "play", position: empty, move: result.bestMove });
    assert.equal(next.kind === "position" && next.moves.length, 1);
  }
});

test("WASM distinguishes a standard overline from a freestyle win on a 20 board", async () => {
  const engine = await createTestEngine();
  const moves = [0, 1, 2, 4, 5, 3].flatMap((x, i) =>
    i < 5
      ? [
          { x, y: 9 },
          { x: i * 2, y: 0 },
        ]
      : [{ x, y: 9 }],
  );
  for (const rule of ["freestyle", "standard"] as const) {
    const result = await engine.request({ method: "inspect", position: { size: 20, rule, moves } });
    assert.equal(
      result.kind === "position" && result.status,
      rule === "standard" ? "playing" : "black_win",
    );
  }
});
