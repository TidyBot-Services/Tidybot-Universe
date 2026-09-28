"""Optional diagnostic projection from public robot SDK observations.

This adds no evaluator verdict. It only compares object poses returned by two
public ``find_objects`` calls around a completed grasp and lift sequence.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


def _cube_height(event: Mapping[str, Any]) -> float | None:
    if (event.get("operation") != "find_objects"
            or event.get("status") != "completed"
            or not isinstance(event.get("result"), list)):
        return None
    for item in event["result"]:
        if not isinstance(item, Mapping) or item.get("name") != "cube":
            continue
        position = item.get("position")
        if (not isinstance(position, list) or len(position) != 3
                or isinstance(position[2], bool)
                or not isinstance(position[2], (int, float))
                or not math.isfinite(position[2])):
            return None
        return float(position[2])
    return None


def public_lift_progress_event(
    events: Sequence[Mapping[str, Any]], *, attention_input: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Report an observed lack of lift after help, using only public SDK data."""
    if not isinstance(attention_input.get("advisor_guidance"), str):
        return None
    close = next((i for i, event in enumerate(events)
                  if event.get("operation") == "close"
                  and event.get("status") == "completed"), None)
    if close is None:
        return None
    before = next(((i, height) for i, event in enumerate(events[:close])
                   if (height := _cube_height(event)) is not None), None)
    moves = [i for i, event in enumerate(events[close + 1:], close + 1)
             if event.get("operation") == "move_to_position"
             and event.get("status") == "completed"]
    if before is None or not moves:
        return None
    after = next(((i, height) for i, event in enumerate(events[moves[-1] + 1:], moves[-1] + 1)
                  if (height := _cube_height(event)) is not None), None)
    if after is None:
        return None
    delta = after[1] - before[1]
    if delta >= 0.05:
        return None
    return {
        "timestamp": float(events[after[0]].get("timestamp", 0.0)),
        "source": "attention_harness.public_sdk_projection",
        "event_type": "attention.public_progress_check",
        "operation": "compare_cube_height_after_lift",
        "status": "failed",
        "arguments": {"before_sdk_event": events[before[0]].get("event_id"),
                      "after_sdk_event": events[after[0]].get("event_id"),
                      "threshold_m": 0.05},
        "result": {"before_z_m": before[1], "after_z_m": after[1], "rise_m": delta},
        "error": {"type": "GraspProgressFailure",
                  "message": "public cube height rose less than 0.05 m after lift command"},
    }
