"""Single-device trainer with portable, atomic checkpoints and exact batch resume."""

import argparse
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import pickle
import random
import signal
import subprocess
import time
import numpy as np
from .device import (
    torch,
    autocast,
    resolve_device,
    restore_rng,
    rng_state,
    runtime_info,
    synchronize,
)
from .config import digest, load_config
from .io import atomic_write, file_sha256, write_json
from .loss import effective_labels, loss_terms, summarize
from .model import LineNNUE
from .shards import BlockSampler, ShardDataset, decode_batch

CHECKPOINT_KIND = "gomoku-nnue-training-checkpoint"


def load_checkpoint(path):
    try:
        state = torch.load(path, map_location="cpu", weights_only=True)
    except pickle.UnpicklingError as error:
        raise ValueError(
            "Checkpoint contains unsupported serialized objects"
        ) from error
    if (
        not isinstance(state, dict)
        or state.get("kind") != CHECKPOINT_KIND
        or state.get("formatVersion") != 1
    ):
        raise ValueError("Not a supported NNUE training checkpoint")
    return state


def compatibility(config, dataset_digest):
    value = config.to_dict()
    value["data"].pop("manifest")
    for name in ("output", "device", "cpu_threads"):
        value["run"].pop(name)
    return digest({"config": value, "dataset": dataset_digest})


def tensor_batch(dataset, indices, device, augmentation=None):
    arrays = decode_batch([dataset[i] for i in indices], dataset.size, augmentation)
    return {key: torch.from_numpy(value).to(device) for key, value in arrays.items()}


def learning_rate(step, run):
    if step < run.warmup_steps:
        return run.learning_rate * (step + 1) / max(run.warmup_steps, 1)
    progress = (step - run.warmup_steps) / max(1, run.steps - run.warmup_steps)
    factor = run.min_lr_ratio + (1 - run.min_lr_ratio) * 0.5 * (
        1 + math.cos(math.pi * progress)
    )
    return run.learning_rate * factor


def validate_model(model, dataset, config, device):
    totals = {}
    model.eval()
    limit = min(len(dataset), config.run.batch_size * config.run.validation_batches)
    with torch.no_grad():
        for start in range(0, limit, config.run.micro_batch_size):
            batch = tensor_batch(
                dataset,
                range(start, min(start + config.run.micro_batch_size, limit)),
                device,
            )
            with autocast(device, config.run.precision):
                prediction = model(batch["boards"], batch["turn"])
                loss, metrics = loss_terms(prediction, batch, config.loss)
            if not torch.isfinite(loss):
                raise RuntimeError("Non-finite validation loss")
            for key, value in metrics.items():
                totals[key] = totals.get(key, 0.0) + float(value)
    model.train()
    if not totals or not totals["effective"]:
        raise ValueError(
            "Validation has no usable labels under this loss configuration"
        )
    return summarize(totals)


def source_info():
    root = Path(__file__).resolve().parents[3]
    source = Path(__file__).resolve().parent
    files = {path.name: file_sha256(path) for path in sorted(source.glob("*.py"))}
    info = {"trainingSourceSha256": digest(files)}
    try:
        info["gitRevision"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        info["gitRevision"] = "unknown"
    return info


def run_training(config, *, resume=False, initialize=None, stop_after=None):
    config.validate()
    if resume and initialize:
        raise ValueError("resume and initialize are mutually exclusive")
    if stop_after is not None and stop_after < 1:
        raise ValueError("stop_after must be positive")
    if int(os.environ.get("WORLD_SIZE", "1")) != 1:
        raise ValueError(
            "This trainer supports one process/device; distributed launch is not enabled"
        )
    device = resolve_device(config.run.device)
    torch.set_num_threads(config.run.cpu_threads)
    torch.manual_seed(config.run.seed)
    random.seed(config.run.seed)
    train = ShardDataset(config.data.manifest, "train")
    validation = None
    try:
        validation = ShardDataset(config.data.manifest, "validation")
        if not len(train) or not len(validation):
            raise ValueError(
                "Nonempty, independent training and validation splits are required"
            )
        out = Path(config.run.output)
        checkpoint_path = out / "last.pt"
        if resume and not checkpoint_path.is_file():
            raise ValueError("No last.pt checkpoint to resume")
        if not resume and out.exists() and any(out.iterdir()):
            raise ValueError(
                "Run output already contains files; use --resume or a new output"
            )
        out.mkdir(parents=True, exist_ok=True)
        model = LineNNUE(config.model).to(device)
        optimizer = torch.optim.AdamW(
            model.parameters(),
            lr=config.run.learning_rate,
            weight_decay=config.run.weight_decay,
        )
        step, best, sampler_state = 0, float("inf"), None
        initialized_from = None
        identity = compatibility(config, train.digest)
        if resume or initialize:
            state = load_checkpoint(checkpoint_path if resume else initialize)
            if state["modelConfig"] != asdict(config.model):
                raise ValueError("Checkpoint architecture differs from configuration")
            if state["rule"] != train.rule or state["size"] != train.size:
                raise ValueError("Checkpoint rule or board size differs from dataset")
            model.load_state_dict(state["model"], strict=True)
            if resume:
                initialized_from = state.get("initializedFrom")
                if state["compatibility"] != identity:
                    raise ValueError(
                        "Resume config or dataset differs; initialize a new run for changed experiments"
                    )
                optimizer.load_state_dict(state["optimizer"])
                step, best, sampler_state = (
                    state["step"],
                    state["bestValidation"],
                    state["sampler"],
                )
                random.setstate(state["pythonRng"])
                restore_rng(state["rng"], device)
            else:
                initialized_from = {
                    "checkpointSha256": file_sha256(initialize),
                    "step": state["step"],
                    "datasetSha256": state["datasetSha256"],
                    "source": state.get("source", {}),
                }
        sampler = BlockSampler(
            len(train), config.run.seed, config.data.shuffle_block, sampler_state
        )
        metadata = {
            "kind": "nnue-training-run",
            "config": config.to_dict(),
            "datasetSha256": train.digest,
            "runtime": runtime_info(device),
            "source": source_info(),
            "architectureHash": model.architecture_hash,
            "initializedFrom": initialized_from,
            "runtimeCompatible": False,
        }
        if not resume:
            write_json(out / "run.json", metadata)
            write_json(out / "dataset-manifest.json", train.manifest)
        log_path = out / "metrics.jsonl"
        if resume and log_path.exists():

            def trim(stream):
                with log_path.open(encoding="utf-8") as old:
                    for line in old:
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if item["step"] <= step:
                            stream.write(
                                (json.dumps(item, allow_nan=False) + "\n").encode()
                            )

            atomic_write(log_path, trim)
        stopping = False

        def request_stop(*_):
            nonlocal stopping
            stopping = True

        old_handler = None
        try:
            old_handler = signal.signal(signal.SIGINT, request_stop)
        except ValueError:
            pass

        def save(path):
            state = {
                "kind": CHECKPOINT_KIND,
                "formatVersion": 1,
                "runtimeCompatible": False,
                "modelConfig": asdict(config.model),
                "config": config.to_dict(),
                "rule": train.rule,
                "size": train.size,
                "datasetSha256": train.digest,
                "runtime": metadata["runtime"],
                "source": metadata["source"],
                "initializedFrom": initialized_from,
                "compatibility": identity,
                "step": step,
                "bestValidation": best,
                "model": model.state_dict(),
                "optimizer": optimizer.state_dict(),
                "sampler": sampler.state_dict(),
                "rng": rng_state(device),
                "pythonRng": random.getstate(),
            }
            atomic_write(path, lambda stream: torch.save(state, stream))

        initial_step = step
        started = time.perf_counter()
        model.train()
        try:
            with log_path.open("a", encoding="utf-8", buffering=1) as log:
                if not resume:
                    baseline = validate_model(model, validation, config, device)
                    best = baseline["loss"]
                    record = {
                        "event": "validation",
                        "step": 0,
                        "baseline": True,
                        **baseline,
                    }
                    log.write(json.dumps(record, allow_nan=False) + "\n")
                    print(json.dumps(record, allow_nan=False), flush=True)
                    save(out / "initial.pt")
                    save(out / "best.pt")
                    save(checkpoint_path)
                while step < config.run.steps and not stopping:
                    if stop_after is not None and step - initial_step >= stop_after:
                        break
                    indices = sampler.next_indices(config.run.batch_size)
                    augmentation = (
                        np.random.default_rng([config.run.seed, step, 9])
                        if config.data.augment
                        else None
                    )
                    batch = tensor_batch(train, indices, device, augmentation)
                    optimizer.zero_grad(set_to_none=True)
                    for group in optimizer.param_groups:
                        group["lr"] = learning_rate(step, config.run)
                    effective = effective_labels(batch, config.loss).sum()
                    if not effective:
                        raise RuntimeError("Batch has no effective labels")
                    metrics = {}
                    for begin in range(0, len(indices), config.run.micro_batch_size):
                        micro = {
                            key: value[begin : begin + config.run.micro_batch_size]
                            for key, value in batch.items()
                        }
                        with autocast(device, config.run.precision):
                            prediction = model(micro["boards"], micro["turn"])
                            loss, part = loss_terms(prediction, micro, config.loss)
                        if not torch.isfinite(loss):
                            raise RuntimeError("Non-finite training loss")
                        (loss * part["effective"] / effective).backward()
                        if device.type == "xpu":
                            synchronize(device)
                        for key, value in part.items():
                            metrics[key] = metrics.get(key, 0) + value.detach()
                    norm = torch.nn.utils.clip_grad_norm_(
                        model.parameters(),
                        config.run.grad_clip,
                        error_if_nonfinite=True,
                    )
                    optimizer.step()
                    step += 1
                    final_step = (
                        step == config.run.steps
                        or stopping
                        or (
                            stop_after is not None and step - initial_step >= stop_after
                        )
                    )
                    if step % config.run.log_every == 0 or final_step:
                        synchronize(device)
                        record = {
                            "event": "train",
                            "step": step,
                            "epoch": sampler.epoch,
                            "lr": optimizer.param_groups[0]["lr"],
                            "gradientNorm": float(norm),
                            "samplesPerSecond": (step - initial_step)
                            * config.run.batch_size
                            / max(time.perf_counter() - started, 1e-9),
                            **summarize({k: float(v) for k, v in metrics.items()}),
                        }
                        log.write(json.dumps(record, allow_nan=False) + "\n")
                        print(json.dumps(record, allow_nan=False), flush=True)
                    if step % config.run.validate_every == 0 or final_step:
                        metrics = validate_model(model, validation, config, device)
                        record = {"event": "validation", "step": step, **metrics}
                        log.write(json.dumps(record, allow_nan=False) + "\n")
                        print(json.dumps(record, allow_nan=False), flush=True)
                        if metrics["loss"] < best:
                            best = metrics["loss"]
                            save(out / "best.pt")
                    if step % config.run.checkpoint_every == 0 or final_step:
                        save(checkpoint_path)
                # SIGINT between updates still publishes the most recent complete update.
                if stopping:
                    save(checkpoint_path)
        finally:
            if old_handler is not None:
                signal.signal(signal.SIGINT, old_handler)
        result = {
            "step": step,
            "completed": step == config.run.steps,
            "device": device.type,
            "bestValidation": best if math.isfinite(best) else None,
            "checkpoint": str(checkpoint_path),
        }
        write_json(out / "status.json", result)
        return result
    finally:
        train.close()
        if validation is not None:
            validation.close()


def main():
    parser = argparse.ArgumentParser(
        description="Train the directional NNUE value/policy model"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--initialize", type=Path)
    parser.add_argument(
        "--stop-after",
        type=int,
        help="Pause after N updates without changing the LR schedule",
    )
    args = parser.parse_args()
    try:
        result = run_training(
            load_config(args.config),
            resume=args.resume,
            initialize=args.initialize,
            stop_after=args.stop_after,
        )
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(2, f"Training failed: {error}\n")
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
