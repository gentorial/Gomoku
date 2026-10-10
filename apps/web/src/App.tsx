import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import {
  Bot,
  ChartNoAxesCombined,
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
import type { Analysis, Color, MatchConfig, Rule } from "@gomoku/contracts";
import { Board, colorName, pointName } from "./Board.js";
import { MatchController, initialSnapshot, isHuman } from "./game/controller.js";

const modelAvailable = __GOMOKU_MODEL_MANIFEST__ !== null;
const modelSizes = __GOMOKU_MODEL_SIZES__;

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

function Choice<T extends string | number>({
  label,
  value,
  options,
  onChange,
  children,
}: {
  label: string;
  value: T;
  options: { value: T; label: ReactNode; disabled?: boolean; title?: string }[];
  onChange: (value: T) => void;
  children?: ReactNode;
}) {
  return (
    <div className="setting-row" role="group" aria-label={label}>
      <span className="setting-label">{label}</span>
      <div className="choices">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            aria-pressed={value === option.value}
            disabled={option.disabled}
            title={option.title}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        ))}
        {children}
      </div>
    </div>
  );
}

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
      if (!modelSizes.includes(next.size)) next.evaluator = "handcrafted";
      return next;
    });
  }
  function timeControl(color: "black" | "white") {
    const key = color === "black" ? "blackTimeMs" : "whiteTimeMs";
    const label = draft.mode === "ai-ai" ? colorName(color) + "思考时间" : "AI 思考时间";
    return (
      <label className="setting-row" key={color}>
        <span className="setting-label">{label}</span>
        <span className="time-control">
          <input
            aria-label={label}
            type="range"
            min={100}
            max={3000}
            step={100}
            value={draft[key]}
            onChange={(event) => update({ [key]: Number(event.target.value) })}
          />
          <output>{(draft[key] / 1000).toFixed(1)} 秒</output>
        </span>
      </label>
    );
  }
  const nnueSupported = modelSizes.includes(draft.size);
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onStart(draft);
      }}
    >
      <Choice
        label="模式"
        value={draft.mode}
        options={[
          { value: "human-human", label: "人人" },
          { value: "human-ai", label: "人机" },
          { value: "ai-ai", label: "机机" },
        ]}
        onChange={(mode) => update({ mode })}
      />
      <Choice
        label="规则"
        value={draft.rule}
        options={[
          { value: "freestyle", label: "自由" },
          { value: "standard", label: "标准" },
        ]}
        onChange={(rule: Rule) => update({ rule })}
      >
        {draft.rule === "standard" && <span className="choice-note">长连不胜</span>}
      </Choice>
      <Choice
        label="棋盘"
        value={draft.size}
        options={[
          { value: 15, label: "15" },
          { value: 20, label: "20" },
        ]}
        onChange={(size) => update({ size })}
      />
      {draft.mode !== "human-human" && (
        <Choice
          label="AI"
          value={draft.evaluator}
          options={[
            {
              value: "nnue",
              label: "NNUE",
              disabled: !modelAvailable || !nnueSupported,
              title: !modelAvailable
                ? "NNUE 权重尚未配置"
                : nnueSupported
                  ? undefined
                  : "NNUE 仅支持 " +
                    modelSizes.map((size) => size + "×" + size).join("、") +
                    " 棋盘",
            },
            { value: "handcrafted", label: "基础" },
          ]}
          onChange={(evaluator) => update({ evaluator })}
        />
      )}
      {draft.mode === "human-ai" && (
        <Choice
          label="执子"
          value={draft.humanColor}
          options={(["black", "white"] as const).map((color) => ({
            value: color,
            label: (
              <>
                <span className={"mini-stone " + color} />
                {color === "black" ? "黑" : "白"}
              </>
            ),
          }))}
          onChange={(humanColor) => update({ humanColor })}
        />
      )}
      {draft.mode === "human-ai" && timeControl(draft.humanColor === "black" ? "white" : "black")}
      {draft.mode === "ai-ai" && (
        <>
          {timeControl("black")}
          {timeControl("white")}
        </>
      )}
      <div className="settings-actions">
        <button type="submit" className="primary-button">
          开始对局
        </button>
      </div>
    </form>
  );
}

// Scores are from the mover's side. Mate scores are 100000 minus the plies to
// the win; heuristic tiers follow each evaluator's scale (NNUE: tanh(score / 600)
// is P(win) - P(loss)).
const mateScore = 100000;
const tiers = { nnue: [60, 250, 660], handcrafted: [100, 500, 2000] } as const;
function scoreText(result: Analysis, mover: Color) {
  const { value, kind } = result.score;
  const side =
    (value >= 0 ? mover : mover === "black" ? "white" : "black") === "black" ? "黑" : "白";
  const magnitude = Math.abs(value);
  if (kind === "mate") return side + Math.max(1, Math.ceil((mateScore - magnitude) / 2)) + "步杀";
  const [even, slight, clear] =
    result.evaluator === "line11-nnue-v1" ? tiers.nnue : tiers.handcrafted;
  if (magnitude < even) return "均势（" + magnitude + "）";
  return (
    side +
    (magnitude < slight ? "略优" : magnitude < clear ? "优势" : "大优") +
    "（" +
    magnitude +
    "）"
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
      evaluator:
        modelAvailable && modelSizes.includes(initialSnapshot.config.size) ? "nnue" : "handcrafted",
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
            label="分析"
            active={panel === "analysis"}
            onClick={() => setPanel("analysis")}
          />
          <IconButton
            icon={Download}
            label="导出棋谱"
            disabled={!position?.moves.length}
            onClick={download}
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
        <Panel title="分析" onClose={() => setPanel(null)}>
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
              <p className="analysis-score">{scoreText(analysis.result, analysis.color)}</p>
              {analysis.result.pv.length > 1 && (
                <div className="variation">
                  <span>预想变化</span>
                  <div>
                    {analysis.result.pv.slice(1).map((move, i) => (
                      <span key={i}>
                        {i > 0 && <ChevronRight size={12} aria-hidden="true" />}
                        {pointName(move, position.size)}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <p className="empty-analysis">暂无分析</p>
          )}
        </Panel>
      )}
    </main>
  );
}
