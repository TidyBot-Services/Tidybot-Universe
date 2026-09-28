"""Run-bound public RGB station handoff between a formal Runner and local UI."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


_LOOPBACK_HTTP = re.compile(r"http://127\.0\.0\.1:(\d{1,5})\Z")
_LOOPBACK_WS = re.compile(r"ws://127\.0\.0\.1:(\d{1,5})\Z")


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def publish_public_station(*, run_dir: Path, run_id: str, attempt_id: str,
                           suite: str, origin: str, camera_name: str | None = None,
                           device_id: str | None = None) -> dict[str, Any]:
    if suite not in {"robosuite", "robocasa"}:
        raise ValueError("unsupported public station suite")
    pattern = _LOOPBACK_HTTP if suite == "robosuite" else _LOOPBACK_WS
    match = pattern.fullmatch(origin)
    if match is None or not 1 <= int(match[1]) <= 65535:
        raise ValueError("public station must use a loopback Service origin")
    if run_id != f"run:{run_dir.name}" or not attempt_id.startswith(f"attempt:{run_dir.name}:"):
        raise ValueError("public station run/attempt identity mismatch")
    if suite == "robocasa" and device_id not in {"maniskill_base", "maniskill_wrist"}:
        raise ValueError("public station needs an approved RoboCasa camera")
    payload = {"schema_version": "attentionbench.public-station.v1", "run_id": run_id,
               "attempt_id": attempt_id, "suite": suite, "origin": origin,
               "camera_name": camera_name if suite == "robosuite" else None,
               "device_id": device_id if suite == "robocasa" else None,
               "published_at": time.time()}
    _atomic_bytes(run_dir / "public_station.json",
                  (json.dumps(payload, sort_keys=True) + "\n").encode())
    return payload


def save_public_frame(run_dir: Path, *, suite: str, frame: bytes) -> dict[str, Any]:
    if suite == "robosuite":
        filename, signature = "public_frame.png", b"\x89PNG\r\n\x1a\n"
    elif suite == "robocasa":
        filename, signature = "public_frame.jpg", b"\xff\xd8"
    else:
        raise ValueError("unsupported public frame suite")
    if not isinstance(frame, bytes) or not frame.startswith(signature) or not 0 < len(frame) <= 8_000_000:
        raise ValueError("invalid public RGB frame")
    _atomic_bytes(run_dir / filename, frame)
    receipt = {"schema_version": "attentionbench.public-frame.v1",
               "suite": suite, "uri": filename,
               "sha256": hashlib.sha256(frame).hexdigest(), "captured_at": time.time()}
    _atomic_bytes(run_dir / "public_frame_receipt.json",
                  (json.dumps(receipt, sort_keys=True) + "\n").encode())
    return receipt


def read_public_frame(run_dir: Path, suite: str) -> tuple[bytes, str] | None:
    filename = "public_frame.png" if suite == "robosuite" else "public_frame.jpg"
    mime = "image/png" if suite == "robosuite" else "image/jpeg"
    try:
        receipt_path = run_dir / "public_frame_receipt.json"
        if receipt_path.is_symlink():
            return None
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        path = run_dir / filename
        if path.is_symlink() or receipt.get("schema_version") != "attentionbench.public-frame.v1" \
                or receipt.get("suite") != suite or receipt.get("uri") != filename:
            return None
        frame = path.read_bytes()
        if hashlib.sha256(frame).hexdigest() != receipt.get("sha256"):
            return None
        return frame, mime
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def read_public_station(root: Path, run_id: str, suite: str,
                        attempt_ids: set[str], *, allow_pending: bool = False) -> dict[str, Any] | None:
    if not run_id.startswith("run:attention-"):
        return None
    name = run_id.removeprefix("run:")
    if "/" in name or "\\" in name or name in {".", ".."}:
        return None
    run_dir = (root / name).resolve()
    if not run_dir.is_relative_to(root.resolve()):
        return None
    path = run_dir / "public_station.json"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        pattern = _LOOPBACK_HTTP if suite == "robosuite" else _LOOPBACK_WS
        origin = payload.get("origin")
        match = pattern.fullmatch(origin) if isinstance(origin, str) else None
        if (payload.get("schema_version") != "attentionbench.public-station.v1"
                or payload.get("run_id") != run_id or payload.get("suite") != suite
                or match is None or not 1 <= int(match[1]) <= 65535):
            return None
        attempt_id = payload.get("attempt_id")
        prefix = f"attempt:{name}:"
        if not isinstance(attempt_id, str) or not attempt_id.startswith(prefix):
            return None
        index = attempt_id.removeprefix(prefix)
        if not index.isdecimal() or not 0 <= int(index) < 10:
            return None
        if attempt_id not in attempt_ids and not allow_pending:
            return None
        if suite == "robocasa" and payload.get("device_id") not in {
                "maniskill_base", "maniskill_wrist"}:
            return None
        return payload
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
