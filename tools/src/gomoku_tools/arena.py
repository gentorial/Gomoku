import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
from .protocol import Pbrain, Worker, binary


def worker_match(engine_a, engine_b, openings, *, model_a=None, model_b=None,
                 size=15, rule="freestyle", time_ms=300, depth=8, on_game=None):
    """Each fixed opening is played twice, swapping engines, with no adjudication.

    Per-move analysis and complete records make failures and search budgets
    inspectable. This small comparison does not estimate an Elo rating.
    """
    if not openings or len({item["id"] for item in openings}) != len(openings):
        raise ValueError("Need nonempty openings with unique ids")
    if not 0 <= time_ms <= 10000 or not 1 <= depth <= 12:
        raise ValueError("Invalid arena search limits")
    results = []
    with Worker() as referee, Worker(engine_a, model_a) as a, Worker(engine_b, model_b) as b:
        for opening in openings:
            state = referee.request("inspect", position={"size": size, "rule": rule,
                                                        "moves": opening["moves"]})
            if state["status"] != "playing":
                raise ValueError("Opening is already terminal: " + opening["id"])
        for opening in openings:
            for a_black in (True, False):
                players = (a, b) if a_black else (b, a)
                position = {"size": size, "rule": rule, "moves": list(opening["moves"])}
                status, failure, analyses = "playing", None, []
                while status == "playing":
                    side = len(position["moves"]) % 2
                    player = players[side]
                    try:
                        # A requested model must never silently fall back to handcrafted evaluation.
                        selected_model = model_a if player is a else model_b
                        analysis = player.request(
                            "analyze", position=position,
                            evaluator="nnue" if selected_model is not None else "handcrafted",
                            limits={"timeMs": time_ms, "maxDepth": depth},
                        )
                        analyses.append({"ply": len(position["moves"]),
                                         "engine": "a" if player is a else "b", **analysis})
                        state = referee.request("play", position=position, move=analysis["bestMove"])
                        position["moves"], status = state["moves"], state["status"]
                    except (ValueError, RuntimeError, TimeoutError, OSError) as error:
                        status = "white_win" if side == 0 else "black_win"
                        failure = str(error)
                score = 0.5 if status == "draw" else int((status == "black_win") == a_black)
                results.append({"format": "gomoku-record-v1", **position, "status": status,
                                "openingId": opening["id"], "openingPly": len(opening["moves"]),
                                "aColor": "black" if a_black else "white", "scoreA": score,
                                "failure": failure, "analyses": analyses})
                if on_game is not None:
                    on_game(results)
    return results


def identity(path):
    if path is None:
        return None
    path = Path(path).resolve()
    with path.open("rb") as stream:
        return {"path": str(path), "sha256": hashlib.file_digest(stream, "sha256").hexdigest()}


def summary(results):
    return {"games": len(results), "scoreA": sum(game["scoreA"] for game in results),
            "winsA": sum(game["scoreA"] == 1 for game in results),
            "draws": sum(game["scoreA"] == 0.5 for game in results),
            "winsB": sum(game["scoreA"] == 0 for game in results),
            "failures": sum(game["failure"] is not None for game in results)}


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
    parser = argparse.ArgumentParser(description="Paired-color engine/model comparison with a native referee")
    parser.add_argument("--protocol", choices=["gomocup", "worker"], default="gomocup")
    parser.add_argument("--engine-a", type=Path)
    parser.add_argument("--engine-b", type=Path)
    parser.add_argument("--model-a", type=Path)
    parser.add_argument("--model-b", type=Path)
    parser.add_argument("--openings", type=Path, help="gomoku-openings-v1 JSON; each opening is played twice")
    parser.add_argument("--depth", type=int, default=8)
    parser.add_argument("--games", type=int, default=2)
    parser.add_argument("--size", type=int, choices=[15, 20], default=15)
    parser.add_argument("--rule", choices=["freestyle", "standard"], default="freestyle")
    parser.add_argument("--time-ms", type=int, default=20)
    parser.add_argument("--output", type=Path, default=Path("artifacts/arena.json"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.protocol == "worker":
        if args.openings is None:
            parser.error("--protocol worker requires --openings (two games per opening)")
        opening_set = json.loads(args.openings.read_text(encoding="utf-8"))
        if (opening_set.get("format"), opening_set.get("size"), opening_set.get("rule")) != (
                "gomoku-openings-v1", args.size, args.rule):
            parser.error("Opening set must match the requested board size and rule")
        engine_a, engine_b = args.engine_a or binary(), args.engine_b or binary()
        report = {"format": "gomoku-arena-v1", "protocol": "worker",
                  "engines": {"a": {"executable": identity(engine_a), "model": identity(args.model_a)},
                              "b": {"executable": identity(engine_b), "model": identity(args.model_b)}},
                  "openings": identity(args.openings), "size": args.size, "rule": args.rule,
                  "limits": {"timeMs": args.time_ms, "maxDepth": args.depth}}

        def save(results):
            report.update(summary=summary(results), games=results)
            args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report["summary"]), flush=True)

        try:
            worker_match(engine_a, engine_b, opening_set["openings"], model_a=args.model_a,
                         model_b=args.model_b, size=args.size, rule=args.rule,
                         time_ms=args.time_ms, depth=args.depth, on_game=save)
        except (ValueError, RuntimeError, TimeoutError, OSError) as error:
            parser.exit(2, f"Arena failed: {error}\n")
    else:
        if args.openings or args.model_a or args.model_b:
            parser.error("Models and fixed openings require --protocol worker")
        if args.games < 2 or args.games % 2:
            parser.error("--games must be a positive even number for paired colors")
        results = match(args.engine_a or binary("pbrain-gomoku"),
                        args.engine_b or binary("pbrain-gomoku"), args.games,
                        args.size, args.rule, args.time_ms)
        args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(json.dumps({**summary(results), "output": str(args.output)}))


if __name__ == "__main__":
    main()
