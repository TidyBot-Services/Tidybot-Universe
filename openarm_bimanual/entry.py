"""Resolve the daily full catalog without modifying earlier approved catalogs."""
from pathlib import Path
import hashlib
import json


def resolve_daily_catalog(root=None, *, override=None):
    root=Path(root or Path(__file__).resolve().parents[1]).resolve()
    if override:
        path=Path(override).resolve()
    else:
        directory=root/'openarm_bimanual'
        pointer=directory/'current.json'
        if pointer.is_file():
            binding=json.loads(pointer.read_bytes())
            path=(directory/binding['catalog']).resolve()
            if (not path.is_relative_to(directory/'launch_configs') or not path.is_file()
                    or hashlib.sha256(path.read_bytes()).hexdigest()!=binding.get('sha256')):
                raise ValueError('Daily catalog identity mismatch')
        else:
            path=root/'benchmarks/attention_harness/protocol/v2/skill_graphs/live_run_catalog.json'
    if not path.is_file():raise ValueError('Daily catalog is missing')
    return path.resolve()
