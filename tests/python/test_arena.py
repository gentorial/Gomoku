import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "src"))

from gomoku_tools.arena import worker_match
from gomoku_tools.protocol import Worker, binary


class ArenaTests(unittest.TestCase):
    def test_fixed_opening_swaps_colors_and_records_analysis(self):
        fixture = json.loads((ROOT / "tests/fixtures/winning-move.json").read_text())
        moves = fixture["position"]["moves"]
        results = worker_match(binary(), binary(), [{"id": "win", "moves": moves}], time_ms=100)
        self.assertEqual([game["aColor"] for game in results], ["black", "white"])
        self.assertEqual(sum(game["scoreA"] for game in results), 1)
        for game in results:
            self.assertIsNone(game["failure"])
            self.assertEqual(game["moves"][:len(moves)], moves)
            self.assertEqual(game["status"], fixture["statusAfterMove"])
            self.assertEqual(game["analyses"][0]["ply"], len(moves))
            self.assertEqual(game["moves"][-1], fixture["bestMove"])

    def test_invalid_opening_is_not_scored_as_an_engine_loss(self):
        with self.assertRaises(ValueError):
            worker_match(binary(), binary(), [{"id": "bad", "moves": [{"x": 7, "y": 7}] * 2}])

    def test_resume_only_plays_the_missing_color(self):
        fixture = json.loads((ROOT / "tests/fixtures/winning-move.json").read_text())
        openings = [{"id": "win", "moves": fixture["position"]["moves"]}]
        games = worker_match(binary(), binary(), openings, time_ms=100)
        updates = []
        resumed = worker_match(binary(), binary(), openings, time_ms=100,
                               completed=games[:1], on_game=lambda r: updates.append(len(r)))
        self.assertEqual(updates, [2])
        self.assertEqual([g["aColor"] for g in resumed], ["black", "white"])
        self.assertEqual(resumed[0], games[0])
        resumed = worker_match(binary(), binary(), openings, time_ms=100, completed=games[1:])
        self.assertEqual(resumed[1], games[1])

    def test_parallel_games_overlap_and_publish_each_result_once(self):
        fixture = json.loads((ROOT / "tests/fixtures/winning-move.json").read_text())
        openings = [{"id": str(i), "moves": fixture["position"]["moves"]} for i in range(2)]
        barrier, threads, updates = threading.Barrier(2), set(), []

        class OverlappingWorker(Worker):
            def request(self, method, **params):
                if method == "analyze":
                    threads.add(threading.get_ident())
                    # A serial scheduler cannot pass this barrier. Each forced-win game lasts one move.
                    barrier.wait(timeout=5)
                return super().request(method, **params)

        def save(games):
            keys = [(g["openingId"], g["aColor"]) for g in games]
            self.assertEqual(len(keys), len(set(keys)))
            updates.append(len(games))

        with patch("gomoku_tools.arena.Worker", OverlappingWorker):
            games = worker_match(binary(), binary(), openings, time_ms=100, workers=2, on_game=save)
        self.assertEqual(len(threads), 2)
        self.assertEqual(updates, [1, 2, 3, 4])
        self.assertEqual([(g["openingId"], g["aColor"]) for g in games],
                         [(str(i), c) for i in range(2) for c in ("black", "white")])
        self.assertEqual(sum(g["scoreA"] for g in games), 2)
        self.assertTrue(all(g["failure"] is None and g["arenaWorkers"] == 2 for g in games))

    def test_parallel_resume_preserves_out_of_order_completed_games(self):
        fixture = json.loads((ROOT / "tests/fixtures/winning-move.json").read_text())
        openings = [{"id": str(i), "moves": fixture["position"]["moves"]} for i in range(3)]
        games = worker_match(binary(), binary(), openings, time_ms=100, workers=2)
        completed, updates = [games[5], games[0]], []
        resumed = worker_match(binary(), binary(), openings, time_ms=100, workers=2,
                               completed=completed, on_game=lambda r: updates.append(len(r)))
        self.assertEqual(updates, [3, 4, 5, 6])
        self.assertEqual(resumed[0], games[0])
        self.assertEqual(resumed[5], games[5])
        for bad in ([games[0], games[0]], [{**games[0], "openingId": "unknown"}]):
            with self.assertRaises(ValueError):
                worker_match(binary(), binary(), openings, workers=2, completed=bad)
        with self.assertRaises(ValueError):
            worker_match(binary(), binary(), openings, workers=0)

    def test_parallel_stops_on_report_write_failure(self):
        fixture = json.loads((ROOT / "tests/fixtures/winning-move.json").read_text())
        openings = [{"id": str(i), "moves": fixture["position"]["moves"]} for i in range(4)]

        def fail(_):
            raise OSError("report write failed")

        with self.assertRaisesRegex(OSError, "report write failed"):
            worker_match(binary(), binary(), openings, time_ms=100, workers=2, on_game=fail)


if __name__ == "__main__":
    unittest.main()
