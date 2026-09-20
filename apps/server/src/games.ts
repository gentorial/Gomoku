import { randomUUID } from "node:crypto";
import {
  CreateGameSchema,
  type Game,
  type Position,
  type PositionResult,
  type Move,
  type Analysis,
  type CreateGame,
} from "@gomoku/contracts";
import type { Engine } from "./worker-pool.js";

export class ApiProblem extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message);
  }
}
type Operation = { controller: AbortController; kind: "play" | "analyze" | "undo" };
type Entry = { game: Game; operation?: Operation };
export class GameService {
  private readonly games = new Map<string, Entry>();
  constructor(private readonly engine: Engine) {}
  async create(input: CreateGame): Promise<Game> {
    const config = CreateGameSchema.parse(input);
    if (this.games.size >= 256)
      throw new ApiProblem(503, "CAPACITY", "开发服务器的对局容量已满，请重启服务");
    const position = { size: config.size, rule: config.rule, moves: [] };
    const checked = await this.inspect(position);
    const { kind: _kind, ...state } = checked;
    const game: Game = {
      ...state,
      ...config,
      id: randomUUID(),
      version: 0,
      thinking: false,
      analysis: null,
    };
    this.games.set(game.id, { game });
    return structuredClone(game);
  }
  private entry(id: string, version?: number): Entry {
    const entry = this.games.get(id);
    if (!entry) throw new ApiProblem(404, "NOT_FOUND", "找不到这盘棋");
    if (version !== undefined && version !== entry.game.version)
      throw new ApiProblem(409, "STALE_POSITION", "棋局已经变化，请刷新后重试");
    return entry;
  }
  get(id: string): Game {
    const entry = this.entry(id);
    return structuredClone({ ...entry.game, thinking: entry.operation?.kind === "analyze" });
  }
  private snapshot(game: Game): Position {
    return { size: game.size, rule: game.rule, moves: [...game.moves] };
  }
  private begin(entry: Entry, kind: Operation["kind"]): Operation {
    if (entry.operation) throw new ApiProblem(409, "BUSY", "上一项操作尚未完成");
    const operation = { kind, controller: new AbortController() };
    entry.operation = operation;
    return operation;
  }
  private current(entry: Entry, operation: Operation) {
    if (entry.operation !== operation || operation.controller.signal.aborted)
      throw new ApiProblem(409, "CANCELLED", "这次操作已经取消");
  }
  private finish(entry: Entry, operation: Operation) {
    if (entry.operation === operation) entry.operation = undefined;
  }
  private commit(entry: Entry, position: PositionResult, analysis: Analysis | null) {
    const { kind: _kind, ...state } = position;
    entry.game = {
      ...entry.game,
      ...state,
      version: entry.game.version + 1,
      thinking: false,
      analysis,
    };
  }
  private async inspect(position: Position, signal?: AbortSignal) {
    const result = await this.engine.request({ method: "inspect", position }, signal);
    if (result.kind !== "position") throw new Error("Unexpected engine result");
    return result;
  }
  async play(id: string, version: number, move: Move): Promise<Game> {
    const entry = this.entry(id, version);
    if (entry.game.status !== "playing") throw new ApiProblem(409, "FINISHED", "对局已经结束");
    if (entry.game.toMove !== entry.game.humanColor)
      throw new ApiProblem(409, "AI_TURN", "现在是 AI 回合");
    const operation = this.begin(entry, "play");
    try {
      const result = await this.engine.request(
        {
          method: "play",
          position: this.snapshot(entry.game),
          move,
        },
        operation.controller.signal,
      );
      this.current(entry, operation);
      if (result.kind !== "position") throw new Error("Unexpected engine result");
      this.commit(entry, result, null);
    } finally {
      this.finish(entry, operation);
    }
    return this.get(id);
  }
  async analyze(id: string, version: number): Promise<Game> {
    const entry = this.entry(id, version);
    if (entry.game.status !== "playing") throw new ApiProblem(409, "FINISHED", "对局已经结束");
    if (entry.game.toMove === entry.game.humanColor)
      throw new ApiProblem(409, "HUMAN_TURN", "现在是你的回合");
    const operation = this.begin(entry, "analyze");
    const position = this.snapshot(entry.game);
    try {
      const result = await this.engine.request(
        {
          method: "analyze",
          position,
          limits: { timeMs: entry.game.thinkTimeMs, maxDepth: 4 },
        },
        operation.controller.signal,
      );
      this.current(entry, operation);
      if (result.kind !== "analysis" || !result.bestMove)
        throw new Error("Engine returned no move");
      const applied = await this.engine.request(
        {
          method: "play",
          position,
          move: result.bestMove,
        },
        operation.controller.signal,
      );
      this.current(entry, operation);
      if (applied.kind !== "position") throw new Error("Unexpected engine result");
      this.commit(entry, applied, result);
    } finally {
      this.finish(entry, operation);
    }
    return this.get(id);
  }
  private cancelAnalysis(entry: Entry) {
    if (entry.operation && entry.operation.kind !== "analyze")
      throw new ApiProblem(409, "BUSY", "落子操作尚未完成");
    entry.operation?.controller.abort();
    entry.operation = undefined;
  }
  cancel(id: string, version: number): Game {
    const entry = this.entry(id, version);
    this.cancelAnalysis(entry);
    ++entry.game.version;
    return this.get(id);
  }
  async undo(id: string, version: number): Promise<Game> {
    const entry = this.entry(id, version);
    if (entry.game.moves.length === 0) throw new ApiProblem(409, "EMPTY", "还没有可撤销的落子");
    this.cancelAnalysis(entry);
    const operation = this.begin(entry, "undo");
    const position = this.snapshot(entry.game);
    do {
      position.moves.pop();
    } while (
      position.moves.length > 0 &&
      (position.moves.length % 2 === 0 ? "black" : "white") !== entry.game.humanColor
    );
    try {
      const result = await this.inspect(position, operation.controller.signal);
      this.current(entry, operation);
      this.commit(entry, result, null);
    } finally {
      this.finish(entry, operation);
    }
    return this.get(id);
  }
  close() {
    for (const entry of this.games.values()) entry.operation?.controller.abort();
    this.engine.close();
  }
}
