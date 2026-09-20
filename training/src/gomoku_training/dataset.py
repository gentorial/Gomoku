import argparse
import json
from pathlib import Path
from gomoku_tools.protocol import Worker

FEATURE_VERSION = "relative-occupancy-v1"


def read_records(path):
    text = Path(path).read_text(encoding="utf-8")
    try:
        value = json.loads(text)
        records = value if isinstance(value, list) else [value]
    except json.JSONDecodeError:
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
    for record in records:
        if record.get("format") != "gomoku-record-v1":
            raise ValueError("Unsupported record format")
        yield record


def validate(record, worker):
    if record.get("failure"):
        raise ValueError("Forfeit games are not training examples")
    position = {key: record[key] for key in ("size", "rule", "moves")}
    state = worker.request("inspect", position=position)
    if state["status"] != record.get("status"):
        raise ValueError("Recorded outcome does not match the native rules")
    if state["status"] == "playing":
        raise ValueError("Training requires a completed game")


def encode(size, moves):
    # Two binary occupancy planes, relative to the side to move.
    features = [0.0] * (2 * size * size)
    own = len(moves) % 2
    for index, move in enumerate(moves):
        plane = 0 if index % 2 == own else 1
        features[plane * size * size + move["y"] * size + move["x"]] = 1.0
    return features


def examples(record):
    winner = {"black_win": 0, "white_win": 1, "draw": None}[record["status"]]
    for ply in range(len(record["moves"])):
        target = 0.0 if winner is None else (1.0 if ply % 2 == winner else -1.0)
        yield encode(record["size"], record["moves"][:ply]), target


def main():
    parser = argparse.ArgumentParser(
        description="Validate game records with the native rule engine"
    )
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    count = positions = 0
    with Worker() as worker:
        for record in read_records(args.input):
            validate(record, worker)
            count += 1
            positions += len(record["moves"])
    print(
        json.dumps(
            {"games": count, "positions": positions, "featureVersion": FEATURE_VERSION}
        )
    )


if __name__ == "__main__":
    main()
