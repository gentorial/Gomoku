"""Bounded-memory label and game-phase counts for a published dataset."""

import argparse
from pathlib import Path
import numpy as np
from .io import read_json, write_json, file_sha256


def quality(manifest_path, output):
    manifest_path = Path(manifest_path).resolve()
    manifest = read_json(manifest_path)
    splits = {}
    for split in ("train", "validation", "test"):
        scores = np.zeros(5, dtype=np.int64)
        policies = np.zeros(5, dtype=np.int64)
        outcomes = np.zeros(4, dtype=np.int64)
        phases = np.zeros(5, dtype=np.int64)
        count = 0
        for shard in manifest["shards"]:
            if shard["split"] != split:
                continue
            data = np.load(manifest_path.parent / shard["file"], mmap_mode="r", allow_pickle=False)
            try:
                scores += np.bincount(data["score_kind"], minlength=5)
                policies += np.bincount(data["policy_kind"], minlength=5)
                outcomes += np.bincount(data["result"].astype(np.int64) + 1, minlength=4)
                for start in range(0, len(data), 4096):
                    part = data[start:start+4096]
                    plies = np.unpackbits(part["black"] | part["white"], axis=1, bitorder="little").sum(axis=1)
                    phases += np.bincount(np.searchsorted([16, 32, 64, 96], plies, side="right"), minlength=5)
                count += len(data)
            finally:
                data._mmap.close()
        if count != manifest["counts"][split]:
            raise ValueError("Quality report counts differ from dataset manifest")
        splits[split] = {"positions": count,
                         "teacherScoreKinds": dict(zip(("missing", "eval", "mate", "lower_bound", "upper_bound"), scores.tolist())),
                         "policyKinds": dict(zip(("none", "best_move", "distribution", "derived_distribution", "played_move"), policies.tolist())),
                         "resultsSideToMove": dict(zip(("unknown", "win", "draw", "loss"), outcomes.tolist())),
                         "plies": dict(zip(("0-15", "16-31", "32-63", "64-95", "96+"), phases.tolist()))}
    result = {"kind": "gomoku-dataset-quality-v1", "datasetSha256": file_sha256(manifest_path),
              "uniquePositions": sum(s["positions"] for s in splits.values()), "splits": splits}
    write_json(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    quality(args.manifest, args.output)


if __name__ == "__main__":
    main()
