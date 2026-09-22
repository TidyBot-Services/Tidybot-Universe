"""Compare native evaluator decisions with exported legacy baseline results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        value = json.loads(text)
        if not isinstance(value, list):
            raise ValueError(f"{path} must contain a JSON array or JSONL")
        return value
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def compare(native: list[dict[str, Any]], legacy: list[dict[str, Any]]) -> dict[str, Any]:
    def index(rows: list[dict[str, Any]]) -> dict[tuple[str, int], bool]:
        return {
            (str(row["task_id"]), int(row["seed"])): bool(row["native_success"])
            for row in rows
        }

    native_index = index(native)
    legacy_index = index(legacy)
    common = sorted(native_index.keys() & legacy_index.keys())
    mismatches = [
        {"task_id": task, "seed": seed, "native": native_index[(task, seed)], "legacy": legacy_index[(task, seed)]}
        for task, seed in common
        if native_index[(task, seed)] != legacy_index[(task, seed)]
    ]
    return {
        "compared": len(common),
        "matches": len(common) - len(mismatches),
        "agreement": (len(common) - len(mismatches)) / len(common) if common else None,
        "missing_in_native": [list(item) for item in sorted(legacy_index.keys() - native_index.keys())],
        "missing_in_legacy": [list(item) for item in sorted(native_index.keys() - legacy_index.keys())],
        "mismatches": mismatches,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native", type=Path)
    parser.add_argument("legacy", type=Path)
    args = parser.parse_args()
    report = compare(_load(args.native), _load(args.legacy))
    print(json.dumps(report, indent=2, sort_keys=True))
    complete = not report["missing_in_native"] and not report["missing_in_legacy"]
    return 0 if complete and report["agreement"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
