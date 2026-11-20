#!/usr/bin/env python3
"""Disk pressure markers using abctl's data/stop-new-runs convention.

Code only: installing the user units is a separate operator action. A recovered
disk clears disk-low.json, but never clears the operator's stop-new-runs latch.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile

GIB = 1024**3


def default_data():
    # Same defaults and optional config as abctl.config(), without importing the
    # control program or probing its services.
    config = Path.home()/'.config/abctl.json'
    value = json.loads(config.read_text()) if config.is_file() else {}
    return Path(os.environ.get('ATTENTIONBENCH_DATA', value.get('data', str(Path.home()/'ab-campus-data'))))


def check(data, *, disk=None, low_bytes=130*GIB, stop_bytes=60*GIB, dry_run=False, usage=shutil.disk_usage):
    data = Path(data)
    if not 0 <= stop_bytes <= low_bytes:
        raise ValueError('require 0 <= stop threshold <= low threshold')
    free = usage(Path(disk) if disk is not None else data).free
    value = dict(free_bytes=free, threshold_bytes=low_bytes, stop_threshold_bytes=stop_bytes,
                 time=datetime.now(timezone.utc).isoformat(), low=free < low_bytes,
                 stop=free < stop_bytes, data=str(data), dry_run=dry_run)
    if dry_run:
        return value
    data.mkdir(parents=True, exist_ok=True)
    marker = data/'disk-low.json'
    if value['low']:
        fd, name = tempfile.mkstemp(prefix='.disk-low-', dir=data)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(value, stream, sort_keys=True); stream.write('\n')
                stream.flush(); os.fsync(stream.fileno())
            os.replace(name, marker)
        finally:
            Path(name).unlink(missing_ok=True)
    else:
        marker.unlink(missing_ok=True)
    if value['stop']:
        (data/'stop-new-runs').touch()
    fd = os.open(data, os.O_DIRECTORY | os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path)
    parser.add_argument('--disk', type=Path)
    parser.add_argument('--low-gib', type=float, default=130)
    parser.add_argument('--stop-gib', type=float, default=60)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    print(json.dumps(check(args.data or default_data(), disk=args.disk,
                           low_bytes=int(args.low_gib*GIB), stop_bytes=int(args.stop_gib*GIB),
                           dry_run=args.dry_run), sort_keys=True))


if __name__ == '__main__':
    main()
