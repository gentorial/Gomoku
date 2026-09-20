"""Portable quantized tensors and line tables; independent NumPy integer reference."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import struct
import numpy as np
from .config import ModelConfig, digest
from .device import torch, resolve_device, runtime_info
from .features import PATTERN_COUNT, enumerate_patterns, pattern_ids
from .io import atomic_write, file_sha256, read_json, write_json
from .model import LineNNUE, aligned_context
from .train import load_checkpoint

MAGIC = b"GMLINE1\0"
HEADER = struct.Struct("<8s12I")
DESCRIPTOR = struct.Struct("<32s6I")


def tensor_shapes(config):
    c, v, p = config.channels, config.value_hidden, config.policy_hidden
    return {
        "codebook.hv": (PATTERN_COUNT, c),
        "codebook.diag": (PATTERN_COUNT, c),
        "spatial.weight": (c, 1, 3, 3),
        "spatial.bias": (c,),
        "value_hidden.weight": (v, 20 * c + aligned_context(20 * c)),
        "value_hidden.bias": (v,),
        "value_out.weight": (3, v),
        "value_out.bias": (3,),
        "policy_local.weight": (p, 2 * c),
        "policy_local.bias": (p,),
        "policy_global.weight": (p, 2 * c + aligned_context(2 * c)),
        "policy_global.bias": (p,),
        "policy_out.weight": (1, p),
        "policy_out.bias": (1,),
    }


def export_checkpoint(checkpoint, output, *, device="cpu", chunk_size=4096):
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    state = load_checkpoint(checkpoint)
    config = ModelConfig(**state["modelConfig"])
    config.validate()
    if not config.qat:
        raise ValueError("Quantized export requires a checkpoint trained with qat=true")
    device = resolve_device(device)
    torch.set_num_threads(4)
    model = LineNNUE(config).to(device).eval()
    model.load_state_dict(state["model"], strict=True)
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Export output must be empty")
    output.mkdir(parents=True, exist_ok=True)
    weights_path = output / "weights.gnn"
    shapes = tensor_shapes(config)

    def write(stream):
        stream.write(
            HEADER.pack(
                MAGIC,
                1,
                1,
                config.mapping_width,
                config.channels,
                config.value_hidden,
                config.policy_hidden,
                config.activation_scale,
                config.weight_scale,
                state["size"],
                {"freestyle": 0, "standard": 1}[state["rule"]],
                len(shapes),
                PATTERN_COUNT,
            )
        )
        with torch.no_grad():
            for name, shape in shapes.items():
                scale = (
                    config.activation_scale
                    if name.startswith("codebook")
                    else config.weight_scale
                )
                dimensions = (*shape, *(0 for _ in range(4 - len(shape))))
                stream.write(
                    DESCRIPTOR.pack(
                        name.encode().ljust(32, b"\0"), len(shape), scale, *dimensions
                    )
                )
                if name.startswith("codebook"):
                    mapping = (
                        model.mapping_hv if name.endswith("hv") else model.mapping_diag
                    )
                    for start in range(0, PATTERN_COUNT, chunk_size):
                        planes = torch.from_numpy(
                            enumerate_patterns(
                                start, min(start + chunk_size, PATTERN_COUNT)
                            )
                        ).to(device)
                        values = mapping(planes).float().cpu().numpy()
                        if not np.isfinite(values).all():
                            raise ValueError("Cannot export non-finite mapping outputs")
                        stream.write(np.rint(values * scale).astype("<i2").tobytes())
                else:
                    layer, parameter = name.split(".")
                    values = (
                        getattr(getattr(model, layer), parameter)
                        .detach()
                        .float()
                        .cpu()
                        .numpy()
                    )
                    if not np.isfinite(values).all():
                        raise ValueError("Cannot export non-finite parameters")
                    stream.write(
                        np.rint(np.clip(values, -8, 8) * scale).astype("<i2").tobytes()
                    )

    atomic_write(weights_path, write)
    manifest = {
        "kind": "gomoku-line-nnue-export",
        "formatVersion": 1,
        "architecture": config.architecture,
        "architectureHash": digest(asdict(config)),
        "modelConfig": asdict(config),
        "rule": state["rule"],
        "size": state["size"],
        "quantization": "int16-weights-int64-reference-v1",
        "weights": "weights.gnn",
        "sha256": file_sha256(weights_path),
        "bytes": weights_path.stat().st_size,
        "checkpointSha256": file_sha256(checkpoint),
        "datasetSha256": state["datasetSha256"],
        "trainingStep": state["step"],
        "trainingSource": state.get("source", {}),
        "exportRuntime": runtime_info(device),
        "runtimeCompatible": False,
        "runtimeStatus": "Exported; run native/WASM parity checks before marking this model runtime-compatible",
    }
    write_json(output / "manifest.json", manifest)
    reference = QuantizedReference(output / "manifest.json")
    try:
        vectors = []
        for stones in ([], [(0, 0)], [(7, 7), (8, 7), (6, 6)]):
            board = np.zeros((state["size"], state["size"]), dtype=np.uint8)
            for i, (x, y) in enumerate(stones):
                board[y, x] = i % 2 + 1
            result = reference.predict(board, len(stones) % 2 + 1)
            vectors.append(
                {
                    "moves": [{"x": x, "y": y} for x, y in stones],
                    "valueLogits": result["value"].tolist(),
                    "policyLogits": result["policy"].tolist(),
                }
            )
        write_json(
            output / "reference-vectors.json",
            {"format": "line-nnue-reference-v1", "vectors": vectors},
        )
    finally:
        reference.close()
    return manifest


def round_divide(value, denominator):
    """Signed nearest-even division, with no floating-point accumulator."""
    quotient, remainder = np.divmod(value, denominator)
    return quotient + (
        (2 * remainder > denominator)
        | ((2 * remainder == denominator) & ((quotient & 1) != 0))
    )


class QuantizedReference:
    def __init__(self, manifest):
        path = Path(manifest).resolve()
        self.manifest = read_json(path)
        if (
            self.manifest.get("kind") != "gomoku-line-nnue-export"
            or self.manifest.get("weights") != "weights.gnn"
            or self.manifest.get("formatVersion") != 1
        ):
            raise ValueError("Unsupported export manifest")
        self.config = ModelConfig(**self.manifest["modelConfig"])
        self.config.validate()
        self.size = self.manifest["size"]
        if (
            self.manifest.get("architecture") != self.config.architecture
            or self.manifest.get("architectureHash") != digest(asdict(self.config))
            or self.size not in (15, 20)
            or self.manifest["rule"] not in ("freestyle", "standard")
        ):
            raise ValueError("Export architecture or rule mismatch")
        file = path.parent / "weights.gnn"
        if (
            file.stat().st_size != self.manifest["bytes"]
            or file_sha256(file) != self.manifest["sha256"]
        ):
            raise ValueError("Export size or checksum mismatch")
        self.tensors = {}
        try:
            with file.open("rb") as stream:
                c = self.config
                expected = (
                    MAGIC,
                    1,
                    1,
                    c.mapping_width,
                    c.channels,
                    c.value_hidden,
                    c.policy_hidden,
                    c.activation_scale,
                    c.weight_scale,
                    self.size,
                    {"freestyle": 0, "standard": 1}[self.manifest["rule"]],
                    14,
                    PATTERN_COUNT,
                )
                if HEADER.unpack(stream.read(HEADER.size)) != expected:
                    raise ValueError("Export header mismatch")
                for name, shape in tensor_shapes(c).items():
                    description = DESCRIPTOR.unpack(stream.read(DESCRIPTOR.size))
                    expected_scale = (
                        c.activation_scale
                        if name.startswith("codebook")
                        else c.weight_scale
                    )
                    if (
                        description[0].rstrip(b"\0").decode() != name
                        or description[1] != len(shape)
                        or (
                            description[2] != expected_scale
                            or description[3 : 3 + len(shape)] != shape
                            or any(description[3 + len(shape) :])
                        )
                    ):
                        raise ValueError("Export tensor descriptor mismatch")
                    offset = stream.tell()
                    length = int(np.prod(shape)) * 2
                    if offset + length > self.manifest["bytes"]:
                        raise ValueError("Truncated export")
                    self.tensors[name] = np.memmap(
                        file, dtype="<i2", mode="r", offset=offset, shape=shape
                    )
                    stream.seek(length, 1)
                if stream.read(1):
                    raise ValueError("Unexpected trailing export data")
        except Exception:
            self.close()
            raise

    def close(self):
        for value in self.tensors.values():
            value._mmap.close()
        self.tensors.clear()

    def linear(self, name, value):
        weight = self.tensors[name + ".weight"].astype(np.int64)
        bias = self.tensors[name + ".bias"].astype(np.int64)
        return value @ weight.T + bias * self.config.activation_scale

    def activate(self, value):
        return np.clip(
            round_divide(value, self.config.weight_scale),
            0,
            self.config.activation_scale,
        )

    def predict(self, board, to_move):
        if (
            board.shape != (self.size, self.size)
            or to_move not in (1, 2)
            or not np.isin(board, [0, 1, 2]).all()
        ):
            raise ValueError("Invalid reference position")
        maps = []
        c, size, q = self.config.channels, self.size, self.config.activation_scale
        for perspective in (1, 2):
            ids = pattern_ids(board, perspective)
            hv, diag = self.tensors["codebook.hv"], self.tensors["codebook.diag"]
            merged = (
                hv[ids[0]].astype(np.int64) + hv[ids[1]] + diag[ids[2]] + diag[ids[3]]
            )
            merged = np.clip(merged, 0, q).T.reshape(c, size, size)
            padded = np.pad(merged, ((0, 0), (1, 1), (1, 1)))
            spatial = np.broadcast_to(
                self.tensors["spatial.bias"].astype(np.int64)[:, None, None] * q,
                (c, size, size),
            ).copy()
            weights = self.tensors["spatial.weight"][:, 0].astype(np.int64)
            for y in range(3):
                for x in range(3):
                    spatial += (
                        padded[:, y : y + size, x : x + size]
                        * weights[:, y, x, None, None]
                    )
            maps.append(self.activate(spatial))
        paired = np.concatenate((maps[to_move - 1], maps[2 - to_move]), axis=0)
        global_mean = round_divide(paired.sum(axis=(1, 2)), size * size)
        regions = []
        for y in range(3):
            for x in range(3):
                region = paired[
                    :,
                    y * size // 3 : (y + 1) * size // 3,
                    x * size // 3 : (x + 1) * size // 3,
                ]
                regions.append(
                    round_divide(
                        region.sum(axis=(1, 2)), region.shape[1] * region.shape[2]
                    )
                )
        context = np.array([int(to_move == 1) * q, size * q // 20], dtype=np.int64)
        value_context = np.pad(context, (0, aligned_context(20 * c) - 2))
        policy_context = np.pad(context, (0, aligned_context(2 * c) - 2))
        hidden = self.activate(
            self.linear(
                "value_hidden", np.concatenate((global_mean, *regions, value_context))
            )
        )
        value = self.linear("value_out", hidden) / (q * self.config.weight_scale)
        local = self.linear("policy_local", paired.transpose(1, 2, 0))
        global_policy = self.linear(
            "policy_global", np.concatenate((global_mean, policy_context))
        )
        policy = self.linear(
            "policy_out", self.activate(local + global_policy)
        ).reshape(-1) / (q * self.config.weight_scale)
        policy[board.reshape(-1) != 0] = -1e9
        return {"value": value, "policy": policy}


def main():
    parser = argparse.ArgumentParser(
        description="Export NNUE line tables, quantized weights and reference vectors"
    )
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--device", choices=["cpu", "xpu", "cuda", "auto"], default="cpu"
    )
    parser.add_argument("--chunk-size", type=int, default=4096)
    args = parser.parse_args()
    try:
        manifest = export_checkpoint(
            args.checkpoint, args.output, device=args.device, chunk_size=args.chunk_size
        )
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(2, f"Export failed: {error}\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
