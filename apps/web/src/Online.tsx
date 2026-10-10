import { useState, useSyncExternalStore } from "react";
import { Check, Copy, Flag, LogOut, Undo2 } from "lucide-react";
import type { Color, PositionResult } from "@gomoku/contracts";
import { Board } from "./Board.js";
import type { OnlineController, OnlineSnapshot } from "./online/controller.js";

const ruleName = { freestyle: "自由", standard: "标准" } as const;

function describe(state: OnlineSnapshot): string {
  if (state.phase === "connecting") return "连接服务器…";
  if (state.connection === "reconnecting" && !state.error) return "连接中断，正在重连…";
  if (state.phase === "queued" && state.queued)
    return "正在匹配 · " + ruleName[state.queued.rule] + " " + state.queued.size + " 路";
  const room = state.room;
  if (!room) return "未加入对局";
  const opponent = room.players[room.you === "black" ? "white" : "black"];
  if (room.status === "playing" && !room.moves.length && !opponent) return "等待对手加入";
  if (room.status === "draw") return "和棋";
  if (room.status !== "playing") {
    const won = room.status === (room.you === "black" ? "black_win" : "white_win");
    if (room.ending === "resign") return won ? "对手认输，你赢了" : "你已认输";
    return won ? "你赢了" : "你输了";
  }
  if (!opponent) return "对手已离开";
  if (!opponent.connected) return "对手已断线，等待重连";
  if (room.undo === room.you) return "已请求悔棋，等待对手回应";
  return room.toMove === room.you ? "轮到你" : "等待对手落子";
}

/** A game against a remote player. The server's room state is the only source of truth. */
export function OnlineGame({ online, onLeave }: { online: OnlineController; onLeave: () => void }) {
  const state = useSyncExternalStore(online.subscribe, online.getSnapshot);
  const [confirming, setConfirming] = useState<"resign" | "leave" | null>(null);
  const [copied, setCopied] = useState(false);
  const room = state.room;
  const opponentColor: Color | null = room ? (room.you === "black" ? "white" : "black") : null;
  const started = !!room && !!room.players.black && !!room.players.white;
  const playing = started && room.status === "playing";
  const live = state.connection === "online";
  const position: PositionResult | null = room && {
    kind: "position",
    size: room.size,
    rule: room.rule,
    moves: room.moves,
    toMove: room.toMove,
    status: room.status,
  };
  // An undo takes back at least one of one's own moves.
  const ownMoves = room
    ? room.moves.filter((_, i) => (i % 2 === 0) === (room.you === "black")).length
    : 0;
  function leave() {
    if (playing && confirming !== "leave") return setConfirming("leave");
    online.leave();
    onLeave();
  }
  async function copy() {
    if (!room) return;
    try {
      await navigator.clipboard.writeText(room.code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* Clipboard may be unavailable; the code stays visible. */
    }
  }
  return (
    <>
      <div className="game-status">
        <div className="turn" role="status" aria-live="polite">
          <span className={"mini-stone " + (room?.toMove ?? "black")} />
          <span>{describe(state)}</span>
        </div>
        {room && !started && (
          <button
            type="button"
            className="room-code"
            onClick={() => void copy()}
            title="复制房间号"
          >
            房间号 <strong>{room.code}</strong>
            {copied ? (
              <Check size={14} aria-hidden="true" />
            ) : (
              <Copy size={14} aria-hidden="true" />
            )}
          </button>
        )}
      </div>
      {room && started && (
        <div className="online-players">
          {(["black", "white"] as const).map((color) => (
            <span key={color}>
              <span className={"mini-stone " + color} />
              {room.players[color]?.name ?? "—"}
              {color === room.you && <small>（你）</small>}
            </span>
          ))}
        </div>
      )}
      <Board
        key={position?.size ?? 15}
        position={position}
        disabled={!live || !playing || room?.toMove !== room?.you || room?.undo !== null}
        onMove={(move) => online.move(move)}
      />
      {room?.undo && room.undo === opponentColor && playing && (
        <div className="online-prompt" role="alert">
          <span>对手请求悔棋</span>
          <button type="button" onClick={() => online.reply(true)}>
            同意
          </button>
          <button type="button" onClick={() => online.reply(false)}>
            拒绝
          </button>
        </div>
      )}
      {confirming && (
        <div className="online-prompt" role="alert">
          <span>{confirming === "resign" ? "确定认输吗？" : "离开会判你认输，确定离开吗？"}</span>
          <button
            type="button"
            onClick={() => {
              setConfirming(null);
              if (confirming === "resign") online.resign();
              else leave();
            }}
          >
            {confirming === "resign" ? "认输" : "离开"}
          </button>
          <button type="button" onClick={() => setConfirming(null)}>
            取消
          </button>
        </div>
      )}
      <div className="game-actions" role="group" aria-label="联机操作">
        <button
          type="button"
          className="icon-button"
          aria-label="请求悔棋"
          title="请求悔棋"
          disabled={!live || !playing || room?.undo !== null || ownMoves === 0}
          onClick={() => online.undo()}
        >
          <Undo2 size={20} strokeWidth={1.6} aria-hidden="true" />
        </button>
        <button
          type="button"
          className="icon-button"
          aria-label="认输"
          title="认输"
          disabled={!live || !playing}
          onClick={() => setConfirming("resign")}
        >
          <Flag size={20} strokeWidth={1.6} aria-hidden="true" />
        </button>
        <span className="action-divider" />
        <button
          type="button"
          className="icon-button"
          aria-label="离开"
          title="离开"
          onClick={leave}
        >
          <LogOut size={20} strokeWidth={1.6} aria-hidden="true" />
        </button>
      </div>
      {state.error && (
        <div className="game-error" role="alert">
          <span>{state.error}</span>
        </div>
      )}
    </>
  );
}
