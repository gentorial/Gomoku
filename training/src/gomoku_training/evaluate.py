import argparse
from dataclasses import replace
import json
from pathlib import Path
from .config import from_dict
from .device import torch, resolve_device
from .model import LineNNUE
from .shards import ShardDataset
from .train import load_checkpoint, validate_model


def evaluate(checkpoint, manifest, split="test", device="auto", *, loss_config=None, precision=None):
    state = load_checkpoint(checkpoint)
    config = from_dict(state["config"])
    if loss_config is not None:
        config = replace(config, loss=loss_config)
    if precision is not None:
        config = replace(config, run=replace(config.run, precision=precision))
    config.validate()
    device = resolve_device(device)
    torch.set_num_threads(config.run.cpu_threads)
    dataset = ShardDataset(manifest, split)
    try:
        if (dataset.size, dataset.rule) != (state["size"], state["rule"]):
            raise ValueError("Evaluation dataset does not match model rule and size")
        if not len(dataset):
            raise ValueError("Evaluation split is empty")
        config = replace(
            config,
            run=replace(
                config.run,
                validation_batches=(len(dataset) + config.run.batch_size - 1)
                // config.run.batch_size,
            ),
        )
        model = LineNNUE(config.model).to(device)
        model.load_state_dict(state["model"], strict=True)
        return {
            "split": split,
            "positions": len(dataset),
            "datasetSha256": dataset.digest,
            "checkpointStep": state["step"],
            "device": device.type,
            "precision": config.run.precision,
            **validate_model(model, dataset, config, device),
        }
    finally:
        dataset.close()


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate a checkpoint on the complete held-out split"
    )
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--split", choices=["validation", "test"], default="test")
    parser.add_argument(
        "--device", choices=["auto", "cpu", "xpu", "cuda"], default="auto"
    )
    args = parser.parse_args()
    try:
        result = evaluate(args.checkpoint, args.manifest, args.split, args.device)
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(2, f"Evaluation failed: {error}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
