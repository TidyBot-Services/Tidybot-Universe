---
name: detect-scene-objects
description: Detect all objects in the scene using sensors.find_objects(). Returns names and world-frame (x, y, z) positions for every graspable object, sorted nearest-first.
---

# Detect Scene Objects

Perception primitive that calls `sensors.find_objects()` and returns a structured inventory of every recognised object in the workspace with world-frame coordinates.

## Pipeline

1. Call `sensors.find_objects()` — ground-truth segmentation, no neural network needed
2. Filter out any detections with invalid name or non-finite position
3. Return structured list sorted by distance from the arm base (nearest first)

## Usage

```python
from main import detect_scene_objects
detections = detect_scene_objects()
# detections = [{"name": "mug_0", "position": [x, y, z], "distance_m": 0.5, "fixture_context": "counter"}, ...]
```

## Output Fields

| Field            | Type   | Description                                      |
|------------------|--------|--------------------------------------------------|
| `name`           | str    | Object name (e.g. `"banana_0"`)                  |
| `position`       | list   | `[x, y, z]` world-frame meters                  |
| `distance_m`     | float  | Distance from arm base in meters                 |
| `fixture_context`| str    | Location context (`"counter"`, `"drawer_interior"`, etc.) |

## Success Signal

Prints `Result: SUCCESS` and a `Detections: [...]` JSON line on success.
Prints `Result: FAILED – <reason>` on any failure.
