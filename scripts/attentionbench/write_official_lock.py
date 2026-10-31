#!/usr/bin/env python3
"""Run r12-compatible lock collection with the newly installed cuRobo Python."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.attentionbench.official_lock import build

parser = argparse.ArgumentParser(description=__doc__)
for name in ('library', 'project', 'report', 'output'):
    parser.add_argument('--' + name, type=Path, required=True)
parser.add_argument('--native-host', nargs='+', type=Path, required=True)
args = parser.parse_args()
build(args.library, args.project, args.report, args.output, args.native_host)
