#!/usr/bin/env python3
"""
detect-scene-objects — Skill main script.

Calls sensors.find_objects() to build a scene inventory, flags the target
object ('obj') and key fixtures ('cabinet', 'counter'), then prints a JSON
detection list and a SUCCESS/FAILED result line.

Output format (parsed by tests/run_trials.py):
  Detections: [{"name": "...", "position": [x, y, z]}, ...]
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
    Detect all objects in the scene and return a list of dicts:
        [{"name": str, "position": [x, y, z]}, ...]

    Raises RuntimeError with a descriptive message on any failure.
    """
    from robot_sdk import sensors

    # ------------------------------------------------------------------
    # 1. Broad scan — all objects the sensor pipeline can see
    # ------------------------------------------------------------------
    raw = sensors.find_objects()

    # ------------------------------------------------------------------
    # 2. Targeted pass to boost recall for 'obj' and fixtures.
    #    find_objects() filters by name when target_names is given, so
    #    run a second call and merge, deduplicating on name.
    # ------------------------------------------------------------------
    targeted = sensors.find_objects(target_names=["obj", "cabinet", "counter"])

    # Merge: start from broad scan, add any targeted results not already present
    seen_names = {o["name"] for o in raw}
    merged = list(raw)
    for o in targeted:
        if o["name"] not in seen_names:
            merged.append(o)
            seen_names.add(o["name"])

    # ------------------------------------------------------------------
    # 3. Build structured detection list
    # ------------------------------------------------------------------
    detections = []
    for o in merged:
        name = o.get("name", "")
        x, y, z = o.get("x", float("nan")), o.get("y", float("nan")), o.get("z", float("nan"))

        if not isinstance(name, str) or not name.strip():
            print(f"  [warn] Skipping detection with invalid name: {o!r}")
            continue
        if not (_is_finite(x) and _is_finite(y) and _is_finite(z)):
            print(f"  [warn] Skipping '{name}' — non-finite position ({x}, {y}, {z})")
            continue

        detections.append({
            "name": name,
            "position": [float(x), float(y), float(z)],
        })

    # ------------------------------------------------------------------
    # 4. Validate presence of required entities
    # ------------------------------------------------------------------
    if not detections:
        raise RuntimeError("no_objects_detected: find_objects returned an empty list")

    names_lower = [d["name"].lower() for d in detections]

    has_target = any("obj" in n for n in names_lower)
    has_fixture = any("cabinet" in n or "counter" in n for n in names_lower)

    if not has_target:
        raise RuntimeError(
            f"target_not_found: 'obj' not found in detections {[d['name'] for d in detections]}"
        )
    if not has_fixture:
        raise RuntimeError(
            f"fixtures_not_found: neither 'cabinet' nor 'counter' found in "
            f"detections {[d['name'] for d in detections]}"
        )

    return detections


# ---------------------------------------------------------------------------
# Entry point — executed by POST /code/execute
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        detections = detect_scene_objects()

        # Print summary to help with debugging
        print(f"Detected {len(detections)} object(s):")
        for d in detections:
            pos = d["position"]
            print(f"  {d['name']:30s}  @ ({pos[0]:.3f}, {pos[1]:.3f}, {pos[2]:.3f})")

        # Machine-readable line — parse_detections() in run_trials.py looks for
        # a line that starts with "Detections:" followed by a JSON array.
        print(f"Detections: {json.dumps(detections)}")
        print("Result: SUCCESS")

    except Exception as exc:
        print(f"Result: FAILED – {exc}")
        raise SystemExit(1)
