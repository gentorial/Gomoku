import { useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import type { Color, Move, PositionResult } from "@gomoku/contracts";

export const letters = "ABCDEFGHJKLMNOPQRSTU";
export const pointName = (move: Move, size: number) => letters[move.x] + String(size - move.y);
export const colorName = (color: Color) => (color === "black" ? "黑棋" : "白棋");

export function Board({
  position,
  disabled,
  onMove,
}: {
  position: PositionResult | null;
  disabled: boolean;
  onMove: (move: Move) => void;
}) {
  const size = position?.size ?? 15;
  const [focus, setFocus] = useState(Math.floor(size / 2) * (size + 1));
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const stones = new Map(position?.moves.map((move, i) => [move.y * size + move.x, i]));
  const stars = size === 15 ? [3, 7, 11] : [3, 9, 15];
  function navigate(event: KeyboardEvent, index: number) {
    const x = index % size;
    let next = index;
    if (event.key === "ArrowLeft") next = index - (x > 0 ? 1 : 0);
    else if (event.key === "ArrowRight") next = index + (x < size - 1 ? 1 : 0);
    else if (event.key === "ArrowUp") next = Math.max(x, index - size);
    else if (event.key === "ArrowDown") next = Math.min((size - 1) * size + x, index + size);
    else return;
    event.preventDefault();
    setFocus(next);
    buttons.current[next]?.focus();
  }
  return (
    <div className="board-frame">
      <div
        className="board"
        role="group"
        aria-label={size + " 路五子棋棋盘"}
        style={{ "--size": size } as CSSProperties}
      >
        <svg
          className="board-lines"
          viewBox={"0 0 " + (size - 1) + " " + (size - 1)}
          aria-hidden="true"
        >
          {Array.from({ length: size }, (_, i) => (
            <g key={i}>
              <line x1={0} y1={i} x2={size - 1} y2={i} />
              <line x1={i} y1={0} x2={i} y2={size - 1} />
            </g>
          ))}
          {stars.flatMap((y) =>
            stars.map((x) => <circle key={x + ":" + y} cx={x} cy={y} r={0.06} />),
          )}
        </svg>
        <div className="intersections">
          {Array.from({ length: size * size }, (_, index) => {
            const x = index % size,
              y = Math.floor(index / size);
            const ply = stones.get(index);
            const occupied = ply !== undefined;
            const last = occupied && ply === (position?.moves.length ?? 0) - 1;
            const color = occupied ? (ply % 2 === 0 ? "black" : "white") : position?.toMove;
            return (
              <button
                key={index}
                ref={(element) => {
                  buttons.current[index] = element;
                }}
                type="button"
                className={"intersection " + (occupied ? color : "empty")}
                aria-label={
                  pointName({ x, y }, size) +
                  (occupied ? "，" + colorName(color!) + "，第 " + (ply + 1) + " 手" : "，空位")
                }
                aria-disabled={disabled || occupied}
                tabIndex={focus === index ? 0 : -1}
                onFocus={() => setFocus(index)}
                onKeyDown={(event) => navigate(event, index)}
                onClick={() => {
                  if (!disabled && !occupied) onMove({ x, y });
                }}
              >
                {occupied && (
                  <span className={"stone" + (last ? " last" : "")}>{last ? ply + 1 : ""}</span>
                )}
                {!occupied && !disabled && <span className={"ghost " + color} />}
              </button>
            );
          })}
        </div>
        <div className="coordinates horizontal" aria-hidden="true">
          {Array.from({ length: size }, (_, i) => (
            <span key={i}>{letters[i]}</span>
          ))}
        </div>
        <div className="coordinates vertical" aria-hidden="true">
          {Array.from({ length: size }, (_, i) => (
            <span key={i}>{size - i}</span>
          ))}
        </div>
      </div>
    </div>
  );
}
