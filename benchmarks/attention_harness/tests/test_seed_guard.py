import pytest

from benchmarks.attention_harness.seed_guard import classify_seed, validate_seed


@pytest.mark.parametrize("seed", range(101, 126))
def test_dev_seeds(seed: int) -> None:
    assert validate_seed(seed) == "dev"


@pytest.mark.parametrize("seed", range(9001, 9006))
def test_smoke_seeds(seed: int) -> None:
    assert validate_seed(seed) == "smoke"


def test_heldout_is_guarded() -> None:
    assert classify_seed(1001) == "heldout"
    with pytest.raises(PermissionError):
        validate_seed(1001)
    assert validate_seed(1100, allow_heldout=True) == "heldout"


@pytest.mark.parametrize("seed", (0, 100, 126, 999, 1101, 9000, 9006))
def test_unknown_seed_rejected(seed: int) -> None:
    with pytest.raises(ValueError):
        validate_seed(seed)
