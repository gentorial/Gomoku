import {
  MatchConfigSchema,
  type MatchConfig,
  type Analysis,
  type Color,
  type Engine,
  type Move,
  type Position,
  type PositionResult,
  type WorkerResult,
} from "@gomoku/contracts";
import { logDiagnostic } from "@gomoku/engine-wasm";

export type LastAnalysis = { result: Analysis; color: Color; ply: number };
export type MatchSnapshot = {
  config: MatchConfig;
  position: PositionResult | null;
  analysis: LastAnalysis | null;
  busy: boolean;
  thinking: Color | null;
  paused: boolean;
  error: string | null;
};
export const initialSnapshot: MatchSnapshot = {
  config: MatchConfigSchema.parse({}),
  position: null,
  analysis: null,
  busy: false,
  thinking: null,
  paused: false,
  error: null,
};
export function isHuman(config: MatchConfig, color: Color) {
  return (
    config.mode === "human-human" || (config.mode === "human-ai" && config.humanColor === color)
  );
}
export function positionInput(position: PositionResult): Position {
  return { size: position.size, rule: position.rule, moves: position.moves };
}
function asPosition(result: WorkerResult): PositionResult {
  if (result.kind !== "position") throw new Error("引擎未返回棋局");
  return result;
}

/** Owns turn scheduling, independent of React and of the engine transport. */
export class MatchController {
  private state: MatchSnapshot = initialSnapshot;
  private listeners = new Set<() => void>();
  private generation = 0;
  private abort: AbortController | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private disposed = false;

  constructor(
    private engine: Engine,
    private paceMs = 180,
  ) {}

  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private update(patch: Partial<MatchSnapshot>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((listener) => listener());
  }
  private invalidate() {
    if (this.abort)
      logDiagnostic("gui", "operation.cancel_requested", { generation: this.generation });
    ++this.generation;
    this.abort?.abort();
    this.abort = null;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }
  private async operate(
    action: (signal: AbortSignal) => Promise<Partial<MatchSnapshot>>,
    patch: Partial<MatchSnapshot> = {},
  ) {
    if (this.disposed) return;
    this.invalidate();
    const token = this.generation;
    const abort = (this.abort = new AbortController());
    this.update({ ...patch, busy: true, error: null });
    try {
      const result = await action(abort.signal);
      if (this.disposed || token !== this.generation) {
        logDiagnostic(
          "gui",
          "operation.result_discarded",
          { generation: token, currentGeneration: this.generation },
          "debug",
        );
        return;
      }
      this.abort = null;
      this.update({ ...result, busy: false, thinking: null });
      logDiagnostic("gui", "position.updated", {
        generation: token,
        plyCount: this.state.position?.moves.length,
        status: this.state.position?.status,
      });
      this.schedule();
    } catch (error) {
      if (this.disposed || token !== this.generation) return;
      logDiagnostic(
        "gui",
        "operation.failed",
        {
          generation: token,
          message: error instanceof Error ? error.message : String(error),
          stack: error instanceof Error ? error.stack : undefined,
        },
        "error",
      );
      this.abort = null;
      this.update({
        busy: false,
        thinking: null,
        paused: true,
        error: error instanceof Error ? error.message : "引擎运行失败，请重试",
      });
    }
  }

  start(config: MatchConfig = this.state.config) {
    const next = MatchConfigSchema.parse(config);
    logDiagnostic("gui", "game.start", { config: next });
    return this.operate(
      async (signal) => {
        const position = asPosition(
          await this.engine.request(
            { method: "inspect", position: { size: next.size, rule: next.rule, moves: [] } },
            signal,
          ),
        );
        return { position, config: next, analysis: null, paused: false };
      },
      { thinking: null },
    );
  }

  canPlay() {
    const { position, config, busy } = this.state;
    return !!position && position.status === "playing" && !busy && isHuman(config, position.toMove);
  }
  play(move: Move) {
    const position = this.state.position;
    if (!position || !this.canPlay()) return Promise.resolve();
    logDiagnostic("gui", "move.human", {
      move,
      toMove: position.toMove,
      plyCount: position.moves.length,
    });
    return this.operate(async (signal) => ({
      position: asPosition(
        await this.engine.request(
          { method: "play", position: positionInput(position), move },
          signal,
        ),
      ),
      // An old AI evaluation does not describe this newly played human move.
      analysis: null,
      paused: false,
    }));
  }

  private schedule() {
    const { position, config, busy, paused, error } = this.state;
    if (
      this.disposed ||
      !position ||
      busy ||
      paused ||
      error ||
      position.status !== "playing" ||
      isHuman(config, position.toMove) ||
      this.timer !== null
    )
      return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.runAI();
    }, this.paceMs);
    logDiagnostic(
      "gui",
      "search.scheduled",
      { toMove: position.toMove, delayMs: this.paceMs },
      "debug",
    );
  }
  private runAI() {
    const { position, config, busy } = this.state;
    if (!position || busy || position.status !== "playing" || isHuman(config, position.toMove))
      return Promise.resolve();
    logDiagnostic("gui", "search.requested", {
      toMove: position.toMove,
      plyCount: position.moves.length,
      evaluator: config.evaluator,
      timeMs: position.toMove === "black" ? config.blackTimeMs : config.whiteTimeMs,
    });
    return this.operate(
      async (signal) => {
        const result = await this.engine.request(
          {
            method: "analyze",
            position: positionInput(position),
            limits: {
              timeMs: position.toMove === "black" ? config.blackTimeMs : config.whiteTimeMs,
              maxDepth: 8,
            },
            evaluator: config.evaluator,
          },
          signal,
        );
        signal.throwIfAborted();
        if (result.kind !== "analysis" || !result.bestMove) throw new Error("引擎未返回合法落子");
        logDiagnostic("gui", "search.result", {
          bestMove: result.bestMove,
          score: result.score,
          depth: result.depth,
          nodes: result.nodes,
          elapsedMs: result.elapsedMs,
          reason: result.reason,
          evaluator: result.evaluator,
          model: result.model,
          pv: result.pv,
        });
        const next = asPosition(
          await this.engine.request(
            { method: "play", position: positionInput(position), move: result.bestMove },
            signal,
          ),
        );
        return {
          position: next,
          analysis: { result, color: position.toMove, ply: next.moves.length },
        };
      },
      { thinking: position.toMove },
    );
  }

  pause() {
    if (this.state.config.mode !== "ai-ai" || this.disposed) return;
    logDiagnostic("gui", "game.paused");
    this.invalidate();
    this.update({ paused: true, busy: false, thinking: null });
  }
  resume() {
    if (this.disposed || this.state.busy) return;
    logDiagnostic("gui", "game.resumed");
    this.update({ paused: false, error: null });
    this.schedule();
  }
  step() {
    if (this.state.config.mode !== "ai-ai" || this.state.busy) return Promise.resolve();
    logDiagnostic("gui", "game.step");
    this.pause();
    return this.runAI();
  }
  private undoLength() {
    const { position, config } = this.state;
    if (!position?.moves.length) return null;
    if (config.mode !== "human-ai") return position.moves.length - 1;
    const parity = config.humanColor === "black" ? 0 : 1;
    for (let i = position.moves.length - 1; i >= 0; --i) if (i % 2 === parity) return i;
    return null;
  }
  canUndo() {
    return this.undoLength() !== null;
  }
  undo() {
    const { position, config } = this.state;
    const length = this.undoLength();
    if (!position || length === null) return Promise.resolve();
    logDiagnostic("gui", "game.undo", { fromPly: position.moves.length, toPly: length });
    return this.operate(
      async (signal) => ({
        position: asPosition(
          await this.engine.request(
            {
              method: "inspect",
              position: { ...positionInput(position), moves: position.moves.slice(0, length) },
            },
            signal,
          ),
        ),
        analysis: null,
        paused: config.mode === "ai-ai",
      }),
      { thinking: null },
    );
  }
  dispose() {
    logDiagnostic("gui", "gui.closed");
    this.disposed = true;
    this.invalidate();
    this.listeners.clear();
    this.engine.close();
  }
}
