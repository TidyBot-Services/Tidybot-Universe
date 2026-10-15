"""Serve only advisor-visible camera evidence and public simulator frames."""

from __future__ import annotations

import base64
import hashlib
import io
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


def rgb_jpeg(array: np.ndarray) -> bytes:
    """Native camera dimensions, bounded JPEG transport rather than full PNG."""
    from PIL import Image
    value = np.asarray(array)
    if (value.dtype != np.uint8 or value.ndim != 3 or value.shape[2] != 3
            or not all(0 < size <= 2048 for size in value.shape[:2])):
        raise ValueError("camera frame must be uint8 RGB with approved dimensions")
    output = io.BytesIO()
    Image.fromarray(value).save(output, format="JPEG", quality=90)
    return output.getvalue()


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


def robocasa_camera_frame(ws_url: str, *, device_id: str = "maniskill_base",
                          max_wait_seconds: float = 5.0) -> bytes:
    """Read one color JPEG from the ManiSkill camera bridge, ignoring depth/state."""
    from websockets.sync.client import connect

    if device_id not in {"maniskill_base", "maniskill_wrist"}:
        raise ValueError("unsupported public camera device")

    deadline = time.monotonic() + max_wait_seconds
    with connect(ws_url, open_timeout=min(3, max_wait_seconds), close_timeout=1) as websocket:
        websocket.send(json.dumps({"action": "subscribe", "fps": 5, "quality": 75}))
        for _ in range(30):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            packet = websocket.recv(timeout=min(3, remaining))
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


def robocasa_human_rgb(origin: str) -> tuple[np.ndarray, float, int]:
    """Native display pixels and their producer clock; never pull policy RGB-D."""
    import math
    import re
    from PIL import Image
    if not isinstance(origin, str) or not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", origin):
        raise ValueError("human camera requires a loopback origin")
    with urlopen(origin + "/frame", timeout=2) as response:
        if response.headers.get_content_type() != "image/jpeg":
            raise ValueError("human camera returned non-JPEG content")
        captured_at = float(response.headers["X-Captured-At"])
        sequence = int(response.headers["X-Sequence"])
        frame = response.read(8_000_001)
    if (not math.isfinite(captured_at) or captured_at <= 0 or captured_at > time.time() + 2
            or sequence <= 0 or len(frame) > 8_000_000):
        raise ValueError("invalid human camera capture metadata")
    with Image.open(io.BytesIO(frame)) as image:
        if image.size != (1920, 1080) or image.format != "JPEG":
            raise ValueError("human camera did not return native 1080p")
        rgb = np.asarray(image.convert("RGB"))
    return rgb, captured_at, sequence


class AdvisorCameraRecorder:
    """Bounded, optional recording of a public camera stream for one attempt."""

    def __init__(self, episode_dir: Path, fetch_frame: Callable[[], bytes], *,
                 image_format: str, fps: int = 5, max_frames: int = 1500,
                 max_bytes: int = 64_000_000, on_frame: Callable[[bytes], None] | None = None) -> None:
        if image_format not in {"png", "jpg"}:
            raise ValueError("unsupported recording frame format")
        self.episode_dir = episode_dir
        self.fetch_frame = fetch_frame
        self.image_format = image_format
        self.fps = fps
        self.max_frames = max_frames
        self.max_bytes = max_bytes
        self.on_frame = on_frame
        self.frames: list[bytes] = []
        self.frame_times: list[float] = []
        self._bytes = 0
        self.truncated = False
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("recorder already started")
        self._thread = threading.Thread(target=self._capture, daemon=True)
        self._thread.start()

    def _capture(self) -> None:
        while not self._stop.is_set() and (self.on_frame is not None or len(self.frames) < self.max_frames):
            start = time.monotonic()
            try:
                frame = self.fetch_frame()
                if isinstance(frame, bytes) and len(frame) <= 8_000_000:
                    if self.on_frame is not None:
                        self.on_frame(frame)
                    if len(self.frames) >= self.max_frames or self._bytes + len(frame) > self.max_bytes:
                        self.truncated = True
                        if self.on_frame is None:
                            break
                    else:
                        self.frames.append(frame)
                        self.frame_times.append(time.monotonic())
                        self._bytes += len(frame)
            except Exception as error:
                self.error = f"{type(error).__name__}: {error}"
            self._stop.wait(max(0.0, 1 / self.fps - (time.monotonic() - start)))

    def finish_sampling(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.truncated |= len(self.frames) >= self.max_frames

    def stop(self) -> Path | None:
        self.finish_sampling()
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
            # Preserve observed wall-time gaps; a slow camera must not produce
            # a misleading accelerated replay at the requested sampling FPS.
            durations = [max(.04, b - a) for a, b in zip(self.frame_times, self.frame_times[1:])]
            durations.append(1 / self.fps)
            listing = frame_dir / "frames.txt"
            lines = []
            for index, duration in enumerate(durations):
                lines += [f"file 'frame-{index:04d}.{self.image_format}'", f"duration {duration:.6f}"]
            lines.append(f"file 'frame-{len(self.frames)-1:04d}.{self.image_format}'")
            listing.write_text("\n".join(lines) + "\n")
            result = subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                 "-i", str(listing), "-vsync", "vfr",
                 "-an", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
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


class AttemptCameraRecording:
    """Record only public RGB while the owning Service context is still alive.

    Camera failure never changes the native verdict. Receipt files distinguish
    missing/truncated video from a successfully recorded physical execution.
    """

    def __init__(self, episode_dir: Path, fetch_frame: Callable[[], bytes], *,
                 run_dir: Path | None = None, defer_encoding: bool = False,
                 fetch_rgb: Callable[[], tuple[np.ndarray, float, int]] | None = None,
                 run_id: str | None = None, attempt_id: str | None = None,
                 suite: str = "robosuite", fps: int = 5) -> None:
        if suite not in {"robosuite", "robocasa"}:
            raise ValueError("unsupported recording suite")
        self.suite = suite
        self.episode_dir = episode_dir
        self.run_dir = run_dir
        self.defer_encoding = defer_encoding
        self.fetch_frame = fetch_frame
        self.fetch_rgb = fetch_rgb
        self.run_id, self.attempt_id = run_id, attempt_id
        self._source_sample = None
        self._jpeg_sequence = None
        self._jpeg = None
        self._published_sequence = None
        self._trace_origin: float | None = None
        # JPEG is a recording transport, never a resize. Initial/final PNGs
        # retain full source pixels, while bounded 1080p capture uses far less RAM.
        def recording_frame() -> bytes:
            if self.fetch_rgb is not None:
                image, captured_at, sequence = self.fetch_rgb()
                self._source_sample = (image.shape[1], image.shape[0], captured_at, sequence)
                if sequence != self._jpeg_sequence:
                    self._jpeg = rgb_jpeg(image)
                    self._jpeg_sequence = sequence
                return self._jpeg
            from PIL import Image
            with Image.open(io.BytesIO(fetch_frame())) as image:
                output = io.BytesIO()
                image.convert("RGB").save(output, format="JPEG", quality=90)
                return output.getvalue()
        self.recorder = AdvisorCameraRecorder(episode_dir, recording_frame,
            image_format="jpg", fps=fps, max_bytes=400_000_000,
            on_frame=self._publish_preview if fetch_rgb and run_dir and run_id and attempt_id else None)
        self.receipt: dict[str, Any] = {"schema_version": "attentionbench.camera-recording.v1",
            "source": "public_rgb_observation", "requested_fps": self.recorder.fps,
            "initial_frame_saved": False, "final_frame_saved": False, "error": None}

    def _publish_preview(self, frame: bytes) -> None:
        if self._source_sample is None or self._published_sequence == self._source_sample[3]:
            return
        from .live_preview import save_preview
        width, height, captured_at, sequence = self._source_sample
        save_preview(self.run_dir, run_id=self.run_id, attempt_id=self.attempt_id,
                     frame=frame, sequence=sequence, captured_at=captured_at, width=width, height=height)
        self._published_sequence = sequence

    def bind_trace_origin(self, monotonic_time: float) -> None:
        """Use the SDK emitter's first clock read, not the attempt start time."""
        if self._trace_origin is None:
            self._trace_origin = monotonic_time

    def _frame(self, stage: str) -> None:
        try:
            if self.fetch_rgb:
                image, captured_at, sequence = self.fetch_rgb()
                frame = rgb_png(image)
                if stage == 'final' and self.recorder.on_frame is not None:
                    self._source_sample = (image.shape[1], image.shape[0], captured_at, sequence)
                    self._publish_preview(rgb_jpeg(image))
            else:
                frame = self.fetch_frame()
                if frame.startswith(b"\xff\xd8") and len(frame) <= 8_000_000:
                    from PIL import Image
                    with Image.open(io.BytesIO(frame)) as image:
                        frame = rgb_png(np.asarray(image.convert("RGB")))
            if not frame.startswith(b"\x89PNG\r\n\x1a\n") or len(frame) > 8_000_000:
                raise ValueError("invalid public camera PNG")
            width, height = struct.unpack(">II", frame[16:24])
            self.episode_dir.mkdir(parents=True, exist_ok=True)
            (self.episode_dir / f"camera_{stage}.png").write_bytes(frame)
            self.receipt.update(source_width=width, source_height=height)
            self.receipt[f"{stage}_frame_saved"] = True
            self.receipt[f"{stage}_captured_at"] = time.time()
            if self.run_dir is not None:
                from .public_station import save_public_frame
                cached = frame
                if self.suite == "robocasa":
                    from PIL import Image
                    with Image.open(io.BytesIO(frame)) as image:
                        cached = rgb_jpeg(np.asarray(image.convert("RGB")))
                save_public_frame(self.run_dir, suite=self.suite, frame=cached)
        except Exception as error:
            self.receipt["error"] = f"{type(error).__name__}: {error}"[:500]

    def __enter__(self) -> "AttemptCameraRecording":
        self._frame("initial")
        self.recorder.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.recorder.finish_sampling()
        self._frame("final")
        if self.recorder.on_frame is not None and not self.recorder._thread.is_alive():
            from .live_preview import finish_preview
            finish_preview(self.run_dir, self.run_id, self.attempt_id)
        if not self.defer_encoding:
            self.finalize()

    def finalize(self) -> None:
        """Encode after native evaluation/Service shutdown when requested."""
        video = None
        try:
            video = self.recorder.stop()
        except Exception as error:
            self.receipt["error"] = f"{type(error).__name__}: {error}"[:500]
        self.receipt.update(video_saved=video is not None, frame_count=len(self.recorder.frames),
            truncated=self.recorder.truncated,
            sampled_seconds=(self.recorder.frame_times[-1] - self.recorder.frame_times[0])
                if len(self.recorder.frame_times) > 1 else 0,
            error=self.receipt["error"] or self.recorder.error)
        if video is not None and self._trace_origin is not None and self.recorder.frame_times:
            # Match the concat encoder's actual sample gaps, including its floor.
            video_times = [0.0]
            for a, b in zip(self.recorder.frame_times, self.recorder.frame_times[1:]):
                video_times.append(video_times[-1] + max(.04, b - a))
            self.receipt['replay_timing'] = {
                'basis': 'relative',
                'event_clock': 'sdk_completion',
                'frame_trace_seconds': [round(t - self._trace_origin, 6)
                                        for t in self.recorder.frame_times],
                'frame_video_seconds': [round(t, 6) for t in video_times],
            }
        self.episode_dir.mkdir(parents=True, exist_ok=True)
        (self.episode_dir / "camera_recording.json").write_text(
            json.dumps(self.receipt, sort_keys=True) + "\n", encoding="utf-8")
