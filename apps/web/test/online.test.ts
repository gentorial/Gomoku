import assert from "node:assert/strict";
import { test } from "node:test";
import { OnlineController, wasOnline } from "../src/online/controller.js";

class FakeSocket {
  readyState = 0;
  sent: unknown[] = [];
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  close() {
    this.readyState = 3;
  }
  opened() {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }
  receive(message: object) {
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent);
  }
  dropped(code = 1006) {
    this.readyState = 3;
    this.onclose?.({ code } as CloseEvent);
  }
}
function memory() {
  const values = new Map<string, string>();
  return {
    values,
    get: (key: string) => values.get(key) ?? null,
    set: (key: string, value: string | null) =>
      value === null ? values.delete(key) : values.set(key, value),
  };
}
const room = {
  code: "ABC234",
  rule: "freestyle",
  size: 15,
  you: "black",
  players: { black: { name: "甲", connected: true }, white: null },
  moves: [],
  toMove: "black",
  status: "playing",
  ending: null,
  undo: null,
};

test("requests wait for the welcome, and the token and room survive a reconnect", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const storage = memory();
  const sockets: FakeSocket[] = [];
  const online = new OnlineController("wss://example.test/ws", "甲", storage, () => {
    const socket = new FakeSocket();
    sockets.push(socket);
    return socket;
  });
  t.after(() => online.dispose());
  online.create("freestyle", 15);
  const first = sockets[0]!;
  first.opened();
  assert.deepEqual(first.sent, [{ type: "hello", name: "甲" }]);
  first.receive({ type: "welcome", token: "t1" });
  assert.deepEqual(first.sent.at(-1), { type: "create", rule: "freestyle", size: 15 });
  first.receive({ type: "room", room });
  assert.equal(online.getSnapshot().phase, "room");
  assert.ok(wasOnline(storage));

  first.dropped();
  assert.equal(online.getSnapshot().connection, "reconnecting");
  online.move({ x: 7, y: 7 });
  t.mock.timers.tick(1000);
  const second = sockets[1]!;
  second.opened();
  assert.deepEqual(second.sent, [{ type: "hello", name: "甲", token: "t1" }]);
  second.receive({ type: "welcome", token: "t1" });
  assert.deepEqual(second.sent.at(-1), { type: "move", move: { x: 7, y: 7 } });

  // Another tab took the seat: show it and stop reconnecting.
  second.dropped(4000);
  t.mock.timers.tick(60_000);
  assert.equal(sockets.length, 2);
  assert.equal(online.getSnapshot().error, "已在其他页面连接");
});

test("leaving returns to the lobby and forgets the open room", (t) => {
  const storage = memory();
  const socket = new FakeSocket();
  const online = new OnlineController("wss://example.test/ws", "乙", storage, () => socket);
  t.after(() => online.dispose());
  socket.opened();
  socket.receive({ type: "welcome", token: "t2" });
  socket.receive({ type: "queued", rule: "standard", size: 20 });
  assert.deepEqual(online.getSnapshot().queued, { rule: "standard", size: 20 });
  online.leave();
  socket.receive({ type: "lobby" });
  assert.equal(online.getSnapshot().phase, "lobby");
  assert.equal(wasOnline(storage), false);
  assert.equal(storage.get("gomoku-online-name"), "乙");
  socket.receive({ type: "error", message: "房间已满" });
  assert.equal(online.getSnapshot().error, "房间已满");
});
