import argparse
import json
from pathlib import Path
from .protocol import Worker


def play(worker, size=15, rule="freestyle", time_ms=10, depth=2):
    position = {"size": size, "rule": rule, "moves": []}
    state = worker.request("inspect", position=position)
    while state["status"] == "playing":
        analysis = worker.request("analyze", position=position,
                                  limits={"timeMs": time_ms, "maxDepth": depth})
        state = worker.request("play", position=position, move=analysis["bestMove"])
        position["moves"] = state["moves"]
    return {"format": "gomoku-record-v1", **position, "status": state["status"],
            "source": {"type": "selfplay", "evaluator": "handcrafted-v1",
                       "timeMs": time_ms, "maxDepth": depth}}


def main():
    parser = argparse.ArgumentParser(description="Generate full-game NDJSON with the native engine")
    parser.add_argument("--games", type=int, default=1)
    parser.add_argument("--size", type=int, choices=[15, 20], default=15)
    parser.add_argument("--rule", choices=["freestyle", "standard"], default="freestyle")
    parser.add_argument("--time-ms", type=int, default=10)
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--output", type=Path, default=Path("data/selfplay.jsonl"))
    args = parser.parse_args()
    if args.games < 1:
        parser.error("--games must be positive")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with Worker() as worker, args.output.open("a", encoding="utf-8") as output:
        for index in range(args.games):
            record = play(worker, args.size, args.rule, args.time_ms, args.depth)
            output.write(json.dumps(record) + "\n")
            output.flush()
            print("Game", index + 1, record["status"], len(record["moves"]), "plies")


if __name__ == "__main__":
    main()
