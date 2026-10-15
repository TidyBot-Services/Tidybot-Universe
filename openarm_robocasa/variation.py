"""Stable, observation-derived identities for AttentionBench variation cases."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()[:20]


def _vector(value: Any) -> list[float]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("fixture geometry is unavailable")
    result = []
    for coordinate in value:
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)) or not math.isfinite(coordinate):
            raise ValueError("fixture geometry is invalid")
        result.append(round(float(coordinate), 4))
    return result


def _fixtures_signature(configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    positioned = 0
    for config in configs:
        fixture = config.get("model")
        name = config.get("name")
        if not isinstance(name, str) or not name or fixture is None:
            raise ValueError("fixture identity is unavailable")
        item = {
            "name": name,
            "type": type(fixture).__name__,
        }
        for attr in ("pos", "size", "quat"):
            value = getattr(fixture, attr, None)
            if value is not None:
                item[attr] = _vector(value)
        positioned += int("pos" in item and "size" in item)
        result.append(item)
    if not result or positioned < 2:
        raise ValueError("fixture configuration lacks spatial geometry")
    return sorted(result, key=lambda item: item["name"])


def variation_identity(
    ep_meta: dict[str, Any], objects: list[dict[str, Any]],
    fixture_configs: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Identify the realized scene and object configuration, never the seed label.

    A task that cannot expose realized fixture configuration or explicit scene
    metadata is not eligible for attested variation validation. Object names and rounded GT positions
    identify the realized object configuration; they are evaluator-side only.
    """
    scene = {
        key: ep_meta[key]
        for key in ("scene_id", "layout_id", "style_id")
        if key in ep_meta
    }
    if fixture_configs:
        # ManiSkill RoboCasa does not put its layout/style IDs in get_ep_meta.
        # The scene builder keeps Python fixture objects. Hash their measured
        # geometry, never their repr (which contains process-local addresses).
        # This attests layout geometry, not visual textures/style.
        scene = {"fixture_geometry": _fixtures_signature(fixture_configs)}
    if not scene:
        raise ValueError("task episode metadata lacks scene/layout/style identity")
    if not objects:
        raise ValueError("task has no observable object configuration")
    normalized = []
    for item in objects:
        name = item.get("name")
        coords = (item.get("x"), item.get("y"), item.get("z"))
        if not isinstance(name, str) or not name or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            for value in coords
        ):
            raise ValueError("invalid object configuration")
        normalized.append([name, *[round(float(value), 3) for value in coords]])
    normalized.sort()
    return {
        "scene_id": "scene:" + _digest(scene),
        "object_set_id": "objects:" + _digest(normalized),
    }


def selected_cameras(sensor_data: dict[str, Any], requested: list[str] | None) -> list[str]:
    cameras = list(sensor_data) if requested is None else requested
    if not isinstance(cameras, list) or not cameras or len(set(cameras)) != len(cameras):
        raise ValueError("camera selection must be a nonempty unique list")
    if any(name not in sensor_data for name in cameras):
        raise ValueError("requested camera is unavailable")
    if any("segmentation" not in sensor_data[name] for name in cameras):
        raise ValueError("requested camera lacks segmentation")
    return cameras
