import { randomBytes, randomInt } from "node:crypto";
import {
  OnlineClientMessageSchema,
  ROOM_CODE_ALPHABET,
  type BoardSize,
  type Color,
  type Engine,
  type Move,
  type OnlineClientMessage,
  type OnlineRoom,
  type OnlineServerMessage,
  type Rule,
} from "@gomoku/contracts";

export interface Connection {
  send(message: OnlineServerMessage): void;
  /** Drops a superseded connection after the same player connects elsewhere. */
  close(): void;
}

type Player = {
  token: string;
  name: string;
  connection: Connection | null;
  room: Room | null;
  queue: string | null;
  seen: number;
};
type Room = {
  code: string;
  rule: Rule;
  size: BoardSize;
  black: Player | null;
  white: Player | null;
  moves: Move[];
  status: OnlineRoom["status"];
  ending: OnlineRoom["ending"];
  undo: { color: Color; keep: number } | null;
  touched: number;
  /** Serializes referee calls so concurrent messages see one move order. */
  busy: Promise<void>;
};
export type OnlineLimits = { rooms: number; idleMs: number };

const other = (color: Color): Color => (color === "black" ? "white" : "black");
const seat = (room: Room, player: Player): Color | null =>
  room.black === player ? "black" : room.white === player ? "white" : null;
const started = (room: Room) => room.black !== null && room.white !== null;

/**
 * Rooms, random matching, resignation and consensual undo for remote players.
 * State lives in memory; every move is validated by the shared C++ rules (WASM).
 * A player is identified by a reconnect token, not by a socket, so a refresh
 * resumes the same seat.
 */
export class OnlineService {
  private players = new Map<string, Player>();
  private rooms = new Map<string, Room>();
  private queues = new Map<string, Player[]>();

  constructor(
    private readonly referee: Engine,
    private readonly limits: OnlineLimits = { rooms: 2000, idleMs: 30 * 60_000 },
    private readonly now = () => Date.now(),
  ) {}

  /** Returns the handler for one socket; call `closed` when the socket goes away. */
  connect(connection: Connection) {
    let player: Player | null = null;
    return {
      receive: async (raw: unknown) => {
        const parsed = OnlineClientMessageSchema.safeParse(raw);
        if (!parsed.success) return connection.send({ type: "error", message: "消息格式错误" });
        const message = parsed.data;
        if (message.type === "hello") {
          player = this.hello(connection, message);
          return;
        }
        if (!player) return connection.send({ type: "error", message: "请先连接" });
        player.seen = this.now();
        try {
          await this.handle(player, message);
        } catch (error) {
          connection.send({
            type: "error",
            message: error instanceof Error ? error.message : "操作失败",
          });
        }
      },
      closed: () => {
        if (!player || player.connection !== connection) return;
        player.connection = null;
        player.seen = this.now();
        this.dequeue(player);
        if (player.room) this.broadcast(player.room);
      },
    };
  }

  private hello(connection: Connection, message: Extract<OnlineClientMessage, { type: "hello" }>) {
    let player = message.token ? this.players.get(message.token) : undefined;
    if (player?.connection && player.connection !== connection) player.connection.close();
    if (!player) {
      player = {
        token: randomBytes(18).toString("base64url"),
        name: "",
        connection,
        room: null,
        queue: null,
        seen: 0,
      };
      this.players.set(player.token, player);
    }
    Object.assign(player, { name: message.name, connection, seen: this.now() });
    connection.send({ type: "welcome", token: player.token });
    if (player.room) this.broadcast(player.room);
    else this.status(player);
    return player;
  }

  private async handle(player: Player, message: Exclude<OnlineClientMessage, { type: "hello" }>) {
    switch (message.type) {
      case "create": {
        this.release(player);
        const room = this.open(message.rule, message.size);
        room.black = player;
        player.room = room;
        return this.broadcast(room);
      }
      case "join": {
        const room = this.rooms.get(message.code);
        if (!room) throw new Error("房间不存在或已关闭");
        if (seat(room, player)) return this.broadcast(room);
        if (started(room)) throw new Error("房间已满");
        this.release(player);
        room.white = player;
        player.room = room;
        return this.begin(room);
      }
      case "match": {
        this.release(player);
        const key = message.rule + ":" + message.size;
        const queue = this.queues.get(key) ?? [];
        const opponent = queue.shift();
        if (!opponent) {
          queue.push(player);
          this.queues.set(key, queue);
          player.queue = key;
          return this.status(player);
        }
        opponent.queue = null;
        const room = this.open(message.rule, message.size);
        Object.assign(room, { black: opponent, white: player });
        opponent.room = player.room = room;
        return this.begin(room);
      }
      case "leave": {
        this.release(player);
        return this.status(player);
      }
      case "resign": {
        const { room, color } = this.playing(player);
        this.finish(room, other(color), "resign");
        return this.broadcast(room);
      }
      case "undo": {
        const { room, color } = this.playing(player);
        if (room.undo) throw new Error("已有悔棋请求");
        // Take back one's own last move and, if the opponent has replied, that reply too.
        const own = room.moves.length % 2 === (color === "black" ? 1 : 0) ? 1 : 2;
        if (room.moves.length < own) throw new Error("没有可以悔的棋");
        room.undo = { color, keep: room.moves.length - own };
        return this.broadcast(room);
      }
      case "undoReply": {
        const { room, color } = this.playing(player);
        if (!room.undo || room.undo.color === color) throw new Error("没有待回应的悔棋请求");
        if (message.accept) room.moves = room.moves.slice(0, room.undo.keep);
        room.undo = null;
        return this.broadcast(room);
      }
      case "move":
        return this.move(player, message.move);
    }
  }

  private async move(player: Player, move: Move) {
    const { room, color } = this.playing(player);
    const turn = room.moves.length % 2 === 0 ? "black" : "white";
    if (color !== turn) throw new Error("还没轮到你");
    const previous = room.busy;
    let done!: () => void;
    room.busy = new Promise((resolve) => (done = resolve));
    try {
      await previous;
      if (room.status !== "playing" || room.moves.length % 2 !== (color === "black" ? 0 : 1))
        throw new Error("对局状态已变化");
      const taken = room.moves.some((m) => m.x === move.x && m.y === move.y);
      if (taken || move.x >= room.size || move.y >= room.size) throw new Error("这里不能落子");
      // Any referee rejection is an illegal move; its English text never reaches players.
      const result = await this.referee
        .request({
          method: "play",
          position: { size: room.size, rule: room.rule, moves: room.moves },
          move,
        })
        .catch(() => {
          throw new Error("这里不能落子");
        });
      if (result.kind !== "position") throw new Error("裁判返回了无效结果");
      room.moves = result.moves;
      // A move answers any pending undo request: the request no longer applies.
      room.undo = null;
      if (result.status !== "playing")
        this.finish(
          room,
          result.status === "draw" ? null : result.status === "black_win" ? "black" : "white",
          result.status === "draw" ? "draw" : "five",
        );
      this.broadcast(room);
    } finally {
      done();
    }
  }

  private playing(player: Player) {
    const room = player.room;
    const color = room && seat(room, player);
    if (!room || !color || !started(room)) throw new Error("当前没有进行中的对局");
    if (room.status !== "playing") throw new Error("对局已结束");
    room.touched = this.now();
    return { room, color };
  }

  private open(rule: Rule, size: BoardSize): Room {
    this.sweep();
    if (this.rooms.size >= this.limits.rooms) throw new Error("服务器房间已满，请稍后再试");
    let code = "";
    do
      code = Array.from(
        { length: 6 },
        () => ROOM_CODE_ALPHABET[randomInt(ROOM_CODE_ALPHABET.length)],
      ).join("");
    while (this.rooms.has(code));
    const room: Room = {
      code,
      rule,
      size,
      black: null,
      white: null,
      moves: [],
      status: "playing",
      ending: null,
      undo: null,
      touched: this.now(),
      busy: Promise.resolve(),
    };
    this.rooms.set(code, room);
    return room;
  }

  /** Colors are drawn when the second player arrives, so neither side picks black. */
  private begin(room: Room) {
    if (randomInt(2)) [room.black, room.white] = [room.white, room.black];
    room.touched = this.now();
    this.broadcast(room);
  }

  private finish(room: Room, winner: Color | null, ending: NonNullable<Room["ending"]>) {
    room.status = winner === null ? "draw" : winner === "black" ? "black_win" : "white_win";
    room.ending = ending;
    room.undo = null;
    room.touched = this.now();
  }

  /** Leaves the queue or the current room; leaving a game in progress resigns it. */
  private release(player: Player) {
    this.dequeue(player);
    const room = player.room;
    if (!room) return;
    const color = seat(room, player);
    player.room = null;
    if (!color) return;
    if (started(room) && room.status === "playing") this.finish(room, other(color), "resign");
    room[color] = null;
    if (!room.black && !room.white) this.rooms.delete(room.code);
    else this.broadcast(room);
  }

  private dequeue(player: Player) {
    if (!player.queue) return;
    const queue = this.queues.get(player.queue) ?? [];
    this.queues.set(
      player.queue,
      queue.filter((p) => p !== player),
    );
    player.queue = null;
  }

  private status(player: Player) {
    if (!player.connection) return;
    if (player.queue) {
      const [rule, size] = player.queue.split(":");
      player.connection.send({
        type: "queued",
        rule: rule as Rule,
        size: Number(size) as BoardSize,
      });
    } else player.connection.send({ type: "lobby" });
  }

  private broadcast(room: Room) {
    for (const color of ["black", "white"] as const) {
      const player = room[color];
      player?.connection?.send({ type: "room", room: this.view(room, color) });
    }
  }

  private view(room: Room, you: Color): OnlineRoom {
    const describe = (player: Player | null) =>
      player && { name: player.name, connected: player.connection !== null };
    return {
      code: room.code,
      rule: room.rule,
      size: room.size,
      you,
      players: { black: describe(room.black), white: describe(room.white) },
      moves: room.moves,
      toMove: room.moves.length % 2 === 0 ? "black" : "white",
      status: room.status,
      ending: room.ending,
      undo: room.undo?.color ?? null,
    };
  }

  /** Forgets rooms and players nobody has touched for the idle limit while offline. */
  sweep() {
    const cutoff = this.now() - this.limits.idleMs;
    for (const room of this.rooms.values()) {
      const online = [room.black, room.white].some((p) => p?.connection);
      if (!online && room.touched < cutoff) {
        for (const player of [room.black, room.white]) if (player) player.room = null;
        this.rooms.delete(room.code);
      }
    }
    for (const player of this.players.values())
      if (!player.connection && !player.room && player.seen < cutoff)
        this.players.delete(player.token);
  }

  get size() {
    return { rooms: this.rooms.size, players: this.players.size };
  }
}
