"""Validate raw records/annotations with C++, deduplicate on disk, publish shards."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import numpy as np
from gomoku_tools.protocol import Worker
from .config import digest
from .dataset import validate
from .features import canonical_board
from .io import atomic_write, file_sha256, read_json, write_json
from .shards import DATA_FORMAT, POLICY_LIMIT, SAMPLE_DTYPE, ShardDataset


def source_records(path):
    path = Path(path)
    if path.suffix.lower() in (".jsonl", ".ndjson"):
        with path.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                if line.strip():
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError as error:
                        raise ValueError(f"{path}:{number}: {error}") from error
    else:
        value = read_json(path)
        yield from value if isinstance(value, list) else [value]


def teacher_inputs(path):
    path = Path(path).resolve()
    corpus = read_json(path)
    if corpus.get("kind") != "gomoku-teacher-corpus-v1" or not corpus.get("jobs"):
        raise ValueError("Expected a published teacher corpus manifest")
    inputs = []
    names = set()
    for job in corpus["jobs"]:
        if job.get("identity") != corpus.get("identity"):
            raise ValueError("Teacher job identity differs from corpus")
        name = job["annotations"]
        if (
            not re.fullmatch(r"job-[0-9]{5,}\.annotations\.jsonl", name)
            or name in names
        ):
            raise ValueError("Invalid or duplicate teacher annotation filename")
        names.add(name)
        source = path.parent / name
        if file_sha256(source) != job["files"][name]:
            raise ValueError("Teacher annotation checksum mismatch")
        inputs.append(source)
    return inputs


def board_from_moves(size, moves):
    board = np.zeros((size, size), dtype=np.uint8)
    for i, move in enumerate(moves):
        board[move["y"], move["x"]] = i % 2 + 1
    return board


def finite(value, name):
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or abs(value) > np.finfo(np.float32).max
    ):
        raise ValueError(f"{name} must be finite")
    return float(value)


def pack_sample(sample):
    position = sample["position"]
    size, moves = position["size"], position["moves"]
    board = board_from_moves(size, moves)
    turn = len(moves) % 2 + 1
    row = np.zeros((), dtype=SAMPLE_DTYPE)
    row["result"] = -1
    row["teacher_wdl"] = np.nan
    row["teacher_score"] = np.nan
    row["turn"] = turn
    for key, color in (("black", 1), ("white", 2)):
        bits = np.packbits(
            (board.reshape(-1) == color).astype(np.uint8), bitorder="little"
        )
        row[key][: len(bits)] = bits
    result = sample.get("result")
    if result is not None:
        if result not in ("black_win", "white_win", "draw"):
            raise ValueError("Invalid absolute game result")
        winner = {"black_win": 1, "white_win": 2, "draw": 0}[result]
        row["result"] = 1 if winner == 0 else (0 if winner == turn else 2)
    teacher = sample.get("teacher")
    if teacher is not None:
        if not isinstance(teacher.get("id"), str) or not teacher["id"]:
            raise ValueError("Teacher labels require a nonempty teacher id")
        if teacher.get("perspective") != "side_to_move":
            raise ValueError("Teacher labels must use side_to_move perspective")
        if "wdl" in teacher:
            wdl = np.asarray(
                [finite(x, "WDL") for x in teacher["wdl"]], dtype=np.float32
            )
            if wdl.shape != (3,) or (wdl < 0).any() or abs(float(wdl.sum()) - 1) > 1e-5:
                raise ValueError("WDL must be win/draw/loss probabilities summing to 1")
            row["teacher_wdl"] = wdl
        if "score" in teacher:
            score = teacher["score"]
            kinds = {"eval": 1, "mate": 2, "lower_bound": 3, "upper_bound": 4}
            if score.get("kind") not in kinds:
                raise ValueError("Unknown teacher score kind")
            row["teacher_score"] = finite(score["value"], "teacher score")
            row["score_kind"] = kinds[score["kind"]]
    policy = sample.get("policy")
    if policy:
        kinds = {
            "best_move": 1,
            "distribution": 2,
            "derived_distribution": 3,
            "played_move": 4,
        }
        if policy.get("kind") not in kinds:
            raise ValueError("Unknown policy target type")
        if policy["kind"] == "derived_distribution" and not policy.get("transform"):
            raise ValueError("Derived policy needs transform provenance")
        targets = policy["moves"]
        if not 1 <= len(targets) <= POLICY_LIMIT:
            raise ValueError(f"Policy must contain 1..{POLICY_LIMIT} entries")
        if policy["kind"] in ("best_move", "played_move") and len(targets) != 1:
            raise ValueError("Single-move policy must have exactly one entry")
        seen = set()
        total = 0.0
        for i, move in enumerate(targets):
            x, y = move["x"], move["y"]
            if (
                type(x) is not int
                or type(y) is not int
                or not (0 <= x < size and 0 <= y < size)
            ):
                raise ValueError("Policy move outside board")
            point = y * size + x
            if board[y, x] or point in seen:
                raise ValueError("Policy contains occupied or duplicate move")
            probability = finite(move.get("probability", 1.0), "policy probability")
            if probability <= 0:
                raise ValueError("Policy probabilities must be positive")
            row["policy_moves"][i], row["policy_probs"][i] = point, probability
            total += probability
            seen.add(point)
        if abs(total - 1) > 1e-5:
            raise ValueError("Policy probabilities must sum to 1")
        row["policy_count"], row["policy_kind"] = len(targets), kinds[policy["kind"]]
    if (
        row["result"] == -1
        and np.isnan(row["teacher_wdl"]).all()
        and not (row["score_kind"] == 1 or
                 (row["score_kind"] == 2 and row["teacher_score"] != 0))
        and not row["policy_count"]
    ):
        raise ValueError("Sample has no usable supervision")
    return row, board


def samples_from_record(record, worker, min_ply, opening_ply):
    if record.get("format") == "gomoku-record-v1":
        validate(record, worker)
        size, rule, moves = record["size"], record["rule"], record["moves"]
        opening = board_from_moves(size, moves[:opening_ply])
        group = hashlib.sha256(
            canonical_board(opening, rule, min(len(moves), opening_ply) % 2 + 1)
        ).hexdigest()
        for ply in range(min_ply, len(moves)):
            yield {
                "position": {"size": size, "rule": rule, "moves": moves[:ply]},
                "result": record["status"],
                "openingId": group,
                "policy": {"kind": "played_move", "moves": [moves[ply]]},
            }
    elif record.get("format") == "gomoku-sample-v1":
        if record.get("failure"):
            raise ValueError("Failed games cannot supply training labels")
        if not all(
            isinstance(record.get(key), str) and record[key]
            for key in ("gameId", "openingId")
        ):
            raise ValueError("Annotated samples require gameId and openingId")
        state = worker.request("inspect", position=record["position"])
        if state["status"] != "playing":
            raise ValueError("Annotated samples must be nonterminal")
        if len(state["moves"]) >= min_ply:
            yield record
    else:
        raise ValueError("Expected gomoku-record-v1 or gomoku-sample-v1")


def prepare(
    inputs,
    output,
    *,
    size=15,
    rule="freestyle",
    seed=42,
    validation_fraction=0.1,
    test_fraction=0.1,
    min_ply=6,
    opening_ply=6,
    shard_size=32768,
    resume=False,
    teacher_corpus=None,
):
    if size not in (15, 20) or rule not in ("freestyle", "standard"):
        raise ValueError("Unsupported rule or size")
    if not (
        0 < validation_fraction < 1
        and 0 <= test_fraction < 1
        and validation_fraction + test_fraction < 1
    ):
        raise ValueError("Invalid split fractions")
    if min_ply < opening_ply or opening_ply < 1 or shard_size < 1:
        raise ValueError("Require min_ply >= opening_ply >= 1 and positive shard_size")
    if teacher_corpus:
        if inputs:
            raise ValueError("Choose raw inputs or --teacher-corpus, not both")
        inputs = teacher_inputs(teacher_corpus)
    if not inputs:
        raise ValueError("Provide input files or --teacher-corpus")
    inputs = [Path(path).resolve() for path in inputs]
    sources = [{"name": path.name, "sha256": file_sha256(path)} for path in inputs]
    settings = {
        "sources": sources,
        "size": size,
        "rule": rule,
        "seed": seed,
        "validationFraction": validation_fraction,
        "testFraction": test_fraction,
        "minPly": min_ply,
        "openingPly": opening_ply,
        "shardSize": shard_size,
    }
    if teacher_corpus:
        settings["teacherCorpusSha256"] = file_sha256(teacher_corpus)
    identity = digest(settings)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        existing = read_json(manifest_path)
        if resume and existing.get("preparationId") == identity:
            for split in ("train", "validation", "test"):
                ShardDataset(manifest_path, split).close()
            return existing
        raise ValueError(
            "Dataset already published; choose a new output or resume identical inputs"
        )
    staging = output / "preparing.sqlite"
    if staging.exists() and not resume:
        raise ValueError("Incomplete preparation exists; use --resume")
    if not staging.exists() and any(output.iterdir()):
        raise ValueError("Output must be empty for a new dataset")
    connection = sqlite3.connect(staging)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS samples (key BLOB PRIMARY KEY, split TEXT, row BLOB)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS games (game TEXT PRIMARY KEY, opening TEXT)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS teachers (identity TEXT PRIMARY KEY, description TEXT)"
        )
        previous = dict(connection.execute("SELECT key, value FROM meta"))
        if previous and previous.get("identity") != identity:
            raise ValueError("Preparation inputs or settings changed")
        connection.execute(
            "INSERT OR REPLACE INTO meta VALUES ('identity', ?)", (identity,)
        )
        processed = int(previous.get("processed", 0))
        duplicates = int(previous.get("duplicates", 0))
        seen = 0
        with Worker() as worker:
            for source in inputs:
                for record in source_records(source):
                    seen += 1
                    if seen <= processed:
                        continue
                    for sample in samples_from_record(
                        record, worker, min_ply, opening_ply
                    ):
                        position = sample["position"]
                        if (position["size"], position["rule"]) != (size, rule):
                            raise ValueError(
                                "A dataset must contain exactly one rule and board size"
                            )
                        game = sample.get("gameId")
                        if game:
                            group = connection.execute(
                                "SELECT opening FROM games WHERE game=?", (game,)
                            ).fetchone()
                            if group and group[0] != sample["openingId"]:
                                raise ValueError(
                                    "One game cannot belong to different opening groups"
                                )
                            connection.execute(
                                "INSERT OR IGNORE INTO games VALUES (?, ?)",
                                (game, sample["openingId"]),
                            )
                        teacher = sample.get("teacher")
                        if teacher:
                            description = {
                                k: v
                                for k, v in teacher.items()
                                if k not in ("score", "wdl")
                            }
                            connection.execute(
                                "INSERT OR IGNORE INTO teachers VALUES (?, ?)",
                                (
                                    digest(description),
                                    json.dumps(description, allow_nan=False),
                                ),
                            )
                        row, board = pack_sample(sample)
                        key = hashlib.sha256(
                            canonical_board(board, rule, int(row["turn"]))
                        ).digest()
                        value = (
                            int(digest([seed, sample["openingId"]])[:16], 16) / 2**64
                        )
                        split = (
                            "test"
                            if value < test_fraction
                            else (
                                "validation"
                                if value < test_fraction + validation_fraction
                                else "train"
                            )
                        )
                        cursor = connection.execute(
                            "INSERT OR IGNORE INTO samples VALUES (?, ?, ?)",
                            (key, split, row.tobytes()),
                        )
                        duplicates += cursor.rowcount == 0
                    connection.execute(
                        "INSERT OR REPLACE INTO meta VALUES ('processed', ?)",
                        (str(seen),),
                    )
                    connection.execute(
                        "INSERT OR REPLACE INTO meta VALUES ('duplicates', ?)",
                        (str(duplicates),),
                    )
                    if seen % 256 == 0:
                        connection.commit()
        connection.commit()
        shards, counts = [], {}
        for split in ("train", "validation", "test"):
            cursor = connection.execute(
                "SELECT row FROM samples WHERE split=? ORDER BY key", (split,)
            )
            total = number = 0
            while rows := cursor.fetchmany(shard_size):
                array = np.frombuffer(
                    b"".join(row[0] for row in rows), dtype=SAMPLE_DTYPE
                )
                name = f"{split}-{number:05d}.npy"
                path = output / name
                atomic_write(
                    path, lambda stream: np.save(stream, array, allow_pickle=False)
                )
                shards.append(
                    {
                        "file": name,
                        "split": split,
                        "count": len(array),
                        "sha256": file_sha256(path),
                    }
                )
                total, number = total + len(array), number + 1
            counts[split] = total
        if not sum(counts.values()):
            raise ValueError("No training samples survived preparation")
        teachers = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT description FROM teachers ORDER BY identity"
            )
        ]
        manifest = {
            "format": DATA_FORMAT,
            "preparationId": identity,
            "size": size,
            "rule": rule,
            "settings": settings,
            "teachers": teachers,
            "counts": counts,
            "shards": shards,
            "records": seen,
            "duplicatesRemoved": duplicates,
            "deduplication": "d4-first-occurrence",
        }
        write_json(manifest_path, manifest)
    finally:
        connection.close()
    staging.unlink()
    return manifest


def main():
    parser = argparse.ArgumentParser(
        description="Prepare verified, resumable NNUE dataset shards"
    )
    parser.add_argument("inputs", type=Path, nargs="*")
    parser.add_argument(
        "--teacher-corpus", type=Path, help="Verified Rapfi teacher corpus manifest"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size", type=int, choices=[15, 20], default=15)
    parser.add_argument(
        "--rule", choices=["freestyle", "standard"], default="freestyle"
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--validation-fraction", type=float, default=0.1)
    parser.add_argument("--test-fraction", type=float, default=0.1)
    parser.add_argument("--min-ply", type=int, default=6)
    parser.add_argument("--opening-ply", type=int, default=6)
    parser.add_argument("--shard-size", type=int, default=32768)
    parser.add_argument("--resume", action="store_true")
    args = vars(parser.parse_args())
    try:
        manifest = prepare(**args)
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
        parser.exit(2, f"Preparation failed: {error}\n")
    print(
        json.dumps(
            {
                "counts": manifest["counts"],
                "duplicatesRemoved": manifest["duplicatesRemoved"],
            }
        )
    )


if __name__ == "__main__":
    main()
