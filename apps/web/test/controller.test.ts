import assert from "node:assert/strict";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import {
  MatchConfigSchema,
  type Engine,
  type Move,
  type WorkerPayload,
  type WorkerResult,
} from "@gomoku/contracts";
import { createTestEngine } from "../../../packages/engine-wasm/test/engine.js";
import { MatchController } from "../src/game/controller.js";

// Search results are scripted to make turn/cancellation tests deterministic;
// actual WASM adjudicates every position, move, undo and terminal state.
const winningMoves: Move[] = [
  { x: 3, y: 7 },
  { x: 2, y: 7 },
  { x: 4, y: 7 },
  { x: 0, y: 0 },
  { x: 5, y: 7 },
  { x: 2, y: 0 },
  { x: 6, y: 7 },
  { x: 4, y: 0 },
  { x: 7, y: 7 },
];
class ScriptedEngine implements Engine {
  analyses = 0;
  gate: Promise<void> | null = null;
  entered = false;
  async request(payload: WorkerPayload, signal?: AbortSignal): Promise<WorkerResult> {
    if (payload.method !== "analyze") return this.native.request(payload, signal);
    ++this.analyses;
    this.entered = true;
    // Intentionally ignore cancellation, to simulate a late engine response.
    if (this.gate) await this.gate;
    const bestMove = winningMoves[payload.position.moves.length]!;
    return {
      kind: "analysis",
      bestMove,
      score: { value: 0, kind: "heuristic", perspective: "side_to_move" },
      depth: 1,
      nodes: 1,
      elapsedMs: 0,
      pv: [bestMove],
      reason: "completed",
      evaluator: "test",
    };
  }
  constructor(private native: Engine) {}
  close() {
    this.native.close();
  }
}
async function waitFor(predicate: () => boolean, label: string) {
  const deadline = Date.now() + 5000;
  while (!predicate()) {
    if (Date.now() > deadline) assert.fail("Timed out: " + label);
    await delay(5);
  }
}
async function setup(
  t: { after: (fn: () => void) => void },
  mode: "human-human" | "human-ai" | "ai-ai",
  humanColor = "black",
) {
  const engine = new ScriptedEngine(await createTestEngine());
  const controller = new MatchController(engine, 5);
  t.after(() => controller.dispose());
  await controller.start(MatchConfigSchema.parse({ mode, humanColor }));
  return { engine, controller, state: controller.getSnapshot };
}

test("same-device humans alternate to a terminal win without starting AI", async (t) => {
  const { engine, controller, state } = await setup(t, "human-human");
  for (const [i, move] of winningMoves.entries()) {
    assert.equal(state().position?.toMove, i % 2 ? "white" : "black");
    await controller.play(move);
  }
  assert.equal(state().position?.status, "black_win");
  assert.equal(controller.canPlay(), false);
  await controller.play({ x: 8, y: 8 });
  assert.equal(state().position?.moves.length, 9);
  assert.equal(engine.analyses, 0);
  await controller.undo();
  assert.equal(state().position?.status, "playing");
  assert.equal(state().position?.moves.length, 8);
});

test("human black triggers exactly one AI response, undo restores the decision point", async (t) => {
  const { engine, controller, state } = await setup(t, "human-ai");
  await controller.play(winningMoves[0]!);
  await controller.play(winningMoves[2]!); // Ignore a human click in the AI turn.
  await waitFor(() => state().position?.moves.length === 2, "AI reply");
  assert.equal(state().position?.toMove, "black");
  assert.equal(engine.analyses, 1);
  assert.equal(state().analysis?.color, "white");
  await controller.undo();
  assert.equal(state().position?.moves.length, 0);
  assert.equal(state().analysis, null);
});

test("human white gets an AI opening and undo preserves that opening", async (t) => {
  const { controller, state } = await setup(t, "human-ai", "white");
  await controller.play({ x: 0, y: 0 });
  await waitFor(() => state().position?.moves.length === 1, "AI opening");
  assert.equal(controller.canUndo(), false);
  await controller.play(winningMoves[1]!);
  await waitFor(() => state().position?.moves.length === 3, "AI black reply");
  await controller.undo();
  assert.equal(state().position?.moves.length, 1);
  assert.equal(state().position?.toMove, "white");
});

test("AI versus AI stops at terminal, pauses, steps exactly one move and undoes paused", async (t) => {
  const { engine, controller, state } = await setup(t, "ai-ai");
  controller.pause();
  await controller.step();
  assert.equal(state().position?.moves.length, 1);
  assert.equal(state().paused, true);
  await delay(30);
  assert.equal(engine.analyses, 1);
  await controller.undo();
  assert.equal(state().position?.moves.length, 0);
  assert.equal(state().paused, true);
  controller.resume();
  await waitFor(() => state().position?.status === "black_win", "AI match finishes");
  const count = engine.analyses;
  await delay(30);
  assert.equal(engine.analyses, count);
  assert.equal(state().position?.moves.length, 9);
});

test("pausing an in-flight AI result prevents a late move and permits resume", async (t) => {
  const { engine, controller, state } = await setup(t, "ai-ai");
  let release!: () => void;
  engine.gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await waitFor(() => engine.entered, "search starts");
  controller.pause();
  release();
  await delay(20);
  assert.equal(state().position?.moves.length, 0);
  assert.equal(state().busy, false);
  engine.gate = null;
  await controller.step();
  assert.equal(state().position?.moves.length, 1);
});

test("new mode and undo invalidate a search even if the engine ignores cancellation", async (t) => {
  const { engine, controller, state } = await setup(t, "human-ai");
  let release!: () => void;
  engine.gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await controller.play(winningMoves[0]!);
  await waitFor(() => engine.entered, "search starts");
  await controller.undo();
  await controller.start(
    MatchConfigSchema.parse({ mode: "human-human", size: 20, rule: "standard" }),
  );
  release();
  await delay(20);
  assert.equal(state().position?.moves.length, 0);
  assert.equal(state().position?.size, 20);
  assert.equal(state().position?.rule, "standard");
  assert.equal(state().thinking, null);
  assert.equal(state().analysis, null);
  assert.equal(state().error, null);
});

test("invalid human move preserves the board and permits a legal retry", async (t) => {
  const { controller, state } = await setup(t, "human-human");
  await controller.play(winningMoves[0]!);
  await controller.play(winningMoves[0]!);
  assert.ok(state().error);
  assert.equal(state().position?.moves.length, 1);
  await controller.play(winningMoves[1]!);
  assert.equal(state().position?.moves.length, 2);
  assert.equal(state().error, null);
});
