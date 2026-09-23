"""Strictly compare native evaluator decisions with legacy evidence."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


Key = tuple[str, int, str]


@dataclass(frozen=True)
class Dataset:
    records: list[dict[str, Any]]
    metadata: dict[str, Any]


def load_dataset(path: Path) -> Dataset:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return Dataset([], {})
    if text.startswith("{"):
        value = json.loads(text)
        records = value.get("records")
        if not isinstance(records, list):
            raise ValueError(f"{path} dataset object must contain a records array")
        return Dataset(records, dict(value.get("metadata") or {}))
    if text.startswith("["):
        value = json.loads(text)
        if not isinstance(value, list):
            raise ValueError(f"{path} must contain a dataset object, JSON array, or JSONL")
        return Dataset(value, {})
    return Dataset([json.loads(line) for line in text.splitlines() if line.strip()], {})


def _index(rows: list[dict[str, Any]], label: str) -> dict[Key, bool]:
    indexed: dict[Key, bool] = {}
    for row_number, row in enumerate(rows, start=1):
        success = row.get("native_success")
        if not isinstance(success, bool):
            raise ValueError(f"{label} row {row_number} native_success must be Boolean")
        key = (
            str(row["task_id"]),
            int(row["seed"]),
            str(row.get("probe_id", "episode")),
        )
        if key in indexed:
            raise ValueError(f"{label} contains duplicate key {key}")
        indexed[key] = success
    return indexed


def compare(
    native: list[dict[str, Any]],
    legacy: list[dict[str, Any]],
    *,
    native_metadata: dict[str, Any] | None = None,
    legacy_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    native_index = _index(native, "native")
    legacy_index = _index(legacy, "legacy")
    common = sorted(native_index.keys() & legacy_index.keys())
    mismatches = [
        {
            "task_id": task,
            "seed": seed,
            "probe_id": probe,
            "native": native_index[(task, seed, probe)],
            "legacy": legacy_index[(task, seed, probe)],
        }
        for task, seed, probe in common
        if native_index[(task, seed, probe)] != legacy_index[(task, seed, probe)]
    ]
    native_metadata = native_metadata or {}
    legacy_metadata = legacy_metadata or {}
    native_predicates = native_metadata.get("predicate_ast_sha256")
    legacy_predicates = legacy_metadata.get("predicate_ast_sha256")
    predicate_source_match = (
        native_predicates == legacy_predicates
        if native_predicates is not None and legacy_predicates is not None
        else None
    )
    native_versions = native_metadata.get("versions")
    legacy_versions = legacy_metadata.get("versions")
    version_match = (
        native_versions == legacy_versions
        if native_versions is not None and legacy_versions is not None
        else None
    )
    return {
        "schema_version": "attentionbench.evaluator-parity.v1",
        "compared": len(common),
        "matches": len(common) - len(mismatches),
        "agreement": (len(common) - len(mismatches)) / len(common) if common else None,
        "complete_keyset": native_index.keys() == legacy_index.keys() and bool(native_index),
        "predicate_source_match": predicate_source_match,
        "version_match": version_match,
        "native_versions": native_versions,
        "legacy_versions": legacy_versions,
        "known_environment_differences": native_metadata.get("known_environment_differences")
        or legacy_metadata.get("known_environment_differences")
        or [],
        "missing_in_native": [list(item) for item in sorted(legacy_index.keys() - native_index.keys())],
        "missing_in_legacy": [list(item) for item in sorted(native_index.keys() - legacy_index.keys())],
        "mismatches": mismatches,
    }


def passed(report: dict[str, Any]) -> bool:
    return bool(
        report["complete_keyset"]
        and report["agreement"] == 1.0
        and report["predicate_source_match"] is not False
        and report["version_match"] is not False
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native", type=Path)
    parser.add_argument("legacy", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    native = load_dataset(args.native)
    legacy = load_dataset(args.legacy)
    report = compare(
        native.records,
        legacy.records,
        native_metadata=native.metadata,
        legacy_metadata=legacy.metadata,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if passed(report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
