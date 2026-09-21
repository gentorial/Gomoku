"""Grow a corpus to a measured D4-deduplicated size, with an on-disk catalog."""

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from gomoku_tools.rapfi import generate, generation_lock, load_config as teacher_config
from .config import digest
from .features import canonical_board
from .io import file_sha256, read_json, write_json
from .prepare import board_from_moves, source_records, teacher_inputs


class Catalog:
    def __init__(self, path, seed=42):
        self.connection = sqlite3.connect(path)
        self.seed = seed
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS sources (path TEXT PRIMARY KEY, sha TEXT, records INTEGER);
            CREATE TABLE IF NOT EXISTS positions (key BLOB PRIMARY KEY, split TEXT);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        """)
        old = self.connection.execute("SELECT value FROM meta WHERE key='seed'").fetchone()
        if old and int(old[0]) != seed:
            raise ValueError("Corpus split seed changed")
        self.connection.execute("INSERT OR IGNORE INTO meta VALUES ('seed', ?)", (str(seed),))

    def add(self, path):
        path = Path(path).resolve()
        checksum = file_sha256(path)
        previous = self.connection.execute("SELECT sha FROM sources WHERE path=?", (str(path),)).fetchone()
        if previous:
            if previous[0] != checksum:
                raise ValueError("A cataloged source was modified")
            return
        records = 0
        # One source is one transaction: a crash cannot mark a partial file done.
        with self.connection:
            for sample in source_records(path):
                if sample.get("format") != "gomoku-sample-v1":
                    raise ValueError("Catalog requires annotated positions")
                position = sample["position"]
                board = board_from_moves(position["size"], position["moves"])
                key = hashlib.sha256(canonical_board(board, position["rule"], len(position["moves"]) % 2 + 1)).digest()
                value = int(digest([self.seed, sample["openingId"]])[:16], 16) / 2**64
                split = "test" if value < .1 else "validation" if value < .2 else "train"
                self.connection.execute("INSERT OR IGNORE INTO positions VALUES (?, ?)", (key, split))
                records += 1
            self.connection.execute("INSERT INTO sources VALUES (?, ?, ?)", (str(path), checksum, records))

    def counts(self):
        counts = dict(self.connection.execute("SELECT split, count(*) FROM positions GROUP BY split"))
        raw = self.connection.execute("SELECT coalesce(sum(records),0) FROM sources").fetchone()[0]
        return {"unique": sum(counts.values()), "splits": counts,
                "records": raw, "duplicates": raw - sum(counts.values())}

    def close(self):
        self.connection.close()


def collect(config_path, output, *, minimum=1_000_000, workers=10, max_batches=20, wait_existing=False):
    if minimum < 1 or workers < 1 or max_batches < 1:
        raise ValueError("Invalid corpus budget")
    executable, template = teacher_config(config_path)
    output = Path(output).resolve()
    base = Path(template.output)
    if not base.name.endswith("-000"):
        raise ValueError("Campaign teacher output must end in -000")
    setup = {"configSha256": file_sha256(config_path), "minimumUnique": minimum,
             "maxBatches": max_batches, "splitSeed": 42, "deduplication": "D4-canonical-board"}
    identity = digest(setup)
    with generation_lock(output):
        setup_path = output / "collection.json"
        if setup_path.exists() and read_json(setup_path)["identity"] != identity:
            raise ValueError("Collection resume settings changed")
        write_json(setup_path, {"identity": identity, **setup})
        catalog = Catalog(output / "catalog.sqlite")
        manifests = []
        try:
            for batch in range(max_batches):
                root = base.with_name(base.name[:-3] + f"{batch:03d}")
                manifest = root / "manifest.json"
                if not manifest.exists():
                    # Only batch zero may have been started manually. A lock
                    # distinguishes active collection from a crashed collector.
                    while True:
                        try:
                            with generation_lock(root):
                                pass
                            break
                        except RuntimeError:
                            if not wait_existing or batch != 0:
                                raise
                            time.sleep(10)
                    if not manifest.exists():
                        config = replace(template, output=str(root), workers=workers)
                        generate(executable, config, resume=(root / "generation.json").exists())
                for source in teacher_inputs(manifest):
                    catalog.add(source)
                manifests.append({"path": str(manifest), "sha256": file_sha256(manifest)})
                counts = catalog.counts()
                result = {"kind": "gomoku-corpus-collection-v1", "identity": identity,
                          "complete": counts["unique"] >= minimum, "minimumUnique": minimum,
                          "corpora": manifests, **counts}
                write_json(output / "status.json", result)
                print(json.dumps({"event": "corpus-batch", "batch": batch, **counts}), flush=True)
                if result["complete"]:
                    write_json(output / "manifest.json", result)
                    return result
            raise ValueError("Corpus exhausted its batch budget before reaching the unique-position target")
        finally:
            catalog.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum", type=int, default=1_000_000)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--max-batches", type=int, default=20)
    parser.add_argument("--wait-existing", action="store_true")
    args = parser.parse_args()
    collect(args.config, args.output, minimum=args.minimum, workers=args.workers,
            max_batches=args.max_batches, wait_existing=args.wait_existing)


if __name__ == "__main__":
    main()
