import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "src"))

from gomoku_tools.arena import sprt, worker_match
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

    def test_node_budget_bounds_every_search_and_is_reproducible(self):
        fixture = json.loads((ROOT / "tests/fixtures/winning-move.json").read_text())
        opening = [{"id": "open", "moves": fixture["position"]["moves"][:4]}]
        runs = [worker_match(binary(), binary(), opening, time_ms=10000, depth=12, max_nodes=300)
                for _ in range(2)]
        for game in runs[0]:
            self.assertTrue(game["analyses"])
            for analysis in game["analyses"]:
                # The node check runs per searched node, so a budget may overshoot by one node.
                self.assertLessEqual(analysis["nodes"], 301)
        self.assertEqual([g["moves"] for g in runs[0]], [g["moves"] for g in runs[1]])
        # A larger budget searches deeper on the same first position, so nodes are the binding limit.
        wide = worker_match(binary(), binary(), opening, time_ms=10000, depth=12, max_nodes=30000)
        self.assertGreater(wide[0]["analyses"][0]["depth"], runs[0][0]["analyses"][0]["depth"])
        for nodes in (0, 10_000_001, 1.5):
            with self.assertRaises(ValueError):
                worker_match(binary(), binary(), opening, max_nodes=nodes)

    def test_sprt_decides_clear_results_and_waits_on_small_samples(self):
        def games(pair_scores):
            return [{"openingId": str(i), "aColor": color, "scoreA": score}
                    for i, pair in enumerate(pair_scores) for color, score in zip(("black", "white"), pair)]
        self.assertIsNone(sprt(games([(1, 1)] * 3), 0, 10)["decision"])
        strong = sprt(games([(1, 1)] * 40), 0, 10)
        self.assertEqual((strong["decision"], strong["pentanomial"]), ("H1", [0, 0, 0, 0, 40]))
        even = sprt(games([(1, 0), (0, 1), (1, 1), (0, 0)] * 100), 0, 50)
        self.assertEqual(even["decision"], "H0")
        self.assertAlmostEqual(even["score"], 0.5)
        self.assertLess(even["eloInterval95"][0], 0)
        self.assertGreater(even["eloInterval95"][1], 0)
        # An incomplete pair carries no pentanomial information yet.
        self.assertEqual(sprt(games([(1, 1)])[:1], 0, 10)["pairs"], 0)
        for hypotheses in ((10, 0), (0, 10, 0.6, 0.05)):
            with self.assertRaises(ValueError):
                sprt([], *hypotheses)

    def test_stop_condition_finishes_started_pairs_and_skips_new_openings(self):
        fixture = json.loads((ROOT / "tests/fixtures/winning-move.json").read_text())
        openings = [{"id": str(i), "moves": fixture["position"]["moves"]} for i in range(4)]
        games = worker_match(binary(), binary(), openings, time_ms=100, stop_when=lambda played: len(played) >= 1)
        self.assertEqual([(g["openingId"], g["aColor"]) for g in games], [("0", "black"), ("0", "white")])
        resumed = worker_match(binary(), binary(), openings, time_ms=100, completed=games,
                               stop_when=lambda played: True)
        self.assertEqual(resumed, games)

    def test_search_overrides_reach_each_engine(self):
        fixture = json.loads((ROOT / "tests/fixtures/winning-move.json").read_text())
        opening = [{"id": "open", "moves": fixture["position"]["moves"][:4]}]
        # The same binary with different overrides plays complete games.
        games = worker_match(binary(), binary(), opening, time_ms=10000, depth=12, max_nodes=3000,
                             search_a=["lmr=0", "threat_filter=0"], search_b=["lmr_divisor=1.5"])
        self.assertTrue(all(g["failure"] is None for g in games))
        # A rejected option stops the worker before it accepts any request.
        with self.assertRaises((RuntimeError, TimeoutError, OSError, ValueError)):
            with Worker(binary(), search=["lmr_divisor=0"]) as worker:
                worker.request("about")

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
