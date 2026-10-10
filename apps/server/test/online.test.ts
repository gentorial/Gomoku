import assert from "node:assert/strict";
import { test } from "node:test";
import {
  OnlineServerMessageSchema,
  type OnlineServerMessage,
  type OnlineRoom,
} from "@gomoku/contracts";
import { createApp } from "../src/app.js";
import { OnlineService, type Connection } from "../src/online.js";
import { createReferee } from "../src/referee.js";

const referee = await createReferee();

/** A fake socket that records what the service sends to one player. */
function player(service: OnlineService, name: string, token?: string) {
  const inbox: OnlineServerMessage[] = [];
  let closed = false;
  const connection: Connection = {
    send: (message) => inbox.push(OnlineServerMessageSchema.parse(message)),
    close: () => (closed = true),
  };
  const session = service.connect(connection);
  const self = {
    inbox,
    session,
    get closed() {
      return closed;
    },
    send: (message: object) => session.receive(message),
    last: () => inbox.at(-1)!,
    room: (): OnlineRoom => {
      const message = [...inbox].reverse().find((m) => m.type === "room");
      assert.ok(message && message.type === "room", "expected a room state");
      return message.room;
    },
    error: () => {
      const message = [...inbox].reverse().find((m) => m.type === "error");
      return message?.type === "error" ? message.message : null;
    },
    token: "",
  };
  return {
    ...self,
    ready: self.send({ type: "hello", name, token }).then(() => {
      const welcome = inbox.find((m) => m.type === "welcome");
      assert.ok(welcome?.type === "welcome");
      return Object.assign(self, { token: welcome.token });
    }),
  };
}
const join = async (service: OnlineService, rule: "freestyle" | "standard" = "freestyle") => {
  const a = await player(service, "甲").ready;
  const b = await player(service, "乙").ready;
  await a.send({ type: "create", rule, size: 15 });
  const code = a.room().code;
  await b.send({ type: "join", code });
  const black = a.room().you === "black" ? a : b;
  const white = black === a ? b : a;
  return { a, b, black, white, code };
};

test("room codes seat two players, enforce turns and reject illegal moves", async () => {
  const service = new OnlineService(referee);
  const a = await player(service, "甲").ready;
  assert.equal(a.last().type, "lobby");
  await a.send({ type: "create", rule: "freestyle", size: 15 });
  const waiting = a.room();
  assert.match(waiting.code, /^[A-Z2-9]{6}$/);
  assert.equal(waiting.players.white, null);
  const b = await player(service, "乙").ready;
  await b.send({ type: "join", code: "AAAAAA" });
  assert.equal(b.error(), "房间不存在或已关闭");
  await b.send({ type: "join", code: waiting.code });
  const black = a.room().you === "black" ? a : b;
  const white = black === a ? b : a;
  assert.deepEqual(
    [black.room().players.black?.name, white.room().you],
    [black === a ? "甲" : "乙", "white"],
  );
  await white.send({ type: "move", move: { x: 7, y: 7 } });
  assert.equal(white.error(), "还没轮到你");
  await black.send({ type: "move", move: { x: 7, y: 7 } });
  await white.send({ type: "move", move: { x: 7, y: 7 } });
  assert.equal(white.error(), "这里不能落子");
  assert.deepEqual(white.room().moves, [{ x: 7, y: 7 }]);
  const third = await player(service, "丙").ready;
  await third.send({ type: "join", code: waiting.code });
  assert.equal(third.error(), "房间已满");
});

test("five in a row ends the game; resignation and leaving award the opponent", async () => {
  const service = new OnlineService(referee);
  const { black, white } = await join(service);
  for (let i = 0; i < 4; ++i) {
    await black.send({ type: "move", move: { x: i, y: 0 } });
    await white.send({ type: "move", move: { x: i, y: 5 } });
  }
  await black.send({ type: "move", move: { x: 4, y: 0 } });
  assert.deepEqual([white.room().status, white.room().ending], ["black_win", "five"]);
  await white.send({ type: "move", move: { x: 4, y: 5 } });
  assert.equal(white.error(), "对局已结束");

  const second = await join(service);
  await second.black.send({ type: "move", move: { x: 7, y: 7 } });
  await second.black.send({ type: "resign" });
  assert.deepEqual(
    [second.white.room().status, second.white.room().ending],
    ["white_win", "resign"],
  );

  const third = await join(service);
  await third.white.send({ type: "leave" });
  assert.equal(third.white.last().type, "lobby");
  assert.deepEqual(
    [third.black.room().status, third.black.room().players.white],
    ["black_win", null],
  );
});

test("undo needs the opponent's consent and takes back the requester's own move", async () => {
  const service = new OnlineService(referee);
  const { black, white } = await join(service);
  await black.send({ type: "undo" });
  assert.equal(black.error(), "没有可以悔的棋");
  await black.send({ type: "move", move: { x: 7, y: 7 } });
  await white.send({ type: "move", move: { x: 8, y: 8 } });
  await black.send({ type: "undo" });
  assert.equal(white.room().undo, "black");
  await black.send({ type: "undoReply", accept: true });
  assert.equal(black.error(), "没有待回应的悔棋请求");
  await white.send({ type: "undoReply", accept: false });
  assert.deepEqual([black.room().undo, black.room().moves.length], [null, 2]);
  // Black asks again after White replied: both moves go back, Black is to move.
  await black.send({ type: "undo" });
  await white.send({ type: "undoReply", accept: true });
  assert.deepEqual([white.room().moves, white.room().toMove], [[], "black"]);
  // A request is dropped by the next move.
  await black.send({ type: "move", move: { x: 7, y: 7 } });
  await black.send({ type: "undo" });
  await white.send({ type: "move", move: { x: 6, y: 6 } });
  assert.equal(black.room().undo, null);
});

test("random matching pairs equal settings and leaving the queue cancels", async () => {
  const service = new OnlineService(referee);
  const a = await player(service, "甲").ready;
  const b = await player(service, "乙").ready;
  const c = await player(service, "丙").ready;
  await a.send({ type: "match", rule: "freestyle", size: 15 });
  assert.equal(a.last().type, "queued");
  await b.send({ type: "match", rule: "standard", size: 15 });
  assert.equal(b.last().type, "queued");
  await c.send({ type: "match", rule: "freestyle", size: 15 });
  assert.equal(a.room().code, c.room().code);
  assert.notEqual(a.room().you, c.room().you);
  await b.send({ type: "leave" });
  assert.equal(b.last().type, "lobby");
  const d = await player(service, "丁").ready;
  await d.send({ type: "match", rule: "standard", size: 15 });
  assert.equal(d.last().type, "queued");
});

test("a reconnect token resumes the seat; idle rooms are swept", async () => {
  let clock = 0;
  const service = new OnlineService(referee, { rooms: 10, idleMs: 1000 }, () => clock);
  const { black, white } = await join(service);
  await black.send({ type: "move", move: { x: 7, y: 7 } });
  black.session.closed();
  assert.equal(white.room().players[black.room().you]?.connected, false);
  const back = await player(service, "改名", black.token).ready;
  assert.deepEqual(back.room().moves, [{ x: 7, y: 7 }]);
  assert.equal(white.room().players.black?.name, "改名");
  // A second tab with the same token takes over; the first one is closed.
  const tab = await player(service, "改名", black.token).ready;
  assert.ok(back.closed && tab.room().you === "black");
  for (const p of [tab, white]) p.session.closed();
  clock = 5000;
  service.sweep();
  assert.deepEqual(service.size, { rooms: 0, players: 0 });
  // A full server refuses new rooms instead of growing without bound.
  const busy = new OnlineService(referee, { rooms: 1, idleMs: 1000 });
  const one = await player(busy, "甲").ready;
  const two = await player(busy, "乙").ready;
  await one.send({ type: "create", rule: "freestyle", size: 15 });
  await two.send({ type: "create", rule: "freestyle", size: 20 });
  assert.equal(two.error(), "服务器房间已满，请稍后再试");
});

test("WebSocket route: allowed origins only, and a full round trip", async (t) => {
  const app = createApp(null, false, {
    online: new OnlineService(referee),
    origins: ["https://example.test"],
  });
  t.after(() => app.close());
  await app.ready();
  const health = await app.inject({ method: "GET", url: "/api/health" });
  assert.deepEqual(health.json(), { status: "ok", online: { rooms: 0, players: 0 } });
  const rejected = await app.injectWS("/ws", { headers: { origin: "https://evil.test" } });
  const code = await new Promise<number>((resolve) => rejected.on("close", resolve));
  assert.equal(code, 1008);
  const socket = await app.injectWS("/ws", { headers: { origin: "https://example.test" } });
  const messages: OnlineServerMessage[] = [];
  const arrived = (count: number) =>
    new Promise<void>((resolve) => {
      const check = () => (messages.length >= count ? resolve() : setTimeout(check, 5));
      check();
    });
  socket.on("message", (data) =>
    messages.push(OnlineServerMessageSchema.parse(JSON.parse(String(data)))),
  );
  socket.send(JSON.stringify({ type: "hello", name: "网" }));
  await arrived(2);
  socket.send("not json");
  socket.send(JSON.stringify({ type: "create", rule: "freestyle", size: 20 }));
  await arrived(4);
  assert.deepEqual(
    messages.map((m) => m.type),
    ["welcome", "lobby", "error", "room"],
  );
  socket.terminate();
});
