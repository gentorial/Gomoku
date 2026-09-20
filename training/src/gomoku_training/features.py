"""Executable line encoding: 397488 contiguous bordered ternary patterns."""

from functools import lru_cache
import numpy as np

RADIUS = 5
DIRECTIONS = ((1, 0), (0, 1), (1, 1), (1, -1))
PATTERN_OFFSETS = {}
_count = 0
for _left in range(6):
    for _right in range(6):
        PATTERN_OFFSETS[(_left, _right)] = _count
        _count += 3 ** (_left + _right + 1)
PATTERN_COUNT = _count


@lru_cache(maxsize=4)
def line_geometry(size):
    if size not in (15, 20):
        raise ValueError("Only 15x15 and 20x20 boards are supported")
    # Index size*size denotes the off-board sentinel.
    indices = np.full((4, size * size, 11), size * size, dtype=np.int64)
    bounds = np.zeros((4, size * size, 2), dtype=np.int64)
    for d, (dx, dy) in enumerate(DIRECTIONS):
        for y in range(size):
            for x in range(size):
                valid = []
                for k in range(-5, 6):
                    xx, yy = x + dx * k, y + dy * k
                    if 0 <= xx < size and 0 <= yy < size:
                        indices[d, y * size + x, k + 5] = yy * size + xx
                        valid.append(k)
                bounds[d, y * size + x] = (-min(valid), max(valid))
    indices.setflags(write=False)
    bounds.setflags(write=False)
    return indices, bounds


def pattern_ids(board, perspective):
    size = board.shape[0]
    indices, bounds = line_geometry(size)
    cells = np.concatenate((board.reshape(-1), [0]))
    lines = cells[indices]
    lines = np.where(lines == perspective, 1, np.where(lines == 3 - perspective, 2, 0))
    result = np.empty((4, size * size), dtype=np.int64)
    for d in range(4):
        for point in range(size * size):
            left, right = bounds[d, point]
            digits = lines[d, point, 5 - left : 6 + right]
            result[d, point] = PATTERN_OFFSETS[(left, right)] + int(
                digits @ (3 ** np.arange(len(digits), dtype=np.int64))
            )
    return result


def enumerate_patterns(start, stop):
    """Return own/opponent/off-board planes with shape [patterns, 3, 11]."""
    if not 0 <= start <= stop <= PATTERN_COUNT:
        raise ValueError("Invalid pattern interval")
    result = np.zeros((stop - start, 3, 11), dtype=np.float32)
    result[:, 2, :] = 1
    for (left, right), offset in PATTERN_OFFSETS.items():
        length = left + right + 1
        lo, hi = max(start, offset), min(stop, offset + 3**length)
        if hi <= lo:
            continue
        codes = np.arange(lo - offset, hi - offset, dtype=np.int64)
        digits = (codes[:, None] // (3 ** np.arange(length))) % 3
        rows = slice(lo - start, hi - start)
        columns = slice(5 - left, 6 + right)
        result[rows, 0, columns] = digits == 1
        result[rows, 1, columns] = digits == 2
        result[rows, 2, columns] = 0
    return result


def canonical_board(board, rule, to_move):
    orientations = [np.rot90(board, k) for k in range(4)]
    orientations += [np.fliplr(item) for item in orientations]
    prefix = f"{board.shape[0]}:{rule}:{to_move}:".encode()
    return prefix + min(item.tobytes() for item in orientations)
