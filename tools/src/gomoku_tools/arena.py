import argparse
from contextlib import ExitStack
import json
from pathlib import Path
from .protocol import Pbrain, Worker, binary


def match(engine_a, engine_b, games=2, size=15, rule="freestyle", time_ms=20):
    results = []
    with Worker() as referee:
        for game_index in range(games):
            a_black = game_index % 2 == 0
            with ExitStack() as stack:
                # A and B swap colors each game. No rating is inferred from this small sample.
                players = [
                    stack.enter_context(Pbrain(path, size, rule, time_ms))
                    for path in ([engine_a, engine_b] if a_black else [engine_b, engine_a])
                ]
                position = {"size": size, "rule": rule, "moves": []}
                status, failure = "playing", None
                while status == "playing":
                    side = len(position["moves"]) % 2
                    try:
                        move = players[side].move(position["moves"])
                        state = referee.request("play", position=position, move=move)
                        position["moves"], status = state["moves"], state["status"]
                    except (ValueError, RuntimeError, TimeoutError, OSError) as error:
                        status = "white_win" if side == 0 else "black_win"
                        failure = str(error)
                score_a = 0.5 if status == "draw" else int((status == "black_win") == a_black)
                results.append({"format": "gomoku-record-v1", **position, "status": status,
                                "aColor": "black" if a_black else "white",
                                "scoreA": score_a, "failure": failure})
    return results


def main():
    parser = argparse.ArgumentParser(description="Run two Gomocup executables with a native referee")
    parser.add_argument("--engine-a", type=Path, default=binary("pbrain-gomoku"))
    parser.add_argument("--engine-b", type=Path, default=binary("pbrain-gomoku"))
    parser.add_argument("--games", type=int, default=2)
    parser.add_argument("--size", type=int, choices=[15, 20], default=15)
    parser.add_argument("--rule", choices=["freestyle", "standard"], default="freestyle")
    parser.add_argument("--time-ms", type=int, default=20)
    parser.add_argument("--output", type=Path, default=Path("artifacts/arena.json"))
    args = parser.parse_args()
    if args.games < 2 or args.games % 2:
        parser.error("--games must be a positive even number for paired colors")
    results = match(args.engine_a, args.engine_b, args.games, args.size, args.rule, args.time_ms)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps({"games": len(results), "scoreA": sum(game["scoreA"] for game in results),
                      "failures": sum(game["failure"] is not None for game in results),
                      "output": str(args.output)}))


if __name__ == "__main__":
    main()
