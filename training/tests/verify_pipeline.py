"""Full-width integration check. Synthetic labels verify plumbing, never strength."""

import argparse
import json
from pathlib import Path
import numpy as np
from gomoku_training.config import Config, DataConfig, RunConfig
from gomoku_training.device import torch, runtime_info, resolve_device
from gomoku_training.evaluate import evaluate
from gomoku_training.export import export_checkpoint, QuantizedReference
from gomoku_training.io import write_json
from gomoku_training.model import LineNNUE
from gomoku_training.prepare import pack_sample
from gomoku_training.shards import decode_batch
from gomoku_training.train import load_checkpoint, run_training
from test_training import make_data, fixture_samples


def verify(output, device, steps):
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Verification output must be empty")
    if steps < 2:
        raise ValueError("At least two steps are required to check resume")
    output.mkdir(parents=True, exist_ok=True)
    write_json(
        output / "PURPOSE.json",
        {
            "testOnly": True,
            "labels": "synthetic integration fixtures",
            "hasMeasuredPlayingStrength": False,
        },
    )
    manifest = make_data(output, count=96)
    config = Config(
        data=DataConfig(str(manifest)),
        run=RunConfig(
            output=str(output / "run"),
            device=device,
            steps=steps,
            warmup_steps=2,
            validate_every=2,
            checkpoint_every=2,
            log_every=1,
        ),
    )
    first = run_training(config, stop_after=steps // 2)
    if first["completed"]:
        raise AssertionError("First run did not pause")
    resumed = run_training(config, resume=True)
    if not resumed["completed"]:
        raise AssertionError("Resumed run did not finish")
    checkpoint = output / "run/last.pt"
    evaluation = evaluate(checkpoint, manifest, device=device)
    exported = export_checkpoint(checkpoint, output / "export", device="cpu")
    # Compare the entire default-width network, including all legal policy logits.
    state = load_checkpoint(checkpoint)
    model = LineNNUE(config.model).eval()
    model.load_state_dict(state["model"])
    reference = QuantizedReference(output / "export/manifest.json")
    max_error = 0.0
    try:
        for sample in list(fixture_samples())[:8]:
            row, board = pack_sample(sample)
            batch = {k: torch.from_numpy(v) for k, v in decode_batch([row], 15).items()}
            with torch.no_grad():
                expected = model(batch["boards"], batch["turn"])
            actual = reference.predict(board, int(row["turn"]))
            for name in ("value", "policy"):
                difference = np.max(np.abs(expected[name][0].numpy() - actual[name]))
                max_error = max(max_error, float(difference))
                np.testing.assert_allclose(
                    actual[name], expected[name][0].numpy(), rtol=0, atol=2e-5
                )
    finally:
        reference.close()
    result = {
        "testOnly": True,
        "runtime": runtime_info(resolve_device(device)),
        "architecture": config.model.architecture,
        "parameters": sum(p.numel() for p in model.parameters()),
        "steps": steps,
        "resumed": True,
        "evaluation": evaluation,
        "exportBytes": exported["bytes"],
        "referencePositions": 8,
        "maximumReferenceError": max_error,
        "runtimeCompatible": False,
    }
    write_json(output / "verification.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--device", choices=["cpu", "xpu", "cuda", "auto"], default="cpu"
    )
    parser.add_argument("--steps", type=int, default=6)
    args = parser.parse_args()
    verify(args.output, args.device, args.steps)
