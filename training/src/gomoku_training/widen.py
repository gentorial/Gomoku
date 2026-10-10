"""Widen the value head of a checkpoint without changing what the network computes.

Existing hidden units are copied; new units start from the regular layer
initialization, but their value_out weights are zero, so every output (and the
quantized export) is unchanged until training moves them. The result is meant
for `run_training(initialize=...)`; optimizer and sampler state are dropped.
"""

import argparse
from dataclasses import asdict
from pathlib import Path
import torch
from .config import ModelConfig
from .io import atomic_write, file_sha256
from .model import LineNNUE
from .train import load_checkpoint


def widen(checkpoint, output, value_hidden, *, seed=42):
    state = load_checkpoint(checkpoint)
    old = state["modelConfig"]["value_hidden"]
    if type(value_hidden) is not int or not old < value_hidden <= 1024:
        raise ValueError(f"value_hidden must grow from {old} to at most 1024")
    config = ModelConfig(**{**state["modelConfig"], "value_hidden": value_hidden})
    torch.manual_seed(seed)
    weights = LineNNUE(config).state_dict()
    for name, tensor in state["model"].items():
        if name.startswith("value_hidden."):
            weights[name][:old] = tensor
        elif name == "value_out.weight":
            weights[name].zero_()
            weights[name][:, :old] = tensor
        else:
            weights[name] = tensor.clone()
    widened = {
        **state,
        "modelConfig": asdict(config),
        "config": {**state["config"], "model": asdict(config)},
        "model": weights,
        "optimizer": {},
        "sampler": {},
        "source": {
            **state.get("source", {}),
            "widenedFrom": {"checkpointSha256": file_sha256(checkpoint), "valueHidden": old},
        },
    }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(output, lambda stream: torch.save(widened, stream))
    return widened


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--value-hidden", type=int, required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    widen(args.checkpoint, args.output, args.value_hidden, seed=args.seed)


if __name__ == "__main__":
    main()
