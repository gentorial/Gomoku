"""Current-engine self-play and stronger-teacher relabeling, resumable per game."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
import json
import math
from pathlib import Path
from .protocol import Worker, binary
from .rapfi import digest, generation_lock, identify, publish, read_json, sha256, write_json
from .teacher_client import TeacherClient
from .teacher_data import MATE_THRESHOLD, multipv_policy, opening_key


def training_openings(corpora, output, count=256, seed=42):
    """Freeze training-only groups; the parent opening stays attached to PV leaves."""
    choices = {}
    for root in corpora:
        for path in sorted(Path(root).glob("job-*.annotations.jsonl")):
            seen_games = set()
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    sample = json.loads(line)
                    if sample["gameId"] in seen_games:
                        continue
                    seen_games.add(sample["gameId"])
                    group = sample["openingId"]
                    # Reserve the first 20% for holdouts, even if a campaign
                    # later uses smaller validation/test fractions.
                    if int(digest([seed, group])[:16], 16) / 2**64 < 0.2:
                        continue
                    score = sample["teacher"].get("score", {})
                    if score.get("kind") != "eval" or abs(score["value"]) > 500:
                        continue
                    choices.setdefault(group, {"id": group, "moves": sample["position"]["moves"]})
    choices = sorted(choices.values(), key=lambda x: digest([seed, x["id"], "selfplay"]))
    if len(choices) < count:
        raise ValueError(f"Need {count} independent training openings; found {len(choices)}")
    result = {"format": "gomoku-openings-v1", "size": 15, "rule": "freestyle",
              "split": "train", "seed": seed, "openings": choices[:count]}
    write_json(output, result)
    return result


def choose_positions(record, roots=32, leaves=16):
    """Half uniform roots, half value reversals; PV leaves never inherit outcomes."""
    analyses = record["analyses"]
    if not analyses:
        return []
    uniform_count = min((roots + 1) // 2, len(analyses))
    chosen = {i * (len(analyses) - 1) // max(1, uniform_count - 1)
              for i in range(uniform_count)}
    swings = []
    for i in range(len(analyses) - 1):
        # Both sides' scores have opposite perspectives. Compare probabilities,
        # not raw centipawns from different engines or tactical score ranges.
        before = math.tanh(analyses[i]["score"]["value"] / 600.0)
        after = -math.tanh(analyses[i+1]["score"]["value"] / 600.0)
        swings.append((abs(before - after), i))
    for _, index in sorted(swings, reverse=True):
        if len(chosen) >= roots:
            break
        chosen.add(index)
    items, seen = [], set()

    def add(moves, kind, analysis):
        key = opening_key(moves, record["size"], record["rule"], plies=len(moves))
        if key in seen:
            return
        seen.add(key)
        items.append({"position": {"size": record["size"], "rule": record["rule"], "moves": moves},
                      "kind": kind, "student": analysis,
                      "result": record["status"] if kind == "root" and record["status"] != "playing" else None})

    for i in sorted(chosen):
        analysis = analyses[i]
        add(record["moves"][:analysis["ply"]], "root", analysis)
    # A search PV is a variation, not the played game. Keep its group but no
    # result label; terminal leaves are filtered by the native referee below.
    leaf_count = 0
    for i in sorted(chosen, key=lambda i: (-len(analyses[i].get("pv", [])), i)):
        pv = analyses[i].get("pv", [])
        if len(pv) < 2 or leaf_count >= leaves:
            continue
        analysis = analyses[i]
        add(record["moves"][:analysis["ply"]] + pv, "pv_leaf", analysis)
        leaf_count += 1
    return items


def mine_game(index, opening, *, engine, model, teacher_executable, teacher, settings, root, identity):
    stem = f"game-{index:05d}"
    summary_path, game_path = root / f"{stem}.json", root / f"{stem}.record.json"
    labels_path, raw_path = root / f"{stem}.annotations.jsonl", root / f"{stem}.teacher.jsonl"
    if summary_path.exists():
        old = read_json(summary_path)
        if old["identity"] != identity or any(sha256(root / k) != v for k, v in old["files"].items()):
            raise ValueError("Mining job identity or checksum changed")
        return old
    with ExitStack() as stack:
        worker = stack.enter_context(Worker(engine, model))
        if game_path.exists():
            record = read_json(game_path)
            if record.get("miningIdentity") != identity:
                raise ValueError("Self-play record belongs to another mining run")
        else:
            position = {"size": 15, "rule": "freestyle", "moves": list(opening["moves"])}
            state = worker.request("inspect", position=position)
            if state["status"] != "playing":
                raise ValueError("Self-play opening is terminal")
            analyses = []
            while state["status"] == "playing" and len(position["moves"]) < settings["maxPlies"]:
                analysis = worker.request("analyze", position=position, evaluator="nnue",
                                          limits={"timeMs": settings["timeMs"], "maxDepth": settings["depth"]})
                if analysis.get("evaluator") != "line11-nnue-v1":
                    raise ValueError("Self-play silently fell back from the requested NNUE")
                analyses.append({"ply": len(position["moves"]), **analysis})
                state = worker.request("play", position=position, move=analysis["bestMove"])
                position["moves"] = state["moves"]
            record = {"format": "gomoku-record-v1", **position, "status": state["status"],
                      "openingId": opening["id"], "analyses": analyses,
                      "miningIdentity": identity, "gameId": digest([identity, index])}
            write_json(game_path, record)
        client = stack.enter_context(TeacherClient(teacher_executable, nodes=settings["teacherNodes"],
                                                  multipv=settings["multipv"]))
        samples, reports = [], []
        for item in choose_positions(record, settings["roots"], settings["leaves"]):
            state = worker.request("inspect", position=item["position"])
            if state["status"] != "playing":
                continue
            report = client.analyze(item["position"]["moves"])
            candidates, label = report["candidates"], {**teacher, "perspective": "side_to_move"}
            score = candidates[0]["eval"]
            if score is not None:
                label["score"] = {"kind": "mate" if abs(score) >= MATE_THRESHOLD else "eval", "value": score}
            sample = {"format": "gomoku-sample-v1", "gameId": record["gameId"],
                      "openingId": record["openingId"], "position": item["position"],
                      "result": item["result"], "teacher": label, "policy": multipv_policy(candidates),
                      "source": {"kind": "current-engine-relabel", "positionKind": item["kind"],
                                 "miningIdentity": identity, "student": item["student"],
                                 "resultVerified": item["result"] is not None,
                                 "teacherLabelDepth": report["labelDepth"],
                                 "teacherReportedNodes": report["reportedNodes"],
                                 "teacherFinalMove": report["finalMove"]},
                      "rawMultiPV": candidates}
            samples.append(sample)
            reports.append({"position": item["position"], **report})
        for path, values in ((labels_path, samples), (raw_path, reports)):
            publish(path, lambda stream, values=values: stream.writelines(
                json.dumps(value, allow_nan=False) + "\n" for value in values))
    result = {"identity": identity, "index": index, "positions": len(samples),
              "pvLeaves": sum(s["source"]["positionKind"] == "pv_leaf" for s in samples),
              "teacherDisagreements": sum(s["policy"]["moves"][0]["x"] != s["source"]["student"]["bestMove"]["x"]
                                          or s["policy"]["moves"][0]["y"] != s["source"]["student"]["bestMove"]["y"]
                                          for s in samples if s["source"]["positionKind"] == "root"),
              "status": record["status"], "annotations": labels_path.name,
              "files": {p.name: sha256(p) for p in (game_path, labels_path, raw_path)}}
    write_json(summary_path, result)
    return result


def mine(openings, output, model, teacher_executable, *, engine=None, workers=2,
         time_ms=80, depth=8, max_plies=120, roots=32, leaves=16, teacher_nodes=200000, multipv=4):
    engine, model, teacher_executable = map(lambda p: Path(p).resolve(), (engine or binary(), model, teacher_executable))
    root = Path(output).resolve()
    settings = {"timeMs": time_ms, "depth": depth, "maxPlies": max_plies,
                "roots": roots, "leaves": leaves, "teacherNodes": teacher_nodes, "multipv": multipv}
    if not (1 <= workers <= 32 and 0 <= time_ms <= 10000 and 1 <= depth <= 12 and
            7 <= max_plies <= 225 and roots >= 1 and leaves >= 0 and teacher_nodes >= 1 and 1 <= multipv <= 32):
        raise ValueError("Invalid mining settings")
    opening_set = read_json(openings)
    if (opening_set.get("format"), opening_set.get("size"), opening_set.get("rule"), opening_set.get("split")) != (
            "gomoku-openings-v1", 15, "freestyle", "train"):
        raise ValueError("Mining requires a frozen training-only opening set")
    if not opening_set["openings"] or len({o["id"] for o in opening_set["openings"]}) != len(opening_set["openings"]):
        raise ValueError("Mining openings must be nonempty and unique")
    for opening in opening_set["openings"]:
        if (opening["id"] != opening_key(opening["moves"], 15, "freestyle") or
            int(digest([opening_set["seed"], opening["id"]])[:16], 16) / 2**64 < 0.2):
            raise ValueError("Mining opening is not a training group")
    teacher = identify(teacher_executable)
    teacher["search"] = {"requestedNodes": teacher_nodes, "multipv": multipv, "threads": 1,
                         "hashMiB": 64, "protocol": "YXBOARD/YXNBEST", "labels": "last-complete-iteration"}
    setup = {"settings": settings, "engineSha256": sha256(engine), "modelSha256": sha256(model),
             "openingsSha256": sha256(openings), "teacher": teacher}
    identity = digest(setup)
    with generation_lock(root):
        if (root / "mining.json").exists() and read_json(root / "mining.json")["identity"] != identity:
            raise ValueError("Mining resume configuration changed")
        write_json(root / "mining.json", {"identity": identity, **setup})
        jobs = []
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            pending = [pool.submit(mine_game, i, opening, engine=engine, model=model,
                                   teacher_executable=teacher_executable, teacher=teacher,
                                   settings=settings, root=root, identity=identity)
                       for i, opening in enumerate(opening_set["openings"])]
            for future in as_completed(pending):
                result = future.result()
                jobs.append(result)
                print(json.dumps({"event": "mine-game", "completed": len(jobs),
                                  "total": len(pending), "positions": sum(j["positions"] for j in jobs),
                                  "disagreements": sum(j["teacherDisagreements"] for j in jobs)}), flush=True)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        result = {"kind": "gomoku-relabel-corpus-v1", "identity": identity, "teacher": teacher,
                  "jobs": sorted(jobs, key=lambda j: j["index"]),
                  "counts": {"games": len(jobs), "positions": sum(j["positions"] for j in jobs),
                             "pvLeaves": sum(j["pvLeaves"] for j in jobs),
                             "disagreements": sum(j["teacherDisagreements"] for j in jobs)}}
        write_json(root / "manifest.json", result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--teacher", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    result = mine(args.openings, args.output, args.model, args.teacher, workers=args.workers)
    print(json.dumps(result["counts"]))


if __name__ == "__main__":
    main()
