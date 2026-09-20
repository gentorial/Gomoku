import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import {
  Bot,
  ChartNoAxesCombined,
  Check,
  ChevronRight,
  Download,
  LoaderCircle,
  Pause,
  Play,
  RotateCcw,
  Settings2,
  StepForward,
  Undo2,
  UserRound,
  Users,
  X,
  type LucideIcon,
} from "lucide-react";
import { BrowserEngine, type ModelProgress } from "@gomoku/engine-wasm";
import type { MatchConfig, Rule } from "@gomoku/contracts";
import { Board, colorName, pointName } from "./Board.js";
import { MatchController, initialSnapshot, isHuman } from "./game/controller.js";

const modelAvailable = __GOMOKU_MODEL_MANIFEST__ !== null;

function IconButton({
  icon: Icon,
  label,
  onClick,
  disabled,
  active = false,
}: {
  icon: LucideIcon;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
}) {
  return (
    <button
      type="button"
      className={"icon-button" + (active ? " active" : "")}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
    >
      <Icon size={20} strokeWidth={1.6} aria-hidden="true" />
    </button>
  );
}

function Panel({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current!;
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    element.showModal();
    return () => {
      element.close();
      trigger?.focus();
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      className="panel"
      aria-labelledby="panel-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) {
          const box = event.currentTarget.getBoundingClientRect();
          if (
            event.clientX < box.left ||
            event.clientX > box.right ||
            event.clientY < box.top ||
            event.clientY > box.bottom
          )
            onClose();
        }
      }}
    >
      <div className="panel-header">
        <h1 id="panel-title">{title}</h1>
        <IconButton icon={X} label="关闭" onClick={onClose} />
      </div>
      {children}
    </dialog>
  );
}

const modes: { value: MatchConfig["mode"]; label: string; icon: LucideIcon }[] = [
  { value: "human-human", label: "人人", icon: Users },
  { value: "human-ai", label: "人机", icon: UserRound },
  { value: "ai-ai", label: "机机", icon: Bot },
];
function Settings({
  config,
  onStart,
}: {
  config: MatchConfig;
  onStart: (config: MatchConfig) => void;
}) {
  const [draft, setDraft] = useState(config);
  function update(patch: Partial<MatchConfig>) {
    setDraft((value) => {
      const next = { ...value, ...patch };
      if (next.size !== 15 || next.rule !== "freestyle") next.evaluator = "handcrafted";
      return next;
    });
  }
  function timeControl(color: "black" | "white") {
    const key = color === "black" ? "blackTimeMs" : "whiteTimeMs";
    const label = draft.mode === "ai-ai" ? colorName(color) + "思考时间" : "AI 思考时间";
    return (
      <label className="time-field" key={color}>
        <span>
          {label}
          <output>{(draft[key] / 1000).toFixed(1)} 秒 / 手</output>
        </span>
        <input
          aria-label={label}
          type="range"
          min={100}
          max={3000}
          step={100}
          value={draft[key]}
          onChange={(event) => update({ [key]: Number(event.target.value) })}
        />
      </label>
    );
  }
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onStart(draft);
      }}
    >
      <div className="mode-picker" role="group" aria-label="对弈模式">
        {modes.map(({ value, label, icon: Icon }) => (
          <button
            key={value}
            type="button"
            aria-pressed={draft.mode === value}
            onClick={() => update({ mode: value })}
          >
            <Icon size={20} strokeWidth={1.6} aria-hidden="true" />
            {label}
          </button>
        ))}
      </div>
      <div className="fields">
        <label>
          规则
          <select
            value={draft.rule}
            onChange={(event) => update({ rule: event.target.value as Rule })}
          >
            <option value="freestyle">自由五子棋</option>
            <option value="standard">标准五子棋</option>
          </select>
        </label>
        <label>
          棋盘
          <select
            value={draft.size}
            onChange={(event) => update({ size: Number(event.target.value) as 15 | 20 })}
          >
            <option value={15}>15 × 15</option>
            <option value={20}>20 × 20</option>
          </select>
        </label>
      </div>
      <p className="rule-note">
        {draft.rule === "freestyle" ? "连成五子或以上获胜" : "恰好五子获胜，长连不胜"}
      </p>
      {draft.mode !== "human-human" && (
        <div className="model-field">
          <label>
            AI
            <select
              value={draft.evaluator}
              onChange={(event) =>
                update({ evaluator: event.target.value as MatchConfig["evaluator"] })
              }
            >
              <option
                value="nnue"
                disabled={!modelAvailable || draft.size !== 15 || draft.rule !== "freestyle"}
              >
                Rapfi NNUE v1
              </option>
              <option value="handcrafted">基础引擎</option>
            </select>
          </label>
          <p className="rule-note">
            {draft.evaluator === "nnue"
              ? "已训练 · 15×15 自由五子棋 · 首次下载约 97 MB"
              : !modelAvailable
                ? "NNUE 权重尚未配置"
                : "手工评估 · NNUE 支持 15×15 自由五子棋"}
          </p>
        </div>
      )}
      {draft.mode === "human-ai" && (
        <div className="color-picker" role="group" aria-label="你的执子">
          {(["black", "white"] as const).map((color) => (
            <button
              key={color}
              type="button"
              aria-pressed={draft.humanColor === color}
              onClick={() => update({ humanColor: color })}
            >
              <span className={"mini-stone " + color} />
              {color === "black" ? "执黑先行" : "执白后行"}
              {draft.humanColor === color && <Check size={14} aria-hidden="true" />}
            </button>
          ))}
        </div>
      )}
      {draft.mode === "human-ai" && timeControl(draft.humanColor === "black" ? "white" : "black")}
      {draft.mode === "ai-ai" && (
        <>
          {timeControl("black")}
          {timeControl("white")}
        </>
      )}
      <button type="submit" className="primary-button">
        <Play size={16} aria-hidden="true" />
        开始对局
      </button>
    </form>
  );
}

const noSubscribe = () => () => {};
const getInitial = () => initialSnapshot;
export function App() {
  const [controller, setController] = useState<MatchController | null>(null);
  const [panel, setPanel] = useState<"settings" | "analysis" | null>(null);
  const [modelProgress, setModelProgress] = useState<ModelProgress | null>(null);
  // Each effect owns its engine; cleanup also works during StrictMode's remount.
  useEffect(() => {
    let disposed = false;
    const match = new MatchController(
      new BrowserEngine({
        modelManifestUrl: __GOMOKU_MODEL_MANIFEST__
          ? new URL(__GOMOKU_MODEL_MANIFEST__, location.href).href
          : undefined,
        onModelProgress: (progress) => {
          if (!disposed) setModelProgress(progress);
        },
      }),
    );
    setController(match);
    void match.start({
      ...initialSnapshot.config,
      evaluator: modelAvailable ? "nnue" : "handcrafted",
    });
    return () => {
      disposed = true;
      match.dispose();
    };
  }, []);
  const state = useSyncExternalStore(
    controller?.subscribe ?? noSubscribe,
    controller?.getSnapshot ?? getInitial,
  );
  const { position, config, thinking, paused, busy, error, analysis } = state;
  const terminal = position && position.status !== "playing";
  const aiMatch = config.mode === "ai-ai";
  const side =
    position?.status === "black_win"
      ? "black"
      : position?.status === "white_win"
        ? "white"
        : (position?.toMove ?? "black");
  const status = !position
    ? "载入棋盘"
    : terminal
      ? position.status === "draw"
        ? "和棋"
        : colorName(side) + "获胜"
      : thinking
        ? config.evaluator === "nnue" && modelProgress && modelProgress.phase !== "ready"
          ? modelProgress.phase === "downloading"
            ? "载入 AI · " + Math.floor((modelProgress.loaded / modelProgress.total) * 100) + "%"
            : modelProgress.phase === "verifying"
              ? "校验 AI"
              : "载入 AI"
          : colorName(thinking) + "思考中"
        : paused && aiMatch
          ? "已暂停"
          : colorName(side) + "行棋";
  const playerIcon = isHuman(config, side) ? UserRound : Bot;
  const PlayerIcon = config.mode === "human-human" ? Users : playerIcon;
  function download() {
    if (!position) return;
    const blob = new Blob(
      [
        JSON.stringify(
          {
            format: "gomoku-record-v1",
            size: position.size,
            rule: position.rule,
            moves: position.moves,
            status: position.status,
          },
          null,
          2,
        ),
      ],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "gomoku-" + new Date().toISOString().replace(/[:.]/g, "-") + ".json";
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <main className="play-page">
      <section className="game" aria-label="五子棋对弈">
        <div className="game-status">
          <div className="turn" role="status" aria-live="polite">
            <span className={"mini-stone " + side} />
            <span>{status}</span>
            {thinking || (!position && busy) ? (
              <LoaderCircle className="spin" size={15} aria-hidden="true" />
            ) : (
              <PlayerIcon size={15} strokeWidth={1.5} aria-hidden="true" />
            )}
          </div>
          <span className="ply-count">
            {position?.moves.length ?? 0}
            <span>手</span>
          </span>
        </div>
        <Board
          key={position?.size ?? 15}
          position={position}
          disabled={!controller?.canPlay()}
          onMove={(move) => void controller?.play(move)}
        />
        <div className="game-actions" role="group" aria-label="对局操作">
          <IconButton
            icon={Undo2}
            label="悔棋"
            disabled={!controller?.canUndo() || (busy && !thinking)}
            onClick={() => void controller?.undo()}
          />
          <IconButton
            icon={RotateCcw}
            label="重新开始"
            disabled={!controller}
            onClick={() => void controller?.start()}
          />
          {aiMatch && (
            <>
              <span className="action-divider" />
              <IconButton
                icon={paused ? Play : Pause}
                label={paused ? "继续对弈" : "暂停对弈"}
                disabled={!position || !!terminal || (busy && !thinking)}
                onClick={() => (paused ? controller?.resume() : controller?.pause())}
              />
              <IconButton
                icon={StepForward}
                label="单步落子"
                disabled={!position || !!terminal || busy || !paused}
                onClick={() => void controller?.step()}
              />
            </>
          )}
          <span className="action-divider" />
          <IconButton
            icon={ChartNoAxesCombined}
            label="最近一步分析"
            active={panel === "analysis"}
            onClick={() => setPanel("analysis")}
          />
          <IconButton
            icon={Settings2}
            label="对局设置"
            active={panel === "settings"}
            onClick={() => setPanel("settings")}
          />
        </div>
        {error && (
          <div className="game-error" role="alert">
            <span>{error}</span>
            <button
              type="button"
              onClick={() => (position ? controller?.resume() : void controller?.start())}
            >
              <RotateCcw size={14} aria-hidden="true" />
              重试
            </button>
          </div>
        )}
      </section>
      {panel === "settings" && (
        <Panel title="对局设置" onClose={() => setPanel(null)}>
          <Settings
            config={config}
            onStart={(next) => {
              setPanel(null);
              void controller?.start(next);
            }}
          />
        </Panel>
      )}
      {panel === "analysis" && (
        <Panel title="最近一步分析" onClose={() => setPanel(null)}>
          {analysis && position ? (
            <>
              <div className="analysis-move">
                <span>
                  {analysis.result.bestMove
                    ? pointName(analysis.result.bestMove, position.size)
                    : "—"}
                </span>
                <span className="analysis-side">
                  <span className={"mini-stone " + analysis.color} />第 {analysis.ply} 手
                </span>
              </div>
              <dl className="analysis-stats">
                <div>
                  <dt>深度</dt>
                  <dd>{analysis.result.depth}</dd>
                </div>
                <div>
                  <dt>节点</dt>
                  <dd>{analysis.result.nodes.toLocaleString()}</dd>
                </div>
                <div>
                  <dt>用时</dt>
                  <dd>
                    {analysis.result.elapsedMs}
                    <small> ms</small>
                  </dd>
                </div>
                <div>
                  <dt>评分 · {colorName(analysis.color)}视角</dt>
                  <dd>
                    {analysis.result.score.kind === "mate"
                      ? analysis.result.score.value > 0
                        ? "搜索判胜"
                        : "搜索判负"
                      : (analysis.result.score.value > 0 ? "+" : "") + analysis.result.score.value}
                  </dd>
                </div>
              </dl>
              <div className="variation">
                <p className="analysis-model">
                  <Bot size={14} aria-hidden="true" />
                  {analysis.result.model?.label ??
                    (analysis.result.evaluator === "line11-nnue-v1" ? "NNUE" : "基础引擎")}
                </p>
                <span>预想变化</span>
                <div>
                  {analysis.result.pv.map((move, i) => (
                    <span key={i}>
                      {i > 0 && <ChevronRight size={12} aria-hidden="true" />}
                      {pointName(move, position.size)}
                    </span>
                  ))}
                </div>
              </div>
            </>
          ) : (
            <p className="empty-analysis">暂无分析</p>
          )}
          <div className="panel-footer">
            <span>{position?.moves.length ?? 0} 手</span>
            <button type="button" onClick={download} disabled={!position?.moves.length}>
              <Download size={16} strokeWidth={1.6} aria-hidden="true" />
              导出棋谱
            </button>
          </div>
        </Panel>
      )}
    </main>
  );
}
