"""Record held-out learning metrics and export parity, without claiming strength."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import numpy as np
from .config import ModelConfig, from_dict
from .device import torch
from .evaluate import evaluate
from .export import QuantizedReference
from .io import file_sha256, read_json, write_json
from .model import LineNNUE
from .shards import ShardDataset, decode_batch
from .train import load_checkpoint


def reference_check(checkpoint, export_manifest, dataset_manifest, positions=32):
    if positions < 1:
        raise ValueError("Reference position count must be positive")
    state = load_checkpoint(checkpoint)
    model = LineNNUE(ModelConfig(**state["modelConfig"])).eval()
    model.load_state_dict(state["model"])
    reference = QuantizedReference(export_manifest)
    dataset = None
    try:
        if reference.manifest["checkpointSha256"] != file_sha256(checkpoint):
            raise ValueError("Export was not produced from this checkpoint")
        dataset = ShardDataset(dataset_manifest, "test")
        if not len(dataset) or (dataset.size, dataset.rule) != (
            state["size"],
            state["rule"],
        ):
            raise ValueError("Need a nonempty test split matching the checkpoint")
        indices = np.linspace(
            0, len(dataset) - 1, min(positions, len(dataset)), dtype=int
        )
        errors = {"value": 0.0, "policy": 0.0}
        vectors = []
        for index in indices:
            batch = decode_batch([dataset[int(index)]], dataset.size)
            board, turn = batch["boards"][0], int(batch["turn"][0])
            with torch.no_grad():
                predicted = model(
                    torch.from_numpy(batch["boards"]), torch.from_numpy(batch["turn"])
                )
            actual = reference.predict(board, turn)
            for name in errors:
                expected = predicted[name][0].numpy()
                errors[name] = max(
                    errors[name], float(np.abs(expected - actual[name]).max())
                )
            vectors.append(
                {
                    "testIndex": int(index),
                    "board": board.tolist(),
                    "toMove": turn,
                    "valueLogits": actual["value"].tolist(),
                    "policyLogits": actual["policy"].tolist(),
                }
            )
        tolerance = 2e-5
        return {
            "positions": len(indices),
            "maximumAbsoluteError": errors,
            "tolerance": tolerance,
            "passed": max(errors.values()) <= tolerance,
        }, vectors
    finally:
        if dataset is not None:
            dataset.close()
        reference.close()


def assess(
    checkpoint, baseline, manifest, export_manifest, output, device="auto", positions=32
):
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Assessment output must be empty")
    output.mkdir(parents=True, exist_ok=True)
    state = load_checkpoint(checkpoint)
    data = read_json(manifest)
    exported = read_json(export_manifest)
    comparison_loss = from_dict(state["config"]).loss
    result = {
        "kind": "gomoku-nnue-assessment-v1",
        "datasetSha256": file_sha256(manifest),
        "checkpointSha256": file_sha256(checkpoint),
        "baselineSha256": file_sha256(baseline),
        "architecture": state["modelConfig"]["architecture"],
        "modelConfig": state["modelConfig"],
        "trainingStep": state["step"],
        "trainingSource": state.get("source", {}),
        "initializedFrom": state.get("initializedFrom"),
        "size": state["size"],
        "rule": state["rule"],
        "datasetCounts": data["counts"],
        "teachers": data.get("teachers", []),
        "exportBytes": exported["bytes"],
        "comparisonLossConfig": asdict(comparison_loss),
        "baseline": {},
        "selected": {},
        "hasMeasuredPlayingStrength": False,
        "runtimeCompatible": False,
    }
    for label, path in (("baseline", baseline), ("selected", checkpoint)):
        for split in ("validation", "test"):
            metrics = evaluate(
                path, manifest, split, device, loss_config=comparison_loss
            )
            result[label][split] = metrics
            print(
                json.dumps({"event": "assessment", "model": label, **metrics}),
                flush=True,
            )
    reference, vectors = reference_check(
        checkpoint, export_manifest, manifest, positions
    )
    result["quantizedReference"] = reference
    write_json(
        output / "reference-vectors.json",
        {"format": "line-nnue-reference-v1", "vectors": vectors},
    )
    write_json(output / "assessment.json", result)
    if not reference["passed"]:
        raise ValueError(
            "Quantized export parity failed; see assessment.json for measured errors"
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--export-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--device", choices=["cpu", "xpu", "cuda", "auto"], default="auto"
    )
    parser.add_argument("--positions", type=int, default=32)
    try:
        result = assess(**vars(parser.parse_args()))
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(2, f"Assessment failed: {error}\n")
    print(json.dumps(result["quantizedReference"], indent=2))


if __name__ == "__main__":
    main()
