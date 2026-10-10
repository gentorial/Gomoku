import {
  OnlineServerMessageSchema,
  type BoardSize,
  type Move,
  type OnlineClientMessage,
  type OnlineRoom,
  type Rule,
} from "@gomoku/contracts";

export type OnlineSnapshot = {
  connection: "connecting" | "online" | "reconnecting";
  phase: "connecting" | "lobby" | "queued" | "room";
  room: OnlineRoom | null;
  queued: { rule: Rule; size: BoardSize } | null;
  error: string | null;
};
type Socket = Pick<WebSocket, "send" | "close" | "readyState"> & {
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
};
type Storage = { get(key: string): string | null; set(key: string, value: string | null): void };

const TOKEN = "gomoku-online-token";
const ACTIVE = "gomoku-online-active";
const NAME = "gomoku-online-name";
/** Browser storage that degrades to memory (private mode, blocked site data). */
export const localStore: Storage = {
  get(key) {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key, value) {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch {
      /* Without storage a refresh simply starts a new session. */
    }
  },
};
export const savedName = (storage: Storage = localStore) => storage.get(NAME) ?? "";
/** Whether this browser left a room or queue open, so a reload should reconnect to it. */
export const wasOnline = (storage: Storage = localStore) =>
  storage.get(ACTIVE) === "1" && storage.get(TOKEN) !== null;

/**
 * One online session over a WebSocket. The server owns the game; this mirrors its
 * latest state for React and reconnects with the stored token after drops, so a
 * refresh or a network blip returns to the same seat.
 */
export class OnlineController {
  private state: OnlineSnapshot = {
    connection: "connecting",
    phase: "connecting",
    room: null,
    queued: null,
    error: null,
  };
  private listeners = new Set<() => void>();
  private socket: Socket | null = null;
  private welcomed = false;
  private pending: OnlineClientMessage[] = [];
  private retry: ReturnType<typeof setTimeout> | null = null;
  private delay = 1000;
  private disposed = false;

  constructor(
    private readonly url: string,
    private readonly name: string,
    private readonly storage: Storage = localStore,
    private readonly open: (url: string) => Socket = (url) => new WebSocket(url),
  ) {
    storage.set(NAME, name);
    this.connect();
  }

  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private update(patch: Partial<OnlineSnapshot>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((listener) => listener());
  }

  private connect() {
    const socket = this.open(this.url);
    this.socket = socket;
    this.welcomed = false;
    socket.onopen = () => {
      const token = this.storage.get(TOKEN);
      socket.send(JSON.stringify({ type: "hello", name: this.name, ...(token ? { token } : {}) }));
    };
    socket.onmessage = (event) => this.receive(event.data);
    socket.onclose = (event) => {
      if (this.socket !== socket || this.disposed) return;
      this.socket = null;
      // 4000: the same player connected from another tab; do not fight over the seat.
      if (event.code === 4000) {
        this.update({ error: "已在其他页面连接", connection: "reconnecting" });
        return;
      }
      this.update({ connection: "reconnecting" });
      this.retry = setTimeout(() => {
        this.retry = null;
        this.connect();
      }, this.delay);
      this.delay = Math.min(this.delay * 2, 30_000);
    };
  }

  private receive(data: unknown) {
    let parsed;
    try {
      parsed = OnlineServerMessageSchema.safeParse(JSON.parse(String(data)));
    } catch {
      return;
    }
    if (!parsed.success) return;
    const message = parsed.data;
    switch (message.type) {
      case "welcome":
        this.storage.set(TOKEN, message.token);
        this.welcomed = true;
        this.delay = 1000;
        this.update({ connection: "online" });
        for (const item of this.pending.splice(0)) this.socket?.send(JSON.stringify(item));
        return;
      case "lobby":
        this.storage.set(ACTIVE, null);
        return this.update({ phase: "lobby", room: null, queued: null });
      case "queued":
        this.storage.set(ACTIVE, "1");
        return this.update({
          phase: "queued",
          room: null,
          queued: { rule: message.rule, size: message.size },
          error: null,
        });
      case "room":
        this.storage.set(ACTIVE, "1");
        return this.update({ phase: "room", room: message.room, queued: null, error: null });
      case "error":
        return this.update({ error: message.message });
    }
  }

  private send(message: OnlineClientMessage) {
    if (this.disposed) return;
    this.update({ error: null });
    // Requests made while (re)connecting are sent once the server knows who we are.
    if (this.socket && this.welcomed && this.socket.readyState === 1)
      this.socket.send(JSON.stringify(message));
    else this.pending.push(message);
  }

  create(rule: Rule, size: BoardSize) {
    this.send({ type: "create", rule, size });
  }
  join(code: string) {
    this.send({ type: "join", code: code.trim().toUpperCase() });
  }
  match(rule: Rule, size: BoardSize) {
    this.send({ type: "match", rule, size });
  }
  leave() {
    this.send({ type: "leave" });
  }
  move(move: Move) {
    this.send({ type: "move", move });
  }
  resign() {
    this.send({ type: "resign" });
  }
  undo() {
    this.send({ type: "undo" });
  }
  reply(accept: boolean) {
    this.send({ type: "undoReply", accept });
  }

  dispose() {
    this.disposed = true;
    if (this.retry !== null) clearTimeout(this.retry);
    this.socket?.close();
    this.socket = null;
    this.listeners.clear();
  }
}
