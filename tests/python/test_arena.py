import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "src"))

from gomoku_tools.arena import worker_match
from gomoku_tools.protocol import binary


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


if __name__ == "__main__":
    unittest.main()
