import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
import hashlib
import json
import math
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Event, Lock
from .protocol import Pbrain, Worker, binary
from .rapfi import write_json


def worker_match(engine_a, engine_b, openings, *, model_a=None, model_b=None,
                 size=15, rule="freestyle", time_ms=300, depth=8, on_game=None, completed=(), workers=1):
    """Each fixed opening is played twice, swapping engines, with no adjudication.

    Per-move analysis and complete records make failures and search budgets
    inspectable. This small comparison does not estimate an Elo rating.
    """
    if not openings or len({item["id"] for item in openings}) != len(openings):
        raise ValueError("Need nonempty openings with unique ids")
    if not 0 <= time_ms <= 10000 or not 1 <= depth <= 12:
        raise ValueError("Invalid arena search limits")
    if type(workers) is not int or workers < 1:
        raise ValueError("Arena workers must be a positive integer")
    completed = list(completed)
    expected = [(o["id"], c) for o in openings for c in ("black", "white")]
    previous = [(r["openingId"], r["aColor"]) for r in completed]
    if len(set(previous)) != len(previous) or not set(previous).issubset(expected):
        raise ValueError("Arena resume contains duplicate or unknown opening/color results")
    results = dict(zip(previous, completed))
    finished = set(previous)
    pending = [o for o in openings if any((o["id"], c) not in finished for c in ("black", "white"))]
    if not pending:
        return [results[key] for key in expected]
    # Validate all openings before publishing any game, including in parallel runs.
    with Worker() as referee:
        for opening in openings:
            state = referee.request("inspect", position={"size": size, "rule": rule,
                                                        "moves": opening["moves"]})
            if state["status"] != "playing":
                raise ValueError("Opening is already terminal: " + opening["id"])
    concurrency = min(workers, len(pending))
    queue, stopped, lock = SimpleQueue(), Event(), Lock()
    for opening in pending:
        queue.put(opening)

    def lane():
        # Each lane owns its referee and both engines; only finished records are shared.
        with Worker() as referee, Worker(engine_a, model_a) as a, Worker(engine_b, model_b) as b:
            while not stopped.is_set():
                try:
                    opening = queue.get_nowait()
                except Empty:
                    return
                for a_black in (True, False):
                    key = (opening["id"], "black" if a_black else "white")
                    if key in finished:
                        continue
                    players = (a, b) if a_black else (b, a)
                    position = {"size": size, "rule": rule, "moves": list(opening["moves"])}
                    status, failure, analyses = "playing", None, []
                    while status == "playing":
                        if stopped.is_set():
                            return
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
                            if selected_model is not None and analysis.get("evaluator") != "line11-nnue-v1":
                                raise RuntimeError("Arena silently fell back from the requested NNUE")
                            analyses.append({"ply": len(position["moves"]),
                                             "engine": "a" if player is a else "b", **analysis})
                            state = referee.request("play", position=position, move=analysis["bestMove"])
                            position["moves"], status = state["moves"], state["status"]
                        except (ValueError, RuntimeError, TimeoutError, OSError) as error:
                            status = "white_win" if side == 0 else "black_win"
                            failure = str(error)
                    score = 0.5 if status == "draw" else int((status == "black_win") == a_black)
                    game = {"format": "gomoku-record-v1", **position, "status": status,
                            "openingId": opening["id"], "openingPly": len(opening["moves"]),
                            "aColor": key[1], "scoreA": score, "failure": failure,
                            "analyses": analyses, "arenaWorkers": concurrency}
                    # A single writer publishes complete games immediately, even out of order.
                    with lock:
                        if stopped.is_set():
                            return
                        results[key] = game
                        if on_game is not None:
                            on_game([results[k] for k in expected if k in results])

    if concurrency == 1:
        lane()
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(lane) for _ in range(concurrency)]
            try:
                for future in as_completed(futures):
                    future.result()
            except BaseException:
                stopped.set()
                for future in futures:
                    future.cancel()
                raise
    return [results[key] for key in expected]


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


def paired_summary(results):
    """Exact one-sided sign test on independent complete opening pairs.

    Treat the pair as the unit, not the two correlated color-swapped games.
    Tied pairs provide no sign information. This is a promotion gate, not Elo.
    """
    pairs = {}
    for game in results:
        colors = pairs.setdefault(game["openingId"], {})
        if game["aColor"] in colors:
            raise ValueError("Duplicate color in an arena pair")
        colors[game["aColor"]] = game["scoreA"]
    scores = [sum(p.values()) for p in pairs.values() if set(p) == {"black", "white"}]
    wins, losses = sum(s > 1 for s in scores), sum(s < 1 for s in scores)
    n = wins + losses
    p = sum(math.comb(n, k) for k in range(wins, n + 1)) / 2**n if n else 1.0
    return {"pairs": len(scores), "winningPairs": wins, "losingPairs": losses,
            "tiedPairs": len(scores) - n, "oneSidedSignP": p}


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
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--workers", type=int, default=1, help="Concurrent opening pairs for --protocol worker")
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

        completed = []
        if args.output.exists():
            old = json.loads(args.output.read_text(encoding="utf-8"))
            if not args.resume or any(old.get(k) != v for k, v in report.items()):
                parser.error("Arena report already exists; resume requires identical engines, models, openings and limits")
            completed = old["games"]

        def save(results):
            report.update(summary=summary(results), paired=paired_summary(results), games=results,
                          execution={"requestedWorkers": args.workers, "unit": "opening-pair"})
            write_json(args.output, report)
            print(json.dumps(report["summary"]), flush=True)

        try:
            results = worker_match(engine_a, engine_b, opening_set["openings"], model_a=args.model_a,
                         model_b=args.model_b, size=args.size, rule=args.rule,
                         time_ms=args.time_ms, depth=args.depth, on_game=save, completed=completed,
                         workers=args.workers)
            save(results)
        except (ValueError, RuntimeError, TimeoutError, OSError) as error:
            parser.exit(2, f"Arena failed: {error}\n")
    else:
        if args.workers != 1:
            parser.error("Parallel arena currently requires --protocol worker")
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
