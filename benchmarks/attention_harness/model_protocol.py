"""Frozen D4/D5 model roles and prompts for the native Robosuite smoke."""

from __future__ import annotations

import base64
import binascii
import json
import re
import struct
import zlib
from typing import Any

import numpy as np

from .task_registry import get_task


DEVELOPER_MODEL = "parcc/GLM"
EVALUATOR_MODEL = "parcc/Qwen"


def developer_messages(task_id: str, observation: dict[str, np.ndarray]) -> list[dict[str, str]]:
    task = get_task(task_id)
    eef = np.asarray(observation["robot0_eef_pos"], dtype=float).round(4).tolist()
    image = observation.get("agentview_image")
    image_summary = None if image is None else {"shape": list(image.shape), "dtype": str(image.dtype)}
    prompt = f"""Write one short Python smoke-test policy for TidyBot's native Robosuite SDK.

Task: {task.prompt}
Public initial proprioception: robot0_eef_pos={eef}
Public camera metadata: {json.dumps(image_summary)}

The available API is exactly:
  from robot_sdk import sensors, arm, gripper
  sensors.get_observation() -> dict of public camera/proprioception numpy arrays
  sensors.pixel_to_world(u, v, camera="agentview", depth_meters=None) -> (x, y, z)
  arm.move_delta(dx, dy, dz, rotation_delta=(0, 0, 0))
  arm.move_to_position(x, y, z, tolerance=0.004, max_steps=100)
  gripper.open(settle_steps=10)
  gripper.close(settle_steps=10)

This is a connectivity smoke, not a success-optimized policy. Inspect the public observation,
print concise progress, and make at most 12 bounded SDK calls. Do not guess object coordinates.
Use only the robot_sdk import above and these safe Python builtins: print, range, len, min, max,
abs, all, any, sum, sorted, enumerate, zip, round, int, float, bool, str, list, tuple, dict, hasattr,
Exception, AttributeError, RuntimeError, TimeoutError. Do not import simulator packages,
read files, access the network, spawn processes, call evaluation/success APIs, or use dunder names.
Do not use a bare `except` or catch `Exception` broadly; unexpected failures must reach the harness.
Return only one Python code block, no explanation."""
    return [{"role": "user", "content": prompt}]


def evaluator_messages(
    task_id: str,
    initial_image: np.ndarray,
    final_image: np.ndarray,
    *,
    native_success: bool,
    execution_status: str,
) -> list[dict[str, Any]]:
    task = get_task(task_id)
    prompt = f"""Review this TidyBot Robosuite trial using only the two public camera images and
the execution summary. Goal: {task.prompt}
Execution status: {execution_status}
Robosuite native success (authoritative, do not override): {native_success}

Return compact JSON with keys visible_change, likely_goal_progress, concerns. Your review is
diagnostic only and must never replace the native success result. First image is before; second is after."""
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_data_url(initial_image)}},
            {"type": "image_url", "image_url": {"url": image_data_url(final_image)}},
        ],
    }]


def extract_python(text: str) -> str:
    match = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    code = (match.group(1) if match else text).strip()
    if not code:
        raise ValueError("model response contained no Python program")
    return code + "\n"


def image_data_url(image: np.ndarray) -> str:
    """Encode uint8 RGB as PNG using the standard library only."""
    array = np.asarray(image)
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError(f"expected HxWx3 image, got {array.shape}")
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)
    height, width, _ = array.shape
    scanlines = b"".join(b"\x00" + row.tobytes() for row in array)
    signature = b"\x89PNG\r\n\x1a\n"

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)
        )

    png = signature + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(scanlines, level=6)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
