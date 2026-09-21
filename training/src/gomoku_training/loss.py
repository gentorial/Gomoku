import torch
from torch.nn import functional as F


def teacher_masks(batch):
    """Exact scalar scores and signed mates are distinct, side-to-move labels.

    Explicit WDL takes precedence. Bounds, missing scores and zero mates do not
    imply a known outcome and must not silently become win/loss targets.
    """
    wdl = torch.isfinite(batch["teacher_wdl"]).all(dim=1)
    finite = torch.isfinite(batch["teacher_score"]) & ~wdl
    scalar = (batch["score_kind"] == 1) & finite
    mate = (batch["score_kind"] == 2) & finite & (batch["teacher_score"] != 0)
    return wdl, scalar, mate


def effective_labels(batch, config):
    wdl, scalar, mate = teacher_masks(batch)
    return (
        ((wdl | scalar | mate) & (config.teacher_weight > 0))
        | ((batch["result"] >= 0) & (config.result_weight > 0))
        | ((batch["policy_kind"] > 0) & (config.policy_weight > 0))
    )


def loss_terms(prediction, batch, config):
    logits = prediction["value"].float()
    log_prob = F.log_softmax(logits, dim=1)
    prob = log_prob.exp()
    result_mask = batch["result"] >= 0
    result_loss = F.nll_loss(log_prob, batch["result"].clamp_min(0), reduction="none")
    wdl_mask, scalar_mask, mate_mask = teacher_masks(batch)
    teacher_ce = -(batch["teacher_wdl"].nan_to_num(0) * log_prob).sum(dim=1)
    mate_target = torch.where(batch["teacher_score"] > 0, 0, 2)
    mate_ce = F.nll_loss(log_prob, mate_target, reduction="none")
    scalar = torch.tanh(batch["teacher_score"].nan_to_num(0) / config.score_scale)
    teacher_mse = (prob[:, 0] - prob[:, 2] - scalar).square()
    teacher_mask = wdl_mask | scalar_mask | mate_mask
    teacher_loss = torch.where(wdl_mask, teacher_ce, torch.where(mate_mask, mate_ce, teacher_mse))
    expected = prob[:, 0] - prob[:, 2]
    mate_mse = (expected - batch["teacher_score"].nan_to_num(0).sign()).square()
    if config.value_target == "expected_score":
        # One value scale for all labels. A teacher mate must not be diluted by
        # mistakes later in the recorded game. Legacy checkpoints stay readable.
        wdl = batch["teacher_wdl"].nan_to_num(0)
        wdl_mse = (expected - (wdl[:, 0] - wdl[:, 2])).square()
        teacher_loss = torch.where(wdl_mask, wdl_mse, torch.where(mate_mask, mate_mse, teacher_mse))
        result_loss = (expected - (1 - batch["result"].float())).square()
        result_mask = result_mask & ~(mate_mask & (config.teacher_weight > 0))
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
    effective = effective_labels(batch, config)
    loss = per_sample.sum() / effective.sum().clamp_min(1)
    policy_target = batch.get("policy_best", batch["policy"].argmax(1))
    top_moves = prediction["policy"].detach().topk(5, dim=1).indices
    # The quiet search retains the first 16 points within distance two. Measure
    # coverage of that actual geometric pool, not just global top-1 agreement.
    candidate_correct = torch.zeros_like(policy_mask)
    candidate_count = torch.zeros_like(policy_mask)
    if "boards" in batch:
        occupied = (batch["boards"] != 0)
        near = F.max_pool2d(occupied.float().unsqueeze(1), 5, 1, 2).squeeze(1) > 0
        candidates = (near & ~occupied).flatten(1)
        center = batch["boards"].shape[-1]//2
        candidates[:, center*batch["boards"].shape[-1]+center] |= ~occupied.flatten(1).any(1)
        candidate_count = policy_mask & candidates.gather(1, policy_target[:, None]).squeeze(1)
        ranked = prediction["policy"].detach().masked_fill(~candidates, -float("inf")).topk(16, dim=1).indices
        candidate_correct = (ranked == policy_target[:, None]).any(1) & candidate_count
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
        "teacherMateCount": mate_mask.sum(),
        "teacherMateLossSum": (mate_ce.detach() * mate_mask).sum(),
        "teacherMateCorrect": ((logits.argmax(1) == mate_target) & mate_mask).sum(),
        "teacherMateSquaredError": (mate_mse.detach() * mate_mask).sum(),
        "policyCandidateEligible": candidate_count.sum(),
        "policyCandidateTop16Correct": candidate_correct.sum(),
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
        "teacherMateCount": int(metrics["teacherMateCount"]),
        "teacherMateLoss": metrics["teacherMateLossSum"] / metrics["teacherMateCount"]
        if metrics["teacherMateCount"] else None,
        "teacherMateAccuracy": metrics["teacherMateCorrect"] / metrics["teacherMateCount"]
        if metrics["teacherMateCount"] else None,
        "teacherMateMse": metrics["teacherMateSquaredError"] / metrics["teacherMateCount"]
        if metrics["teacherMateCount"] else None,
        "policyCandidateEligibility": metrics["policyCandidateEligible"] / max(metrics["policyCount"], 1),
        "policyCandidateTop16Recall": metrics["policyCandidateTop16Correct"] / max(metrics["policyCount"], 1),
        "resultCount": int(metrics["resultCount"]),
        "policyCount": int(metrics["policyCount"]),
        "effective": int(metrics["effective"]),
    }
