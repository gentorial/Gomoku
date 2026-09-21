"""Persistent, node-limited Rapfi analysis for positions from our own search.

YXBOARD + YXNBEST are supported by the pinned 250615 release. Only complete
MultiPV iterations are labels: interrupted deeper PVs must not be mixed with
shallower alternatives or with the final protocol move.
"""

from pathlib import Path
import re
import time
from .protocol import EngineProcess


def parse_score(value):
    mate = re.fullmatch(r"([+-])M(\d+|\*)", value)
    if mate:
        distance = 500 if mate[2] == "*" else int(mate[2])
        if not 0 <= distance <= 500:
            raise ValueError("Invalid teacher mate distance")
        return (1 if mate[1] == "+" else -1) * (30000 - distance)
    return int(value)


def parse_iterations(lines, size):
    iterations, current = {}, None
    for line in lines:
        match = re.fullmatch(r"(?:MESSAGE )?INFO (\w+) (.*)", line)
        if not match:
            continue
        key, value = match.groups()
        if key == "PV" and value.isdigit():
            current = {"index": int(value)}
        elif key == "PV" and value == "DONE" and current is not None:
            if all(k in current for k in ("DEPTH", "NUMPV", "EVAL", "BESTLINE")):
                pv = []
                for coordinate in current["BESTLINE"].split():
                    point = re.fullmatch(r"(\d+),(\d+)", coordinate)
                    if not point:
                        raise ValueError("Unsupported teacher PV coordinate")
                    x, y = map(int, point.groups())
                    if not (0 <= x < size and 0 <= y < size):
                        raise ValueError("Teacher PV is outside the board")
                    pv.append({"x": x, "y": y})
                if pv:
                    depth, count = int(current["DEPTH"]), int(current["NUMPV"])
                    score = parse_score(current["EVAL"])
                    if abs(score) > 30000:
                        raise ValueError("Teacher score outside the pinned convention")
                    iterations.setdefault((depth, count), {})[current["index"]] = {
                        "move": pv[0], "eval": score, "pv": pv,
                        "depth": depth, "nodes": int(current.get("TOTALNODES", 0)),
                    }
            current = None
        elif current is not None:
            current[key] = value
    complete = [(depth, count, values) for (depth, count), values in iterations.items()
                if set(values) == set(range(count))]
    if not complete:
        return []
    _, count, values = max(complete, key=lambda item: item[0])
    candidates = [values[i] for i in range(count)]
    if len({(c["move"]["x"], c["move"]["y"]) for c in candidates}) != count:
        raise ValueError("Duplicate teacher MultiPV moves")
    return candidates


class TeacherClient(EngineProcess):
    def __init__(self, executable, *, size=15, rule="freestyle", nodes=200000,
                 multipv=4, timeout=30, hash_mb=64):
        if size not in (15, 20) or rule not in ("freestyle", "standard"):
            raise ValueError("Unsupported teacher board/rule")
        if nodes < 1 or not 1 <= multipv <= 32 or timeout <= 0 or hash_mb < 1:
            raise ValueError("Invalid teacher search budget")
        self.size, self.rule = size, rule
        self.nodes, self.multipv, self.timeout = nodes, multipv, timeout
        executable = Path(executable).resolve()
        super().__init__(executable, ("--force-utf8",), cwd=executable.parent)
        try:
            for line in ("INFO thread_num 1", "INFO usedatabase 0", "INFO pondering 0",
                         f"INFO rule {0 if rule == 'freestyle' else 1}",
                         f"INFO hash_size {hash_mb * 1024}", "INFO show_detail 2",
                         f"INFO timeout_turn {int(timeout * 1000)}",
                         "INFO timeout_match 100000000", f"INFO max_node {nodes}",
                         f"START {size}"):
                self.send(line)
            while True:
                line = self.receive(timeout)
                if line.startswith("ERROR"):
                    raise ValueError(line)
                if line == "OK":
                    break
        except BaseException:
            self.close()
            raise

    def analyze(self, moves):
        self.send("YXHASHCLEAR")
        self.send("INFO time_left 100000000")
        self.send("YXBOARD")
        turn = len(moves) % 2
        for i, move in enumerate(moves):
            self.send(f"{move['x']},{move['y']},{1 if i % 2 == turn else 2}")
        self.send("DONE")
        self.send(f"YXNBEST {self.multipv}")
        started = time.monotonic()
        deadline, lines = started + self.timeout + 5, []
        while True:
            line = self.receive(max(0.001, deadline - time.monotonic()))
            lines.append(line)
            if line.startswith("ERROR"):
                raise ValueError(line)
            move = re.fullmatch(r"(\d+),(\d+)", line)
            if move:
                final = dict(zip(("x", "y"), map(int, move.groups())))
                break
            if time.monotonic() > deadline:
                raise TimeoutError("Teacher exceeded its analysis deadline")
        candidates = parse_iterations(lines, self.size)
        occupied = {(m["x"], m["y"]) for m in moves}
        if (not 0 <= final["x"] < self.size or not 0 <= final["y"] < self.size
                or (final["x"], final["y"]) in occupied):
            raise ValueError("Teacher returned an illegal final move")
        if not candidates:
            # Opening/forced-move shortcuts can omit INFO scores. Keep only the
            # observed move; never manufacture a numeric value label.
            candidates = [{"move": final, "eval": None, "pv": [final], "depth": 0, "nodes": 0}]
        if any((c["move"]["x"], c["move"]["y"]) in occupied for c in candidates):
            raise ValueError("Teacher labeled an occupied move")
        return {"candidates": candidates, "finalMove": final,
                "elapsedMs": round((time.monotonic() - started) * 1000),
                "labelDepth": candidates[0]["depth"],
                "reportedNodes": max(c["nodes"] for c in candidates), "raw": lines}
