"""Versioned, strict configuration shared by training and export."""

from dataclasses import asdict, dataclass, field, fields
import hashlib
import json
import math
from pathlib import Path
import tomllib

ARCHITECTURE = "line11-dual-v1"
FEATURE_VERSION = "line11-relative-border-v1"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def positive(value, name, maximum=1_000_000_000):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [1, {maximum}]")


@dataclass(frozen=True)
class ModelConfig:
    architecture: str = ARCHITECTURE
    mapping_width: int = 64
    channels: int = 64
    value_hidden: int = 128
    policy_hidden: int = 32
    activation_scale: int = 256
    weight_scale: int = 1024
    qat: bool = True

    def validate(self):
        if self.architecture != ARCHITECTURE:
            raise ValueError(f"Unsupported architecture: {self.architecture}")
        for name in ("mapping_width", "channels", "value_hidden", "policy_hidden"):
            positive(getattr(self, name), name, 1024)
        if self.activation_scale != 256 or self.weight_scale != 1024:
            raise ValueError(
                "v1 quantization uses activation_scale=256 and weight_scale=1024"
            )
        if type(self.qat) is not bool:
            raise ValueError("qat must be a boolean")


@dataclass(frozen=True)
class DataConfig:
    manifest: str = "data/training/manifest.json"
    augment: bool = True
    shuffle_block: int = 65536


@dataclass(frozen=True)
class LossConfig:
    value_target: str = "legacy"
    teacher_weight: float = 0.75
    result_weight: float = 0.25
    policy_weight: float = 1.0
    score_scale: float = 600.0


@dataclass(frozen=True)
class RunConfig:
    output: str = "artifacts/training/run01"
    device: str = "auto"
    precision: str = "fp32"
    seed: int = 42
    batch_size: int = 16
    micro_batch_size: int = 1
    steps: int = 50000
    learning_rate: float = 0.001
    weight_decay: float = 0.0001
    warmup_steps: int = 1000
    min_lr_ratio: float = 0.05
    grad_clip: float = 1.0
    validate_every: int = 500
    validation_batches: int = 64
    checkpoint_every: int = 500
    log_every: int = 25
    cpu_threads: int = 4


@dataclass(frozen=True)
class Config:
    version: int = 1
    model: ModelConfig = field(default_factory=ModelConfig)
    data: DataConfig = field(default_factory=DataConfig)
    loss: LossConfig = field(default_factory=LossConfig)
    run: RunConfig = field(default_factory=RunConfig)

    def validate(self):
        if type(self.version) is not int or self.version != 1:
            raise ValueError("Unsupported config version")
        self.model.validate()
        for name in (
            "batch_size",
            "micro_batch_size",
            "steps",
            "validate_every",
            "validation_batches",
            "checkpoint_every",
            "log_every",
            "cpu_threads",
        ):
            positive(getattr(self.run, name), name)
        positive(self.data.shuffle_block, "shuffle_block", 1_000_000)
        if self.run.micro_batch_size > self.run.batch_size:
            raise ValueError("micro_batch_size must not exceed batch_size")
        if type(self.run.seed) is not int or not 0 <= self.run.seed < 2**32:
            raise ValueError("seed must be an unsigned 32-bit integer")
        if type(self.run.warmup_steps) is not int or self.run.warmup_steps < 0:
            raise ValueError("warmup_steps must be nonnegative")
        if type(self.data.augment) is not bool:
            raise ValueError("augment must be a boolean")
        if self.run.device not in ("auto", "cpu", "xpu", "cuda"):
            raise ValueError("device must be auto, cpu, xpu or cuda")
        if self.run.precision not in ("fp32", "bf16"):
            raise ValueError("precision must be fp32 or bf16")
        if self.loss.value_target not in ("legacy", "expected_score"):
            raise ValueError("value_target must be legacy or expected_score")
        for name, value in {
            **{k: v for k, v in asdict(self.loss).items() if k != "value_target"},
            "learning_rate": self.run.learning_rate,
            "weight_decay": self.run.weight_decay,
            "min_lr_ratio": self.run.min_lr_ratio,
            "grad_clip": self.run.grad_clip,
        }.items():
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if (
            self.loss.score_scale <= 0
            or self.run.learning_rate <= 0
            or self.run.grad_clip <= 0
        ):
            raise ValueError(
                "score_scale, learning_rate and grad_clip must be positive"
            )
        if (
            max(
                self.loss.teacher_weight,
                self.loss.result_weight,
                self.loss.policy_weight,
            )
            == 0
        ):
            raise ValueError("At least one loss weight must be positive")
        if self.run.min_lr_ratio > 1:
            raise ValueError("min_lr_ratio must be at most 1")
        if not isinstance(self.data.manifest, str) or not isinstance(
            self.run.output, str
        ):
            raise ValueError("manifest and output must be paths")

    def to_dict(self):
        return asdict(self)


def strict(cls, value):
    if not isinstance(value, dict):
        raise ValueError(f"{cls.__name__} must be an object")
    unknown = set(value) - {item.name for item in fields(cls)}
    if unknown:
        raise ValueError(f"Unknown {cls.__name__} fields: {sorted(unknown)}")
    return cls(**value)


def from_dict(value):
    value = dict(value)
    for key, cls in (
        ("model", ModelConfig),
        ("data", DataConfig),
        ("loss", LossConfig),
        ("run", RunConfig),
    ):
        value[key] = strict(cls, value.get(key, {}))
    config = strict(Config, value)
    config.validate()
    return config


def load_config(path):
    path = Path(path).resolve()
    with path.open("rb") as stream:
        value = tomllib.load(stream)
    config = from_dict(value)
    value = config.to_dict()
    for section, key in (("data", "manifest"), ("run", "output")):
        value[section][key] = str((path.parent / value[section][key]).resolve())
    return from_dict(value)
