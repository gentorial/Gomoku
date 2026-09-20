"""The production architecture. No CUDA-only operators or alternate toy network."""

from dataclasses import asdict
import torch
from torch import nn
from torch.nn import functional as F
from .config import ModelConfig, digest
from .features import line_geometry


def aligned_context(features):
    return (features + 2 + 15) // 16 * 16 - features


def quantize_ste(value, scale, limit):
    clipped = value.clamp(-limit, limit)
    return clipped + (torch.round(clipped * scale) / scale - clipped).detach()


class Mapping(nn.Module):
    def __init__(self, width, channels):
        super().__init__()
        layers = []
        for i in range(5):
            layers.append(
                nn.Conv1d(3 if i == 0 else width, channels if i == 4 else width, 3)
            )
            if i < 4:
                layers.append(nn.ReLU())
        self.layers = nn.Sequential(*layers)

    def forward(self, lines):
        return self.layers(lines).squeeze(-1).tanh()


class LineNNUE(nn.Module):
    def __init__(self, config=ModelConfig()):
        super().__init__()
        config.validate()
        self.config = config
        c = config.channels
        self.mapping_hv = Mapping(config.mapping_width, c)
        self.mapping_diag = Mapping(config.mapping_width, c)
        self.spatial = nn.Conv2d(c, c, 3, padding=1, groups=c)
        # Global + 3x3 regional means from both perspectives, plus absolute turn/size.
        self.value_hidden = nn.Linear(
            20 * c + aligned_context(20 * c), config.value_hidden
        )
        self.value_out = nn.Linear(config.value_hidden, 3)
        self.policy_local = nn.Linear(2 * c, config.policy_hidden)
        self.policy_global = nn.Linear(
            2 * c + aligned_context(2 * c), config.policy_hidden
        )
        self.policy_out = nn.Linear(config.policy_hidden, 1)
        self._geometry = {}

    @property
    def architecture_hash(self):
        return digest(asdict(self.config))

    def activation(self, value, limit=1.0):
        if self.config.qat:
            return quantize_ste(value, self.config.activation_scale, limit)
        return value.clamp(-limit, limit)

    def weight(self, value):
        if self.config.qat:
            return quantize_ste(value, self.config.weight_scale, 8.0)
        return value.clamp(-8, 8)

    def linear(self, layer, value):
        return F.linear(value, self.weight(layer.weight), self.weight(layer.bias))

    def feature_maps(self, boards):
        batch, size, _ = boards.shape
        key = (size, str(boards.device))
        if key not in self._geometry:
            self._geometry[key] = torch.tensor(
                line_geometry(size)[0].copy(), device=boards.device
            )
        indices = self._geometry[key]
        cells = F.pad(boards.reshape(batch, -1), (0, 1), value=3)
        lines = cells[:, indices]  # B, direction, point, line
        maps = []
        for perspective in (1, 2):
            planes = torch.stack(
                (lines == perspective, lines == 3 - perspective, lines == 3), dim=-2
            ).float()
            directional = []
            for begin, end, mapping in (
                (0, 2, self.mapping_hv),
                (2, 4, self.mapping_diag),
            ):
                value = mapping(planes[:, begin:end].reshape(-1, 3, 11))
                value = self.activation(value)
                directional.append(value.reshape(batch, 2, size * size, -1).sum(1))
            merged = self.activation(F.relu(directional[0] + directional[1]))
            merged = merged.transpose(1, 2).reshape(
                batch, self.config.channels, size, size
            )
            spatial = F.conv2d(
                merged,
                self.weight(self.spatial.weight),
                self.weight(self.spatial.bias),
                padding=1,
                groups=self.config.channels,
            )
            maps.append(self.activation(F.relu(spatial)))
        return torch.stack(maps, dim=1)

    def heads(self, maps, boards, to_move):
        batch, _, channels, size, _ = maps.shape
        selection = (to_move - 1).long()
        row = torch.arange(batch, device=maps.device)
        paired = torch.cat((maps[row, selection], maps[row, 1 - selection]), dim=1)
        global_mean = self.activation(paired.mean(dim=(-2, -1)))
        # Disjoint regions, exact floor boundaries, valid for both 15 and 20.
        regions = []
        for y in range(3):
            for x in range(3):
                region = paired[
                    :,
                    :,
                    y * size // 3 : (y + 1) * size // 3,
                    x * size // 3 : (x + 1) * size // 3,
                ]
                regions.append(self.activation(region.mean(dim=(-2, -1))))
        context = torch.stack(
            (
                (to_move == 1).float(),
                torch.full_like(to_move, size / 20, dtype=torch.float),
            ),
            dim=1,
        )
        context = self.activation(context)
        # Fixed zero padding aligns head inputs for CPU/XPU SIMD kernels.
        global_context = torch.cat(
            (global_mean, F.pad(context, (0, aligned_context(2 * channels) - 2))), dim=1
        )
        value_input = torch.cat(
            (
                global_mean,
                *regions,
                F.pad(context, (0, aligned_context(20 * channels) - 2)),
            ),
            dim=1,
        )
        hidden = self.activation(F.relu(self.linear(self.value_hidden, value_input)))
        value = self.linear(self.value_out, hidden).float()
        global_policy = self.linear(self.policy_global, global_context)
        local = self.linear(self.policy_local, paired.permute(0, 2, 3, 1).contiguous())
        policy_hidden = self.activation(F.relu(local + global_policy[:, None, None, :]))
        policy = (
            self.linear(self.policy_out, policy_hidden)
            .squeeze(-1)
            .reshape(batch, -1)
            .float()
        )
        policy = policy.masked_fill(boards.reshape(batch, -1) != 0, -1e9)
        return {"value": value, "policy": policy}

    def forward(self, boards, to_move):
        return self.heads(self.feature_maps(boards), boards, to_move)
