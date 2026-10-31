"""The committed seed inputs must retain the seed delivery's digest pins."""

import hashlib
from pathlib import Path


CONFIGS = Path(__file__).resolve().parents[1] / 'robocasa365/configs'


def test_seed_config_copies_match_sha256sums():
    entries = {}
    for line in (CONFIGS / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        entries[name.lstrip('*')] = digest
    assert entries
    for name, expected in entries.items():
        assert hashlib.sha256((CONFIGS / name).read_bytes()).hexdigest() == expected
