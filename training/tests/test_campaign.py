import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from gomoku_training.campaign import promotion_gate
from gomoku_training.corpus import Catalog
from gomoku_training.prepare import pack_sample
from gomoku_training.shards import decode_batch


class CampaignTests(unittest.TestCase):
    def test_d4_catalog_counts_unique_positions_and_detects_changed_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            p = {"format": "gomoku-sample-v1", "openingId": "one", "gameId": "one",
                 "position": {"size": 15, "rule": "freestyle", "moves": [{"x": 1, "y": 2}, {"x": 3, "y": 4}]}}
            q = json.loads(json.dumps(p))
            q["position"]["moves"] = [{"x": 13, "y": 12}, {"x": 11, "y": 10}]
            source = root / "samples.jsonl"
            source.write_text('\n'.join(map(json.dumps, (p, q))), encoding="utf-8")
            catalog = Catalog(root / "catalog.sqlite")
            try:
                catalog.add(source)
                catalog.add(source)
                self.assertEqual(catalog.counts()["unique"], 1)
                self.assertEqual(catalog.counts()["duplicates"], 1)
                source.write_text(json.dumps(p), encoding="utf-8")
                with self.assertRaises(ValueError):
                    catalog.add(source)
            finally:
                catalog.close()

    def test_equal_soft_targets_preserve_teacher_best_through_augmentation(self):
        sample = {"position": {"size": 15, "moves": []},
                  "policy": {"kind": "derived_distribution", "transform": {"name": "test"},
                             "moves": [{"x": 12, "y": 10, "probability": .5},
                                       {"x": 1, "y": 2, "probability": .5}]}}
        row, _ = pack_sample(sample)
        for orientation in range(8):
            class Fixed:
                def integers(self, _):
                    return orientation
            batch = decode_batch([row], 15, Fixed())
            target = np.zeros((15, 15), np.uint8)
            target[10, 12] = 1
            target = np.rot90(target, orientation % 4)
            if orientation >= 4:
                target = np.fliplr(target)
            self.assertEqual(int(batch["policy_best"][0]), int(target.argmax()))

    def test_promotion_requires_complete_failure_free_three_second_pairs(self):
        games = [{"openingId": str(i), "aColor": color, "scoreA": 1, "failure": None}
                 for i in range(32) for color in ("black", "white")]
        report = {"limits": {"timeMs": 3000}, "games": games}
        self.assertTrue(promotion_gate(report)["passed"])
        report["games"] = games[:-1]
        self.assertFalse(promotion_gate(report)["passed"])
        report["games"] = games
        report["limits"]["timeMs"] = 300
        self.assertFalse(promotion_gate(report)["passed"])
        report["limits"]["timeMs"] = 3000
        games[0]["failure"] = "timeout"
        self.assertFalse(promotion_gate(report)["passed"])


if __name__ == "__main__":
    unittest.main()
