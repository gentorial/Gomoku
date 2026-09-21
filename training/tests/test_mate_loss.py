from dataclasses import replace
import unittest
import torch
from gomoku_training.config import LossConfig
from gomoku_training.loss import effective_labels, loss_terms


class MateLossTests(unittest.TestCase):
    def batch(self, kinds, scores):
        n = len(kinds)
        return {
            "result": torch.full((n,), -1, dtype=torch.long),
            "teacher_wdl": torch.full((n, 3), float("nan")),
            "score_kind": torch.tensor(kinds),
            "teacher_score": torch.tensor(scores),
            "policy_kind": torch.zeros(n, dtype=torch.long),
            "policy": torch.zeros(n, 225),
        }

    def loss(self, batch):
        prediction = {
            "value": torch.zeros(len(batch["result"]), 3, requires_grad=True),
            "policy": torch.zeros(len(batch["result"]), 225, requires_grad=True),
        }
        config = replace(LossConfig(), teacher_weight=1, result_weight=0, policy_weight=0)
        loss, metrics = loss_terms(prediction, batch, config)
        loss.backward()
        self.assertEqual(int(effective_labels(batch, config).sum()), int(metrics["effective"]))
        return prediction["value"].grad, metrics

    def test_mate_only_batch_learns_both_outcomes(self):
        grad, metrics = self.loss(self.batch([2, 2], [29995.0, -29995.0]))
        self.assertEqual(int(metrics["effective"]), 2)
        self.assertEqual(int(metrics["teacherMateCount"]), 2)
        self.assertLess(float(grad[0, 0]), 0)
        self.assertGreater(float(grad[0, 2]), 0)
        self.assertLess(float(grad[1, 2]), 0)
        self.assertGreater(float(grad[1, 0]), 0)
        torch.testing.assert_close(grad[0], grad[1].flip(0))

    def test_unknown_bounds_and_zero_mate_have_no_value_gradient(self):
        grad, metrics = self.loss(self.batch([0, 2, 2, 3, 4], [0.0, 0.0, float("nan"), 30000.0, -30000.0]))
        self.assertEqual(int(metrics["effective"]), 0)
        self.assertEqual(float(grad.abs().sum()), 0)

    def test_explicit_wdl_precedes_mate(self):
        batch = self.batch([2], [29995.0])
        batch["teacher_wdl"][0] = torch.tensor([0.0, 1.0, 0.0])
        grad, metrics = self.loss(batch)
        self.assertLess(float(grad[0, 1]), 0)
        self.assertEqual(int(metrics["teacherMateCount"]), 0)
        self.assertEqual(int(metrics["teacherCount"]), 1)


if __name__ == "__main__":
    unittest.main()
