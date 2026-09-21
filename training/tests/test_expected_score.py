from dataclasses import replace
import unittest
import torch
from gomoku_training.config import Config, LossConfig
from gomoku_training.loss import loss_terms


class ExpectedScoreTests(unittest.TestCase):
    def terms(self, scores, kinds, results=None, teacher_weight=1.0):
        n = len(scores)
        logits = torch.tensor([[0.2, -0.1, 0.4]]*n, requires_grad=True)
        batch = {"result": torch.tensor(results if results is not None else [-1]*n),
                 "teacher_wdl": torch.full((n,3), float("nan")),
                 "teacher_score": torch.tensor(scores), "score_kind": torch.tensor(kinds),
                 "policy_kind": torch.zeros(n,dtype=torch.long), "policy": torch.zeros(n,225)}
        config = LossConfig(value_target="expected_score", teacher_weight=teacher_weight,
                            result_weight=0.1 if results is not None else 0, policy_weight=0,
                            score_scale=400)
        loss, metrics = loss_terms({"value":logits,"policy":torch.zeros(n,225)},batch,config)
        return loss, logits, metrics

    def test_mate_uses_same_bounded_value_scale(self):
        loss, logits, _ = self.terms([29995.0,-29995.0,200.0],[2,2,1])
        p=logits.softmax(1)
        target=torch.tensor([1.0,-1.0,torch.tanh(torch.tensor(.5))])
        torch.testing.assert_close(loss, ((p[:,0]-p[:,2]-target)**2).mean())
        loss.backward()
        self.assertLess(float(logits.grad[0,0]),0)
        self.assertLess(float(logits.grad[1,2]),0)

    def test_later_blunder_does_not_override_teacher_mate(self):
        loss, _, _ = self.terms([29990.0],[2],[2])
        teacher_only, _, _ = self.terms([29990.0],[2])
        torch.testing.assert_close(loss,teacher_only)
        result_only, _, _ = self.terms([29990.0],[2],[2],teacher_weight=0)
        self.assertGreater(float(loss.detach()),float(result_only.detach()))

    def test_invalid_loss_mode_rejected(self):
        with self.assertRaises(ValueError):
            replace(Config(),loss=replace(LossConfig(),value_target="unknown")).validate()


if __name__ == "__main__":
    unittest.main()
