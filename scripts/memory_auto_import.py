#!/usr/bin/env python3
"""Offline importer for provisional Memory proposals; see
benchmarks/attention_harness/memory_auto_import.py for usage."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks.attention_harness.memory_auto_import import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
