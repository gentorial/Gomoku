import torch
from torch.nn import functional as F


def loss_terms(prediction, batch, config):
    logits = prediction["value"].float()
    log_prob = F.log_softmax(logits, dim=1)
    prob = log_prob.exp()
    result_mask = batch["result"] >= 0
    result_loss = F.nll_loss(log_prob, batch["result"].clamp_min(0), reduction="none")
    wdl_mask = torch.isfinite(batch["teacher_wdl"]).all(dim=1)
    teacher_ce = -(batch["teacher_wdl"].nan_to_num(0) * log_prob).sum(dim=1)
    scalar_mask = (
        (batch["score_kind"] == 1) & torch.isfinite(batch["teacher_score"]) & ~wdl_mask
    )
    scalar = torch.tanh(batch["teacher_score"].nan_to_num(0) / config.score_scale)
    teacher_mse = (prob[:, 0] - prob[:, 2] - scalar).square()
    teacher_mask = wdl_mask | scalar_mask
    teacher_loss = torch.where(wdl_mask, teacher_ce, teacher_mse)
    weight = config.teacher_weight * teacher_mask + config.result_weight * result_mask
    value_loss = (
        config.teacher_weight * teacher_mask * teacher_loss
        + config.result_weight * result_mask * result_loss
    ) / weight.clamp_min(1e-12)
    policy_mask = batch["policy_kind"] > 0
    policy_ce = -(
        batch["policy"] * F.log_softmax(prediction["policy"].float(), dim=1)
    ).sum(dim=1)
    policy_weight = torch.where(batch["policy_kind"] == 4, 0.25, 1.0)
    per_sample = (
        value_loss + config.policy_weight * policy_weight * policy_mask * policy_ce
    )
    effective = (weight > 0) | (policy_mask & (config.policy_weight > 0))
    loss = per_sample.sum() / effective.sum().clamp_min(1)
    policy_target = batch["policy"].argmax(1)
    top_moves = prediction["policy"].detach().topk(5, dim=1).indices
    return loss, {
        "lossSum": per_sample.detach().sum(),
        "effective": effective.sum(),
        "valueLossSum": value_loss.detach().sum(),
        "valueCount": (weight > 0).sum(),
        "policyLossSum": (policy_ce.detach() * policy_mask).sum(),
        "policyCount": policy_mask.sum(),
        "teacherCount": teacher_mask.sum(),
        "teacherScalarSquaredError": (teacher_mse.detach() * scalar_mask).sum(),
        "teacherScalarCount": scalar_mask.sum(),
        "policyCorrect": ((top_moves[:, 0] == policy_target) & policy_mask).sum(),
        "policyTop5Correct": (
            (top_moves == policy_target[:, None]).any(1) & policy_mask
        ).sum(),
        "resultCount": result_mask.sum(),
        "resultCorrect": ((logits.argmax(1) == batch["result"]) & result_mask).sum(),
    }


def summarize(metrics):
    return {
        "loss": metrics["lossSum"] / max(metrics["effective"], 1),
        "valueLoss": metrics["valueLossSum"] / max(metrics["valueCount"], 1),
        "policyLoss": metrics["policyLossSum"] / max(metrics["policyCount"], 1),
        "resultAccuracy": metrics["resultCorrect"] / max(metrics["resultCount"], 1),
        "teacherScalarMse": metrics["teacherScalarSquaredError"]
        / metrics["teacherScalarCount"]
        if metrics["teacherScalarCount"]
        else None,
        "policyTop1Agreement": metrics["policyCorrect"]
        / max(metrics["policyCount"], 1),
        "policyTop5Agreement": metrics["policyTop5Correct"]
        / max(metrics["policyCount"], 1),
        "teacherCount": int(metrics["teacherCount"]),
        "resultCount": int(metrics["resultCount"]),
        "policyCount": int(metrics["policyCount"]),
        "effective": int(metrics["effective"]),
    }
