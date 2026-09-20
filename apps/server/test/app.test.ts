import assert from "node:assert/strict";
import { test } from "node:test";
import { GameSchema, type WorkerPayload, type WorkerResult } from "@gomoku/contracts";
import { createApp } from "../src/app.js";
import { WorkerPool, defaultWorkerPath, type Engine } from "../src/worker-pool.js";

test("HTTP: native validation, human move, real AI response, versioned undo", async (t) => {
  const app = createApp(new WorkerPool(defaultWorkerPath, 1));
  t.after(() => app.close());
  const health = await app.inject({ method: "GET", url: "/api/health" });
  assert.equal(health.statusCode, 200);
  const created = await app.inject({
    method: "POST",
    url: "/api/games",
    payload: { thinkTimeMs: 50 },
  });
  assert.equal(created.statusCode, 201);
  let game = GameSchema.parse(created.json());
  const prefix = "/api/games/" + game.id;
  const outside = await app.inject({
    method: "POST",
    url: prefix + "/moves",
    payload: { version: game.version, move: { x: 19, y: 19 } },
  });
  assert.equal(outside.statusCode, 400);
  const moved = await app.inject({
    method: "POST",
    url: prefix + "/moves",
    payload: { version: game.version, move: { x: 7, y: 7 } },
  });
  assert.equal(moved.statusCode, 200);
  game = GameSchema.parse(moved.json());
  assert.equal(game.version, 1);
  assert.equal(game.toMove, "white");
  const stale = await app.inject({
    method: "POST",
    url: prefix + "/analyze",
    payload: { version: 0 },
  });
  assert.equal(stale.statusCode, 409);
  const analyzed = await app.inject({
    method: "POST",
    url: prefix + "/analyze",
    payload: { version: game.version },
  });
  assert.equal(analyzed.statusCode, 200);
  game = GameSchema.parse(analyzed.json());
  assert.equal(game.moves.length, 2);
  assert.equal(game.analysis?.evaluator, "handcrafted-v1");
  assert.ok(game.analysis!.nodes > 0);
  const undone = await app.inject({
    method: "POST",
    url: prefix + "/undo",
    payload: { version: game.version },
  });
  assert.equal(undone.statusCode, 200);
  game = GameSchema.parse(undone.json());
  assert.equal(game.moves.length, 0);
  assert.equal(game.version, 3);
});

test("a duplicate concurrent move cannot be applied twice", async (t) => {
  const app = createApp();
  t.after(() => app.close());
  const created = await app.inject({ method: "POST", url: "/api/games", payload: {} });
  const game = GameSchema.parse(created.json());
  const responses = await Promise.all(
    [0, 1].map(() =>
      app.inject({
        method: "POST",
        url: "/api/games/" + game.id + "/moves",
        payload: { version: 0, move: { x: 7, y: 7 } },
      }),
    ),
  );
  assert.deepEqual(responses.map((response) => response.statusCode).sort(), [200, 409]);
  const state = await app.inject({ method: "GET", url: "/api/games/" + game.id });
  assert.equal(state.json().moves.length, 1);
});

test("undo invalidates even a worker that ignores cancellation", async (t) => {
  const native = new WorkerPool(defaultWorkerPath, 1);
  let started!: () => void;
  let release!: (value: WorkerResult) => void;
  const waiting = new Promise<void>((resolve) => {
    started = resolve;
  });
  const delayed = new Promise<WorkerResult>((resolve) => {
    release = resolve;
  });
  const engine: Engine = {
    request(payload: WorkerPayload, signal?: AbortSignal) {
      if (payload.method === "analyze") {
        started();
        return delayed;
      }
      return native.request(payload, signal);
    },
    close() {
      native.close();
    },
  };
  const app = createApp(engine);
  t.after(() => app.close());
  const created = await app.inject({ method: "POST", url: "/api/games", payload: {} });
  const game = GameSchema.parse(created.json());
  const prefix = "/api/games/" + game.id;
  await app.inject({
    method: "POST",
    url: prefix + "/moves",
    payload: { version: 0, move: { x: 7, y: 7 } },
  });
  const analysis = app
    .inject({
      method: "POST",
      url: prefix + "/analyze",
      payload: { version: 1 },
    })
    .then((response) => response);
  await waiting;
  const undone = await app.inject({
    method: "POST",
    url: prefix + "/undo",
    payload: { version: 1 },
  });
  assert.equal(undone.statusCode, 200);
  release({
    kind: "analysis",
    bestMove: { x: 8, y: 8 },
    score: { value: 1, kind: "heuristic", perspective: "side_to_move" },
    depth: 1,
    nodes: 1,
    elapsedMs: 1,
    pv: [{ x: 8, y: 8 }],
    reason: "completed",
    evaluator: "test",
  });
  assert.equal((await analysis).statusCode, 409);
  const state = await app.inject({ method: "GET", url: prefix });
  assert.equal(state.json().moves.length, 0);
  assert.equal(state.json().version, 2);
});

test("pool cancels active and queued work and recovers with a fresh process", async () => {
  const pool = new WorkerPool(defaultWorkerPath, 1);
  try {
    const active = new AbortController();
    const queued = new AbortController();
    const first = pool.request(
      {
        method: "analyze",
        position: { size: 20, rule: "freestyle", moves: [{ x: 10, y: 10 }] },
        limits: { timeMs: 10000, maxDepth: 12 },
      },
      active.signal,
    );
    const second = pool.request({ method: "about" }, queued.signal);
    const firstRejected = assert.rejects(first, /cancelled/);
    const secondRejected = assert.rejects(second, /cancelled/);
    queued.abort();
    active.abort();
    await Promise.all([firstRejected, secondRejected]);
    const result = await pool.request({ method: "about" });
    assert.equal(result.kind, "about");
  } finally {
    pool.close();
  }
});

test("unavailable native executable is surfaced as a service error", async (t) => {
  const app = createApp(new WorkerPool(defaultWorkerPath + "-missing", 1));
  t.after(() => app.close());
  const result = await app.inject({ method: "GET", url: "/api/health" });
  assert.equal(result.statusCode, 503);
  assert.equal(result.json().error.code, "ENGINE_UNAVAILABLE");
});

test("invalid API payloads and unimplemented rules are rejected", async (t) => {
  const app = createApp();
  t.after(() => app.close());
  for (const payload of [
    { rule: "renju" },
    { size: 16 },
    { thinkTimeMs: 999999 },
    { extra: true },
  ]) {
    const result = await app.inject({ method: "POST", url: "/api/games", payload });
    assert.equal(result.statusCode, 400);
  }
  assert.equal((await app.inject({ method: "GET", url: "/api/games/missing" })).statusCode, 404);
});

test("white human and standard 20x20 use the same native pipeline", async (t) => {
  const app = createApp();
  t.after(() => app.close());
  const created = await app.inject({
    method: "POST",
    url: "/api/games",
    payload: { size: 20, rule: "standard", humanColor: "white", thinkTimeMs: 50 },
  });
  const game = GameSchema.parse(created.json());
  const prefix = "/api/games/" + game.id;
  assert.equal(
    (
      await app.inject({
        method: "POST",
        url: prefix + "/moves",
        payload: { version: 0, move: { x: 0, y: 0 } },
      })
    ).statusCode,
    409,
  );
  const result = await app.inject({
    method: "POST",
    url: prefix + "/analyze",
    payload: { version: 0 },
  });
  assert.equal(result.statusCode, 200);
  assert.deepEqual(result.json().moves, [{ x: 10, y: 10 }]);
  assert.equal(result.json().toMove, "white");
});
