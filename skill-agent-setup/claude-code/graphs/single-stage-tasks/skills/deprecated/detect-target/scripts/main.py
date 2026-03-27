#!/usr/bin/env python3
"""
detect-target — Detect the target object ('obj') on the counter.

Returns world-frame position, bounding-box size, and fixture context
so downstream skills (e.g. pick-object) can grasp it directly.

Output (machine-readable)
-------------------------
  Target: {"name": "...", "position": [x, y, z],
            "size": [sx, sy, sz], "fixture_context": "..."}
  Result: SUCCESS  |  Result: FAILED – <reason>
"""

import json
import math
import sys

from robot_sdk import sensors

# Objects whose fixture_context contains one of these strings are "on the counter"
COUNTER_CONTEXTS = ("counter",)

# Fallback: if fixture_context is missing, gate on z-range
Z_COUNTER_MIN = -0.60   # m
Z_COUNTER_MAX = -0.25   # m


def _is_finite(v):
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def detect_target() -> dict | None:
    """
    Scan the scene and return detection info for the target object ('obj')
    on the counter.

    Returns a dict with keys:
        name           (str)   — object name
        position       (list)  — [x, y, z] world-frame metres
        size           (list)  — [size_x, size_y, size_z] bounding-box metres
        fixture_context (str)  — where the object is ("counter", …)

    Returns None and prints Result: FAILED on any error.
    """
    # ── Step 1: broad scan ────────────────────────────────────────────────
    print("Step 1: scanning scene with sensors.find_objects()", flush=True)
    raw = sensors.find_objects()

    # ── Step 2: targeted pass to boost recall for 'obj' ──────────────────
    print("Step 2: targeted scan for 'obj'", flush=True)
    targeted = sensors.find_objects(target_names=["obj"])

    # Merge, deduplicating on name
    seen = {o["name"] for o in raw}
    merged = list(raw)
    for o in targeted:
        if o["name"] not in seen:
            merged.append(o)
            seen.add(o["name"])

    print(f"  Found {len(merged)} object(s): {[o['name'] for o in merged]}", flush=True)

    # ── Step 3: validate and filter ──────────────────────────────────────
    candidates = []
    for o in merged:
        name = o.get("name", "")
        x = o.get("x", float("nan"))
        y = o.get("y", float("nan"))
        z = o.get("z", float("nan"))

        if not (isinstance(name, str) and name.strip()):
            continue
        if not (_is_finite(x) and _is_finite(y) and _is_finite(z)):
            print(f"  [warn] skipping '{name}' — non-finite position", flush=True)
            continue

        # Only keep entries that look like 'obj'
        if "obj" not in name.lower():
            continue

        # Check fixture context or fall back to z-band
        fixture = o.get("fixture_context", "")
        on_counter = any(ctx in fixture.lower() for ctx in COUNTER_CONTEXTS)
        if not on_counter:
            on_counter = Z_COUNTER_MIN <= z <= Z_COUNTER_MAX

        if not on_counter:
            print(
                f"  [warn] '{name}' fixture_context='{fixture}' z={z:.3f} — "
                "not identified as on counter, including anyway",
                flush=True,
            )

        candidates.append({
            "name": name,
            "position": [float(x), float(y), float(z)],
            "size": [
                float(o.get("size_x", 0.0)),
                float(o.get("size_y", 0.0)),
                float(o.get("size_z", 0.0)),
            ],
            "fixture_context": fixture,
            "distance_m": float(o.get("distance_m", float("nan"))),
            "on_counter": on_counter,
        })

    if not candidates:
        names = [o["name"] for o in merged]
        print(
            f"Result: FAILED – obj_not_found: no 'obj' in detections {names}",
            flush=True,
        )
        return None

    # Pick the closest candidate on the counter (prefer on_counter=True)
    on_counter = [c for c in candidates if c["on_counter"]]
    pool = on_counter if on_counter else candidates
    pool.sort(key=lambda c: c["distance_m"] if _is_finite(c["distance_m"]) else 9999)
    target = pool[0]

    return target


def main() -> bool:
    try:
        target = detect_target()
        if target is None:
            return False

        # Machine-readable output for downstream skills
        output = {
            "name":            target["name"],
            "position":        target["position"],
            "size":            target["size"],
            "fixture_context": target["fixture_context"],
        }
        print(f"Target: {json.dumps(output)}", flush=True)

        x, y, z = target["position"]
        sx, sy, sz = target["size"]
        print(
            f"  position: ({x:.3f}, {y:.3f}, {z:.3f}) m  "
            f"size: ({sx:.3f}×{sy:.3f}×{sz:.3f}) m  "
            f"fixture: '{target['fixture_context']}'",
            flush=True,
        )
        print("Result: SUCCESS", flush=True)
        return True

    except Exception as e:
        print(f"Result: FAILED – crash: {e}", flush=True)
        return False


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
