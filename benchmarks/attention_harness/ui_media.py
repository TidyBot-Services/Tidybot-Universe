"""Serve only advisor-visible camera evidence and public simulator frames."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import zlib
from pathlib import Path
from typing import Any, Callable
from urllib.request import urlopen

import numpy as np


def _public_image_key(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith("_image") and not any(
        marker in lowered for marker in ("oracle", "segmentation", "depth", "reward", "privileged", "state")
    )


def rgb_png(array: np.ndarray) -> bytes:
    value = np.asarray(array)
    if value.dtype != np.uint8 or value.ndim != 3 or value.shape[2] != 3:
        raise ValueError("camera frame must be uint8 RGB")
    height, width, _ = value.shape
    if not 0 < width <= 2048 or not 0 < height <= 2048:
        raise ValueError("invalid camera frame dimensions")
    rows = b"".join(b"\0" + value[row].tobytes() for row in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 3)) + chunk(b"IEND", b""))


def encoded_observation_frame(payload: dict[str, Any], camera_name: str | None = None) -> bytes:
    observation = payload.get("observation")
    if not isinstance(observation, dict):
        raise ValueError("simulator did not return an observation")
    keys = [camera_name + "_image"] if camera_name else sorted(
        key for key in observation if _public_image_key(key)
    )
    for key in keys:
        if not _public_image_key(key):
            continue
        encoded = observation.get(key)
        if not isinstance(encoded, dict) or encoded.get("dtype") != "uint8":
            continue
        shape = encoded.get("shape")
        if not isinstance(shape, list) or len(shape) != 3 or shape[2] != 3:
            continue
        height, width, _ = shape
        if not all(isinstance(x, int) and 0 < x <= 2048 for x in (height, width)):
            continue
        raw = base64.b64decode(encoded.get("data", ""), validate=True)
        if len(raw) != height * width * 3:
            continue
        return rgb_png(np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3)))
    raise ValueError("no public RGB camera frame available")


def robosuite_camera_frame(service_url: str, camera_name: str | None = None) -> bytes:
    with urlopen(service_url.rstrip("/") + "/v1/observation", timeout=3) as response:
        return encoded_observation_frame(json.load(response), camera_name)


def robocasa_camera_frame(ws_url: str, *, device_id: str = "maniskill_base") -> bytes:
    """Read one color JPEG from the ManiSkill camera bridge, ignoring depth/state."""
    from websockets.sync.client import connect

    if device_id not in {"maniskill_base", "maniskill_wrist"}:
        raise ValueError("unsupported public camera device")

    with connect(ws_url, open_timeout=3, close_timeout=1) as websocket:
        websocket.send(json.dumps({"action": "subscribe", "fps": 5, "quality": 75}))
        for _ in range(30):
            packet = websocket.recv(timeout=3)
            if not isinstance(packet, bytes) or len(packet) < 8:
                continue
            header_length = struct.unpack(">I", packet[:4])[0]
            if header_length > 4096 or len(packet) <= 4 + header_length:
                continue
            header = json.loads(packet[4:4 + header_length])
            data = header.get("data") or {}
            frame = packet[4 + header_length:]
            if (header.get("type") == 11 and data.get("device_id") == device_id
                    and data.get("stream_type") == "color" and data.get("format") == "jpeg"
                    and len(frame) <= 8_000_000 and frame.startswith(b"\xff\xd8")
                    and frame.endswith(b"\xff\xd9")):
                return frame
    raise ValueError("camera bridge returned no valid public RGB frame")


class AdvisorCameraRecorder:
    """Bounded, optional recording of a public camera stream for one attempt."""

    def __init__(self, episode_dir: Path, fetch_frame: Callable[[], bytes], *,
                 image_format: str, fps: int = 5, max_frames: int = 1500) -> None:
        if image_format not in {"png", "jpg"}:
            raise ValueError("unsupported recording frame format")
        self.episode_dir = episode_dir
        self.fetch_frame = fetch_frame
        self.image_format = image_format
        self.fps = fps
        self.max_frames = max_frames
        self.frames: list[bytes] = []
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("recorder already started")
        self._thread = threading.Thread(target=self._capture, daemon=True)
        self._thread.start()

    def _capture(self) -> None:
        while not self._stop.is_set() and len(self.frames) < self.max_frames:
            start = time.monotonic()
            try:
                frame = self.fetch_frame()
                if isinstance(frame, bytes) and len(frame) <= 8_000_000:
                    self.frames.append(frame)
            except Exception as error:
                self.error = f"{type(error).__name__}: {error}"
            self._stop.wait(max(0.0, 1 / self.fps - (time.monotonic() - start)))

    def stop(self) -> Path | None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if not self.frames:
            return None
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            self.error = "ffmpeg unavailable"
            return None
        self.episode_dir.mkdir(parents=True, exist_ok=True)
        target = self.episode_dir / "advisor_replay.mp4"
        with tempfile.TemporaryDirectory(prefix="attention-frames-") as temporary:
            frame_dir = Path(temporary)
            for index, frame in enumerate(self.frames):
                (frame_dir / f"frame-{index:04d}.{self.image_format}").write_bytes(frame)
            result = subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error", "-framerate", str(self.fps),
                 "-i", str(frame_dir / f"frame-%04d.{self.image_format}"),
                 "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-movflags", "+faststart", str(target)],
                capture_output=True, timeout=30, check=False,
            )
        if result.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
            target.unlink(missing_ok=True)
            self.error = (result.stderr.decode("utf-8", "replace") or "ffmpeg failed")[:500]
            return None
        return target


def authorized_evidence_path(root: Path, trace: dict[str, Any], evidence_id: str) -> tuple[Path, str]:
    evidence = next((item for item in trace.get("evidence", [])
                     if item.get("evidence_id") == evidence_id), None)
    if evidence is None or "advisor" not in evidence.get("visibility", []):
        raise FileNotFoundError("advisor-visible evidence not found")
    uri = evidence.get("uri", "")
    if not isinstance(uri, str) or not uri.startswith("artifact://"):
        raise FileNotFoundError("unsupported evidence URI")
    parts = uri.removeprefix("artifact://").split("/")
    if len(parts) != 2 or any(part in {"", ".", ".."} or "\\" in part for part in parts):
        raise FileNotFoundError("invalid evidence URI")
    direct = root / parts[0]
    if direct.is_dir():
        episode_dir = direct
    else:
        # Shared stores contain many scheduler runs, each with an attempts/
        # directory. Resolve an episode by its stable ID, never by client path.
        matches = list(root.glob(f"*/attempts/{parts[0]}"))
        if len(matches) != 1:
            raise FileNotFoundError("episode artifact directory not found or ambiguous")
        episode_dir = matches[0]
    path = (episode_dir / parts[1]).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise FileNotFoundError("evidence file missing")
    if path.stat().st_size > 100_000_000:
        raise ValueError("evidence file too large")
    if hashlib.sha256(path.read_bytes()).hexdigest() != evidence.get("sha256"):
        raise ValueError("evidence hash mismatch")
    return path, str(evidence.get("mime_type") or "")


def render_evidence(path: Path, mime_type: str) -> tuple[bytes, str]:
    if mime_type == "application/x-npz":
        with np.load(path, allow_pickle=False) as archive:
            names = sorted(name for name in archive.files if _public_image_key(name))
            if not names:
                raise ValueError("observation has no RGB camera image")
            return rgb_png(archive[names[0]]), "image/png"
    if mime_type in {"image/png", "image/jpeg", "video/mp4", "video/webm"}:
        return path.read_bytes(), mime_type
    raise ValueError("evidence is not displayable media")
