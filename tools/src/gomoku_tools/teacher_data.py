"""Read Rapfi 250615/c-gomoku-cli's little-endian binpack wire format.

The format has no magic/version header: callers must explicitly select it.
Passes, unknown tags, and unsupported rules are rejected, never guessed.
"""

import hashlib
import math
from pathlib import Path
import struct

FORMAT = "rapfi-binpack-250615"
HEADER = struct.Struct("<II")
MOVE = struct.Struct("<Hh")
MATE_THRESHOLD = 29500


def exact(stream, count):
    value = stream.read(count)
    if len(value) != count:
        raise ValueError(f"Truncated binpack at byte {stream.tell()}")
    return value


def coordinate(encoded, size):
    x, y = encoded >> 5, encoded & 31
    if not 0 <= x < size or not 0 <= y < size:
        raise ValueError("Binpack move outside board")
    return {"x": x, "y": y}


def read_games(path):
    """Yield complete games with raw scores. Memory is bounded by one game."""
    with Path(path).open("rb") as stream:
        while head := stream.read(HEADER.size):
            if len(head) != HEADER.size:
                raise ValueError("Truncated binpack header")
            a, b = HEADER.unpack(head)
            size, rule_id, result = a & 31, (a >> 5) & 7, (a >> 8) & 15
            total, initial = (a >> 12) & 1023, a >> 22
            tag, count = b & 16383, b >> 14
            if (
                size not in (15, 20)
                or rule_id not in (0, 1)
                or result not in (0, 1, 2, 3)
            ):
                raise ValueError("Unsupported binpack size, rule or result")
            if (
                tag
                or not 0 <= initial < total <= size * size
                or not 1 <= count <= size * size * 32
            ):
                raise ValueError("Unsupported tag or invalid binpack length")
            opening_bytes = exact(stream, 2 * initial)
            opening = [
                coordinate(x[0], size) for x in struct.iter_unpack("<H", opening_bytes)
            ]
            payload = exact(stream, MOVE.size * count)
            groups, group = [], []
            for bits, score in MOVE.iter_unpack(payload):
                first, last, missing = bool(bits & 1), bool(bits & 2), bool(bits & 4)
                if bits & 56:  # pass and reserved bits
                    raise ValueError("Binpack passes/reserved flags are unsupported")
                if first != (len(group) == 0):
                    raise ValueError("Broken binpack MultiPV boundaries")
                if not missing and abs(score) > 30000:
                    raise ValueError("Invalid binpack evaluation")
                move = coordinate(bits >> 6, size)
                group.append({"move": move, "eval": None if missing else score})
                if len(group) > 32:
                    raise ValueError("Too many MultiPV entries")
                if last:
                    groups.append(group)
                    group = []
            if group or initial + len(groups) != total:
                raise ValueError("Incomplete binpack game")
            occupied = {(m["x"], m["y"]) for m in opening}
            if len(occupied) != initial:
                raise ValueError("Duplicate opening move")
            for candidates in groups:
                points = [
                    (entry["move"]["x"], entry["move"]["y"]) for entry in candidates
                ]
                if len(set(points)) != len(points) or any(
                    p in occupied for p in points
                ):
                    raise ValueError("Illegal/duplicate MultiPV move")
                occupied.add(points[0])
            # Header result is relative to the side to move after the opening.
            start = initial % 2
            absolute = (
                None
                if result == 3
                else "draw"
                if result == 1
                else ("black_win" if (start == 0) == (result == 2) else "white_win")
            )
            yield {
                "id": hashlib.sha256(head + opening_bytes + payload).hexdigest(),
                "size": size,
                "rule": ("freestyle", "standard")[rule_id],
                "opening": opening,
                "turns": groups,
                "reportedResult": absolute,
            }


def opening_key(moves, size, rule, plies=6):
    """D4-equivalent six-stone openings always share the same split group."""
    positions = moves[:plies]
    variants = []
    for reflected in (False, True):
        for rotations in range(4):
            cells = bytearray(size * size)
            for i, move in enumerate(positions):
                x, y = move["x"], move["y"]
                if reflected:
                    x = size - 1 - x
                for _ in range(rotations):
                    x, y = size - 1 - y, x
                cells[y * size + x] = i % 2 + 1
            variants.append(bytes(cells))
    return hashlib.sha256(
        f"{rule}:{size}:{len(positions) % 2}:".encode() + min(variants)
    ).hexdigest()


def multipv_policy(candidates, temperature=200.0):
    """A score-derived target, never a fabricated visit-count distribution."""
    if not math.isfinite(temperature) or temperature <= 0:
        raise ValueError("Policy temperature must be positive and finite")
    scored = [item for item in candidates if item["eval"] is not None]
    winners = [item for item in scored if item["eval"] >= MATE_THRESHOLD]
    ordinary = [item for item in scored if abs(item["eval"]) < MATE_THRESHOLD]
    selected = winners or ordinary
    if len(candidates) == 1 or not selected or candidates[0] not in selected:
        return {"kind": "best_move", "moves": [candidates[0]["move"]]}
    best = max(item["eval"] for item in selected)
    weights = [1.0 if winners else math.exp(max(-20.0, (item["eval"]-best)/temperature))
               for item in selected]
    total = sum(weights)
    return {"kind": "derived_distribution",
            "transform": {"name": "rapfi-multipv-score-softmax-v1", "temperature": temperature,
                          "matePolicy": "uniform-proven-wins; otherwise ordinary-only; losing-mates best-move"},
            "moves": [{**item["move"], "probability": weight/total}
                      for item, weight in zip(selected, weights)]}


def sampled_indices(game, limit):
    """Deterministic coverage of game phases, plus changes of tactical status."""
    turns = game["turns"]
    if not limit or len(turns) <= limit:
        return range(len(turns))
    # Keep an even spread; reserve a quarter for transitions to/from mate.
    transitions = []
    for i in range(1, len(turns)):
        before, after = turns[i-1][0]["eval"], turns[i][0]["eval"]
        if before is not None and after is not None and (
            (abs(before) >= MATE_THRESHOLD) != (abs(after) >= MATE_THRESHOLD)
        ):
            transitions.extend(range(max(0, i-2), min(len(turns), i+2)))
    selected = set(sorted(set(transitions))[:limit//4])
    remaining = limit-len(selected)
    available = [i for i in range(len(turns)) if i not in selected]
    selected.update(available[(i*(len(available)-1))//max(remaining-1, 1)] for i in range(remaining))
    return sorted(selected)


def annotated_game(game, worker, teacher, source, samples_per_game=0, policy_temperature=200.0):
    """Only native-verified terminal outcomes become result supervision."""
    moves = game["opening"] + [turn[0]["move"] for turn in game["turns"]]
    position = {"size": game["size"], "rule": game["rule"], "moves": moves}
    state = worker.request("inspect", position=position)
    result = None if state["status"] == "playing" else state["status"]
    if (
        result is not None
        and game["reportedResult"] is not None
        and result != game["reportedResult"]
    ):
        raise ValueError("Teacher result disagrees with native rules")
    opening = opening_key(moves, game["size"], game["rule"])
    records = []
    for i in sampled_indices(game, samples_per_game):
        candidates = game["turns"][i]
        ply = len(game["opening"]) + i
        score = candidates[0]["eval"]
        label = {**teacher, "perspective": "side_to_move"}
        if score is not None:
            label["score"] = {
                "kind": "mate" if abs(score) >= MATE_THRESHOLD else "eval",
                "value": score,
            }
        records.append(
            {
                "format": "gomoku-sample-v1",
                "gameId": game["id"],
                "openingId": opening,
                "position": {**position, "moves": moves[:ply]},
                "result": result,
                "teacher": label,
                "policy": multipv_policy(candidates, policy_temperature),
                "source": {
                    **source,
                    "format": FORMAT,
                    "reportedResult": game["reportedResult"],
                    "resultVerified": result is not None,
                },
                "rawMultiPV": candidates,
            }
        )
    return records, {
        "format": "gomoku-record-v1",
        **position,
        "status": state["status"],
        "source": {
            **source,
            "teacherId": teacher["id"],
            "reportedResult": game["reportedResult"],
        },
    }
