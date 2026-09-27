"""Durable UI launch supervisor; its receipt survives a UI server restart."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .core.store import AttentionStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-path", type=Path, required=True)
    parser.add_argument("--launch-id", required=True)
    parser.add_argument("--summary-path", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("missing runner command")
    exit_code = 127
    reason = None
    try:
        exit_code = subprocess.run(command, check=False).returncode
    except OSError as error:
        reason = f"{type(error).__name__}: {error}"[:300]
    summary = None
    if args.summary_path.is_file():
        try:
            summary = json.loads(args.summary_path.read_text(encoding="utf-8"))
            if not isinstance(summary, dict) or summary.get("formal_eligible") is not False:
                raise ValueError("invalid formal run summary")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            summary = None
            reason = f"invalid runner summary: {error}"[:300]
    if summary is None and reason is None:
        reason = f"runner exited {exit_code} without a formal run summary"
    AttentionStore(args.store_path).record_launch_terminal(
        args.launch_id, exit_code=exit_code, summary=summary, reason=reason)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
