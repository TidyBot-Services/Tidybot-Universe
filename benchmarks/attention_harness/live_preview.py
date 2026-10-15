"""Latest-only public RGB handoff. No queues, Service calls or robot commands."""
from __future__ import annotations

import hashlib
import json
import math
import struct
import time
from pathlib import Path
from typing import Any

from .public_station import _atomic_bytes

_MAGIC = b'ABRGB1\n'
_SCHEMA = 'attentionbench.public-preview.v1'
_MAX_FRAME = 8_000_000


def _path(run_dir: Path, run_id: str, attempt_id: str, view: str | None = None) -> Path:
    name = run_id.removeprefix('run:')
    prefix = f'attempt:{name}:'
    index = attempt_id.removeprefix(prefix)
    if (not run_id.startswith('run:attention-') or name != run_dir.name
            or '/' in name or '\\' in name or not attempt_id.startswith(prefix)
            or not index.isdecimal() or not 0 <= int(index) < 10):
        raise ValueError('preview run/attempt identity mismatch')
    if view is not None and view not in {'chest','wrist','external','left_wrist','right_wrist'}:
        raise ValueError('unsupported camera view')
    suffix='-'+view if view else ''
    return run_dir / f'public_preview-{int(index)}{suffix}.frame'


def _write(path: Path, header: dict[str, Any], frame: bytes) -> None:
    encoded = json.dumps(header, sort_keys=True, allow_nan=False).encode()
    if len(encoded) > 4096:
        raise ValueError('preview header too large')
    _atomic_bytes(path, _MAGIC + struct.pack('>I', len(encoded)) + encoded + frame)


def save_preview(run_dir: Path, *, run_id: str, attempt_id: str, frame: bytes,
                 sequence: int, captured_at: float, width: int, height: int,
                 view: str | None = None) -> None:
    path = _path(run_dir, run_id, attempt_id, view)
    if (not isinstance(frame, bytes) or not frame.startswith(b'\xff\xd8')
            or not frame.endswith(b'\xff\xd9') or not 0 < len(frame) <= _MAX_FRAME
            or type(sequence) is not int or sequence < 1
            or not isinstance(captured_at, (int, float)) or not math.isfinite(captured_at)
            or captured_at <= 0 or any(type(v) is not int or not 0 < v <= 2048 for v in (width, height))):
        raise ValueError('invalid public RGB preview')
    _write(path, {'schema_version': _SCHEMA, 'run_id': run_id, 'attempt_id': attempt_id,
        'sequence': sequence, 'captured_at': captured_at, 'source_width': width,
        'source_height': height, 'source': 'executor_public_rgb',
        'timestamp_basis': 'physical_snapshot_wall_clock' if view else 'executor_observation_received_wall_clock',
        'sha256': hashlib.sha256(frame).hexdigest(), 'ended_at': None}, frame)


def read_preview(run_dir: Path, run_id: str, attempt_id: str, view: str | None = None) -> tuple[dict[str, Any], bytes] | None:
    try:
        path = _path(run_dir, run_id, attempt_id, view)
        if path.is_symlink() or path.stat().st_size > _MAX_FRAME + 4107:
            return None
        packet = path.read_bytes()
        offset = len(_MAGIC) + 4
        if not packet.startswith(_MAGIC) or len(packet) < offset:
            return None
        size = struct.unpack('>I', packet[len(_MAGIC):offset])[0]
        if not 0 < size <= 4096:
            return None
        header = json.loads(packet[offset:offset+size])
        frame = packet[offset+size:]
        if (header.get('schema_version') != _SCHEMA or header.get('run_id') != run_id
                or header.get('attempt_id') != attempt_id
                or type(header.get('sequence')) is not int or header['sequence'] < 1
                or not isinstance(header.get('captured_at'), (float, int))
                or not math.isfinite(header['captured_at']) or header['captured_at'] <= 0
                or any(type(header.get(k)) is not int or not 0 < header[k] <= 2048
                       for k in ('source_width', 'source_height'))
                or not frame.startswith(b'\xff\xd8') or not frame.endswith(b'\xff\xd9')
                or hashlib.sha256(frame).hexdigest() != header.get('sha256')):
            return None
        return header, frame
    except (OSError, ValueError, TypeError, AttributeError, struct.error):
        return None


def finish_preview(run_dir: Path, run_id: str, attempt_id: str, view: str | None = None) -> None:
    packet = read_preview(run_dir, run_id, attempt_id, view)
    if packet:
        header, frame = packet
        header['ended_at'] = time.time()
        _write(_path(run_dir, run_id, attempt_id, view), header, frame)


def mark_preview_offline(run_dir: Path, run_id: str, attempt_id: str, error: str, view: str | None = None) -> None:
    packet = read_preview(run_dir, run_id, attempt_id, view)
    if packet:
        header, frame = packet
        header["producer_error"] = str(error)[:512]
        _write(_path(run_dir, run_id, attempt_id, view), header, frame)
