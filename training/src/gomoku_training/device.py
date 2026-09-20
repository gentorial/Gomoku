import argparse
from contextlib import nullcontext
import json
import os
import sys
import time

# PyTorch 2.11 / Windows / Arc B390 fails under sustained training with the
# Level Zero V2 adapter. Select Intel's supported legacy adapter before SYCL
# initializes. An explicit environment override is preserved for other drivers.
if sys.platform == "win32":
    os.environ.setdefault("SYCL_UR_USE_LEVEL_ZERO_V2", "0")

try:
    import torch
except ImportError as error:
    raise SystemExit(
        "Install a training backend: uv sync --package gomoku-training --extra cpu (or xpu/cuda)"
    ) from error


def available():
    return {
        "cpu": True,
        "xpu": hasattr(torch, "xpu") and torch.xpu.is_available(),
        "cuda": torch.cuda.is_available(),
    }


def runtime_info(device):
    info = {
        "torch": str(torch.__version__),
        "device": device.type,
        "name": "CPU"
        if device.type == "cpu"
        else getattr(torch, device.type).get_device_name(),
    }
    if device.type == "xpu":
        info["levelZeroV2"] = os.environ.get(
            "SYCL_UR_USE_LEVEL_ZERO_V2", "backend-default"
        )
    return info


def resolve_device(requested):
    if requested == "cpu":
        return torch.device("cpu")
    choices = available()
    if requested == "auto":
        requested = next(name for name in ("cuda", "xpu", "cpu") if choices[name])
    if requested not in choices or not choices[requested]:
        raise ValueError(
            f"Requested device {requested!r} is unavailable; available={choices}"
        )
    return torch.device(requested)


def synchronize(device):
    if device.type != "cpu":
        getattr(torch, device.type).synchronize()


def autocast(device, precision):
    return (
        nullcontext()
        if precision == "fp32"
        else torch.autocast(device_type=device.type, dtype=torch.bfloat16)
    )


def rng_state(device):
    state = {"cpu": torch.get_rng_state(), "backend": device.type}
    if device.type != "cpu":
        state["accelerator"] = getattr(torch, device.type).get_rng_state().cpu()
    return state


def restore_rng(state, device):
    torch.set_rng_state(state["cpu"])
    if state["backend"] == device.type and device.type != "cpu":
        getattr(torch, device.type).set_rng_state(state["accelerator"])


def probe(
    requested="auto", batch_size=2, steps=3, precision="fp32", micro_batch_size=1
):
    from .model import LineNNUE

    device = resolve_device(requested)
    torch.set_num_threads(4)
    torch.manual_seed(42)
    model = LineNNUE().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001)
    boards = torch.zeros((batch_size, 15, 15), dtype=torch.int64, device=device)
    boards[:, 7, 7] = 1
    turns = torch.full((batch_size,), 2, dtype=torch.int64, device=device)
    durations = []
    for step in range(steps + 1):
        synchronize(device)
        started = time.perf_counter()
        optimizer.zero_grad(set_to_none=True)
        for begin in range(0, batch_size, micro_batch_size):
            end = min(begin + micro_batch_size, batch_size)
            with autocast(device, precision):
                result = model(boards[begin:end], turns[begin:end])
                loss = (
                    result["value"].square().mean()
                    + result["policy"][:, 0].square().mean()
                )
            (loss * ((end - begin) / batch_size)).backward()
            if device.type == "xpu":
                synchronize(device)
        optimizer.step()
        synchronize(device)
        if not torch.isfinite(loss):
            raise RuntimeError("Non-finite device probe loss")
        if step:
            durations.append(time.perf_counter() - started)
    backend = getattr(torch, device.type, None)
    return {
        **runtime_info(device),
        "available": available(),
        "architecture": model.config.architecture,
        "parameters": sum(p.numel() for p in model.parameters()),
        "batchSize": batch_size,
        "microBatchSize": micro_batch_size,
        "precision": precision,
        "measuredSteps": steps,
        "samplesPerSecond": batch_size * steps / sum(durations),
        "loss": float(loss.detach()),
        "peakAllocatedBytes": 0
        if device.type == "cpu"
        else backend.max_memory_allocated(),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Inspect devices and test the production architecture"
    )
    parser.add_argument(
        "--device", choices=["auto", "cpu", "xpu", "cuda"], default="auto"
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--micro-batch-size", type=int, default=1)
    parser.add_argument("--steps", type=int, default=3)
    parser.add_argument("--precision", choices=["fp32", "bf16"], default="fp32")
    args = parser.parse_args()
    if (
        args.batch_size < 1
        or args.steps < 1
        or not 1 <= args.micro_batch_size <= args.batch_size
    ):
        parser.error("Require batch-size >= micro-batch-size >= 1 and positive steps")
    try:
        device = resolve_device(args.device)
        result = (
            probe(
                args.device,
                args.batch_size,
                args.steps,
                args.precision,
                args.micro_batch_size,
            )
            if args.check
            else {**runtime_info(device), "available": available()}
        )
    except (ValueError, RuntimeError) as error:
        parser.exit(2, f"Device check failed: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
