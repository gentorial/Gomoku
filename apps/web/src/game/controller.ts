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
      if (this.disposed || token !== this.generation) return;
      this.abort = null;
      this.update({ ...result, busy: false, thinking: null });
      this.schedule();
    } catch (error) {
      if (this.disposed || token !== this.generation) return;
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
  }
  private runAI() {
    const { position, config, busy } = this.state;
    if (!position || busy || position.status !== "playing" || isHuman(config, position.toMove))
      return Promise.resolve();
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
    this.invalidate();
    this.update({ paused: true, busy: false, thinking: null });
  }
  resume() {
    if (this.disposed || this.state.busy) return;
    this.update({ paused: false, error: null });
    this.schedule();
  }
  step() {
    if (this.state.config.mode !== "ai-ai" || this.state.busy) return Promise.resolve();
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
    this.disposed = true;
    this.invalidate();
    this.listeners.clear();
    this.engine.close();
  }
}
