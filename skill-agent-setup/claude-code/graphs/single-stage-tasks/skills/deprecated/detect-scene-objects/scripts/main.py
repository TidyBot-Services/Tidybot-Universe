#!/usr/bin/env python3
"""
detect-scene-objects — Detect all objects in the scene using sensors.find_objects().

Output format:
  Detections: [{"name": "...", "position": [x, y, z], "distance_m": ..., "fixture_context": "..."}, ...]
  Result: SUCCESS
"""

import json
import math


def _is_finite(v):
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def detect_scene_objects():
    """
    Detect all objects in the scene using sensors.find_objects().

    Returns a list of dicts:
        [{"name": str, "position": [x, y, z], "distance_m": float, "fixture_context": str}, ...]

    Raises RuntimeError on failure.
    """
    from robot_sdk import sensors

    raw = sensors.find_objects()

    if not raw:
        raise RuntimeError("no_objects_detected: find_objects returned an empty list")

    detections = []
    for o in raw:
        name = o.get("name", "")
        x, y, z = o.get("x"), o.get("y"), o.get("z")

        if not name.strip():
            print(f"  [warn] Skipping detection with invalid name: {o!r}")
            continue
        if not (_is_finite(x) and _is_finite(y) and _is_finite(z)):
            print(f"  [warn] Skipping '{name}' — non-finite position ({x}, {y}, {z})")
            continue

        detections.append({
            "name": name,
            "position": [x, y, z],
            "distance_m": float(o.get("distance_m", 0.0)),
            "fixture_context": o.get("fixture_context", ""),
        })

    if not detections:
        raise RuntimeError("no_valid_objects: all detections had invalid data")

    return detections


if __name__ == "__main__":
    try:
        detections = detect_scene_objects()

        print(f"Detected {len(detections)} object(s):")
        for d in detections:
            pos = d["position"]
            ctx = f" [{d['fixture_context']}]" if d["fixture_context"] else ""
            print(f"  {d['name']:30s}  @ ({pos[0]:.3f}, {pos[1]:.3f}, {pos[2]:.3f})  {d['distance_m']:.2f}m{ctx}")

        print(f"Detections: {json.dumps(detections)}")
        print("Result: SUCCESS")

    except Exception as exc:
        print(f"Result: FAILED – {exc}")
        raise SystemExit(1)
