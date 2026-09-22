"""Central seed split policy, including held-out leakage protection."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SeedSplit:
    name: str
    start: int
    stop: int

    def contains(self, seed: int) -> bool:
        return self.start <= seed <= self.stop


SPLITS = (
    SeedSplit("dev", 101, 125),
    SeedSplit("heldout", 1001, 1100),
    SeedSplit("smoke", 9001, 9005),
)


def classify_seed(seed: int) -> str:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("seed must be an integer")
    for split in SPLITS:
        if split.contains(seed):
            return split.name
    raise ValueError(f"seed {seed} is outside the frozen AttentionBench splits")


def validate_seed(seed: int, *, allow_heldout: bool = False) -> str:
    split = classify_seed(seed)
    if split == "heldout" and not allow_heldout:
        raise PermissionError(
            "held-out seeds require explicit allow_heldout=True; do not use them during development"
        )
    return split
