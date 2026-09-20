import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/src"))
from gomoku_tools.protocol import Worker
from gomoku_tools.rapfi import (
    Generation,
    generation_lock,
    run_job,
    sha256,
    snapshot,
    write_json,
)
from gomoku_tools.teacher_data import annotated_game, opening_key, read_games


def encode_game(moves, initial=6, result=2, extra=False, missing=False):
    turns = []
    for i, move in enumerate(moves[initial:]):
        encoded = (move["x"] << 5) | move["y"]
        flags = 1 | (0 if extra and i == 0 else 2) | (4 if missing else 0)
        turns.append(
            struct.pack("<Hh", flags | (encoded << 6), 0 if missing else 300 - i)
        )
        if extra and i == 0:
            turns.append(struct.pack("<Hh", 2 | (((12 << 5) | 12) << 6), -120))
    first = 15 | (result << 8) | (len(moves) << 12) | (initial << 22)
    return (
        struct.pack("<II", first, len(turns) << 14)
        + b"".join(
            struct.pack("<H", (move["x"] << 5) | move["y"]) for move in moves[:initial]
        )
        + b"".join(turns)
    )


class TeacherTests(unittest.TestCase):
    def game(self, blob):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "game.binpack"
            path.write_bytes(blob)
            return list(read_games(path))

    def winning(self):
        fixture = json.loads((ROOT / "tests/fixtures/winning-move.json").read_text())
        return fixture["position"]["moves"] + [fixture["bestMove"]]

    def test_binpack_coordinates_multipv_and_native_result(self):
        game = self.game(encode_game(self.winning(), extra=True))[0]
        self.assertEqual(len(game["turns"][0]), 2)
        self.assertEqual(game["turns"][0][1]["eval"], -120)
        with Worker() as worker:
            samples, record = annotated_game(
                game, worker, {"id": "parser-test-only"}, {}
            )
        self.assertEqual(record["status"], "black_win")
        self.assertTrue(all(s["result"] == "black_win" for s in samples))
        self.assertEqual(samples[-1]["policy"]["moves"], [{"x": 7, "y": 7}])

    def test_result_perspective_after_odd_opening(self):
        moves = [
            {"x": x, "y": y}
            for x, y in [
                (0, 0),
                (3, 7),
                (1, 0),
                (4, 7),
                (2, 0),
                (5, 7),
                (4, 1),
                (6, 7),
                (5, 0),
                (7, 7),
            ]
        ]
        game = self.game(encode_game(moves, initial=7, result=2))[0]
        self.assertEqual(game["reportedResult"], "white_win")
        with Worker() as worker:
            samples, _ = annotated_game(game, worker, {"id": "parser-test-only"}, {})
        self.assertEqual(samples[0]["result"], "white_win")

    def test_false_teacher_terminal_label_is_rejected(self):
        game = self.game(encode_game(self.winning(), result=0))[0]
        with Worker() as worker:
            with self.assertRaisesRegex(ValueError, "disagrees"):
                annotated_game(game, worker, {"id": "parser-test-only"}, {})

    def test_missing_evaluation_and_unverified_result_remain_missing(self):
        game = self.game(encode_game(self.winning()[:-1], missing=True))[0]
        with Worker() as worker:
            samples, record = annotated_game(
                game, worker, {"id": "parser-test-only"}, {}
            )
        self.assertEqual(record["status"], "playing")
        self.assertTrue(
            all(s["result"] is None and "score" not in s["teacher"] for s in samples)
        )
        self.assertFalse(samples[0]["source"]["resultVerified"])

    def test_truncated_reserved_and_invalid_moves_rejected(self):
        valid = encode_game(self.winning())
        variants = [
            valid[:-1],
            valid[:7],
            valid[:8],
            valid[:4] + struct.pack("<I", 1 | (3 << 14)) + valid[8:],
        ]
        bad = bytearray(valid)
        bad[20] |= 8  # first payload move's pass flag
        variants.append(bytes(bad))
        bad = bytearray(valid)
        bad[8:10] = struct.pack("<H", (31 << 5) | 7)
        variants.append(bytes(bad))
        for blob in variants:
            with self.subTest(blob=blob.hex()), self.assertRaises(ValueError):
                self.game(blob)

    def test_opening_group_survives_rotation_and_reflection(self):
        moves = self.winning()[:6]
        transformed = [{"x": move["y"], "y": 14 - move["x"]} for move in moves]
        reflected = [{"x": 14 - move["x"], "y": move["y"]} for move in moves]
        self.assertEqual(
            opening_key(moves, 15, "freestyle"),
            opening_key(transformed, 15, "freestyle"),
        )
        self.assertEqual(
            opening_key(moves, 15, "freestyle"), opening_key(reflected, 15, "freestyle")
        )

    def test_official_teacher_fixture_with_native_rules(self):
        path = ROOT / "tests/fixtures/rapfi-250615.binpack"
        provenance = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(sha256(path), provenance["sha256"])
        games = list(read_games(path))
        self.assertEqual(len(games), 2)
        positions = 0
        with Worker() as worker:
            for game in games:
                records, full_game = annotated_game(
                    game, worker, provenance["teacher"], {}
                )
                self.assertNotEqual(full_game["status"], "playing")
                positions += len(records)
        self.assertEqual(positions, 57)

    def test_resume_verifies_completed_job_without_relaunch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            annotation = root / "job-00000.annotations.jsonl"
            annotation.write_text("fixture", encoding="utf-8")
            saved = {"identity": "same", "files": {annotation.name: sha256(annotation)}}
            write_json(root / "job-00000.json", saved)
            config = Generation(output=str(root))
            self.assertEqual(
                run_job(0, Path("missing.exe"), config, {}, "same", threading.Event()),
                saved,
            )
            annotation.write_text("corrupt", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checksum"):
                run_job(0, Path("missing.exe"), config, {}, "same", threading.Event())

    def test_generation_lock_rejects_second_writer_and_releases(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with generation_lock(root):
                with self.assertRaisesRegex(RuntimeError, "Another collector"):
                    with generation_lock(root):
                        self.fail("Second writer acquired the lock")
            with generation_lock(root):
                pass

    def test_snapshot_only_publishes_verified_completed_jobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            root.mkdir()
            write_json(
                root / "generation.json",
                {
                    "kind": "gomoku-teacher-generation-v1",
                    "identity": "same",
                    "teacher": {"id": "test"},
                    "config": {"jobs": 2},
                },
            )
            annotation = root / "job-00000.annotations.jsonl"
            annotation.write_text("fixture", encoding="utf-8")
            job = {
                "identity": "same",
                "index": 0,
                "annotations": annotation.name,
                "files": {annotation.name: sha256(annotation)},
                "games": 1,
                "positions": 2,
                "terminalGames": 1,
                "ordinaryScores": 1,
                "mateScores": 1,
                "missingScores": 0,
            }
            write_json(root / "job-00000.json", job)
            (root / "job-00001.binpack.partial").write_bytes(b"unfinished")
            output = Path(temporary) / "snapshot"
            result = snapshot(root, output)
            self.assertFalse(result["generationComplete"])
            self.assertEqual(result["counts"]["games"], 1)
            self.assertEqual((output / annotation.name).read_text(), "fixture")
            self.assertFalse((output / "job-00001.binpack.partial").exists())
            with self.assertRaisesRegex(ValueError, "empty"):
                snapshot(root, output)
            annotation.write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checksum"):
                snapshot(root, Path(temporary) / "corrupt")


if __name__ == "__main__":
    unittest.main()
