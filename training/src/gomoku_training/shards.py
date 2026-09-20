"""Bounded-memory, indexed NumPy shards and a resumable block shuffle."""

from bisect import bisect_right
from collections import OrderedDict
from pathlib import Path
import re
import numpy as np
from .io import file_sha256, read_json

DATA_FORMAT = "gomoku-training-shards-v1"
POLICY_LIMIT = 32
SAMPLE_DTYPE = np.dtype(
    [
        ("black", "u1", (50,)),
        ("white", "u1", (50,)),
        ("turn", "u1"),
        ("result", "i1"),
        ("teacher_wdl", "<f4", (3,)),
        ("teacher_score", "<f4"),
        ("score_kind", "u1"),
        ("policy_kind", "u1"),
        ("policy_count", "u1"),
        ("policy_moves", "<u2", (POLICY_LIMIT,)),
        ("policy_probs", "<f4", (POLICY_LIMIT,)),
    ]
)


class ShardDataset:
    def __init__(self, manifest, split, verify=True):
        path = Path(manifest).resolve()
        self.manifest = read_json(path)
        self.digest = file_sha256(path)
        if self.manifest.get("format") != DATA_FORMAT or split not in (
            "train",
            "validation",
            "test",
        ):
            raise ValueError("Unsupported dataset format or split")
        self.size = self.manifest["size"]
        self.rule = self.manifest["rule"]
        if self.size not in (15, 20) or self.rule not in ("freestyle", "standard"):
            raise ValueError("Unsupported dataset rule or size")
        self.paths, self.ends = [], []
        total = 0
        names = set()
        for shard in self.manifest["shards"]:
            name = shard["file"]
            if shard["split"] not in (
                "train",
                "validation",
                "test",
            ) or not re.fullmatch(re.escape(shard["split"]) + r"-[0-9]{5,}\.npy", name):
                raise ValueError("Invalid shard filename")
            if name in names:
                raise ValueError("Duplicate shard filename")
            names.add(name)
            if shard["split"] != split:
                continue
            file = path.parent / name
            if verify and file_sha256(file) != shard["sha256"]:
                raise ValueError(f"Shard checksum mismatch: {name}")
            array = np.load(file, mmap_mode="r", allow_pickle=False)
            try:
                if array.dtype != SAMPLE_DTYPE or array.shape != (shard["count"],):
                    raise ValueError(f"Invalid shard shape or dtype: {name}")
                if shard["count"] <= 0:
                    raise ValueError("Empty shards are not allowed")
            finally:
                array._mmap.close()
            total += shard["count"]
            self.paths.append(file)
            self.ends.append(total)
        self.cache = OrderedDict()
        self.count = total
        if total != self.manifest["counts"][split]:
            raise ValueError("Dataset count does not match manifest")

    def __len__(self):
        return self.count

    def __getitem__(self, index):
        if not 0 <= index < self.count:
            raise IndexError(index)
        shard = bisect_right(self.ends, index)
        if shard not in self.cache:
            self.cache[shard] = np.load(
                self.paths[shard], mmap_mode="r", allow_pickle=False
            )
            if len(self.cache) > 4:
                self.cache.popitem(last=False)[1]._mmap.close()
        self.cache.move_to_end(shard)
        start = self.ends[shard - 1] if shard else 0
        return self.cache[shard][index - start].copy()

    def close(self):
        for array in self.cache.values():
            array._mmap.close()
        self.cache.clear()


class BlockSampler:
    """Memory bounded by block size + number of blocks, independent of epochs."""

    def __init__(self, count, seed, block_size=65536, state=None):
        if count < 1:
            raise ValueError("Training split is empty")
        self.count, self.seed, self.block_size = count, seed, block_size
        self.epoch, self.offset = (
            (0, 0) if state is None else (state["epoch"], state["offset"])
        )
        if self.epoch < 0 or not 0 <= self.offset < count:
            raise ValueError("Invalid sampler cursor")
        self._epoch = -1
        self._block = None

    def state_dict(self):
        return {"epoch": self.epoch, "offset": self.offset}

    def next_indices(self, count):
        result = []
        while len(result) < count:
            if self._epoch != self.epoch:
                blocks = (self.count + self.block_size - 1) // self.block_size
                self.order = np.random.default_rng([self.seed, self.epoch]).permutation(
                    blocks
                )
                self.lengths = np.minimum(
                    self.block_size, self.count - self.order * self.block_size
                )
                self.ends = np.cumsum(self.lengths)
                self._epoch, self._block = self.epoch, None
            block_index = int(np.searchsorted(self.ends, self.offset, side="right"))
            block = int(self.order[block_index])
            if self._block != block:
                rng = np.random.default_rng([self.seed, self.epoch, block, 1])
                self.permutation = (
                    rng.permutation(int(self.lengths[block_index]))
                    + block * self.block_size
                )
                self._block = block
            start = self.offset - (
                int(self.ends[block_index - 1]) if block_index else 0
            )
            take = min(count - len(result), len(self.permutation) - start)
            result.extend(self.permutation[start : start + take].tolist())
            self.offset += take
            if self.offset == self.count:
                self.epoch += 1
                self.offset = 0
        return result


def decode_batch(samples, size, augment_rng=None):
    count = len(samples)
    rows = np.array(samples, dtype=SAMPLE_DTYPE)
    black = np.unpackbits(rows["black"], axis=1, bitorder="little")[:, : size * size]
    white = np.unpackbits(rows["white"], axis=1, bitorder="little")[:, : size * size]
    boards = (black + 2 * white).reshape(count, size, size).astype(np.int64)
    policy = np.zeros((count, size * size), dtype=np.float32)
    for i, row in enumerate(rows):
        n = int(row["policy_count"])
        policy[i, row["policy_moves"][:n]] = row["policy_probs"][:n]
        if augment_rng is not None:
            orientation = int(augment_rng.integers(8))
            board = np.rot90(boards[i], orientation % 4)
            target = np.rot90(policy[i].reshape(size, size), orientation % 4)
            if orientation >= 4:
                board, target = np.fliplr(board), np.fliplr(target)
            boards[i], policy[i] = board.copy(), target.reshape(-1).copy()
    return {
        "boards": boards,
        "turn": rows["turn"].astype(np.int64),
        "result": rows["result"].astype(np.int64),
        "teacher_wdl": rows["teacher_wdl"].copy(),
        "teacher_score": rows["teacher_score"].copy(),
        "score_kind": rows["score_kind"].copy(),
        "policy": policy,
        "policy_kind": rows["policy_kind"].copy(),
    }
