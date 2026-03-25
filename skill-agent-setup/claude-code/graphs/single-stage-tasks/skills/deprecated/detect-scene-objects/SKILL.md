---
name: detect-scene-objects
description: Use sensors.find_objects() to detect all objects in the scene and return their names and world-frame (x, y, z) positions. Identify the target object (obj) and key fixtures (cabinet, counter).
---

# Detect Scene Objects

Perception primitive that sweeps the sensor pipeline and returns a structured inventory of every recognised object in the workspace, tagged with world-frame coordinates.

## Pipeline

1. Call `sensors.find_objects()` to get raw detections
2. Extract `name` and world-frame `position` (x, y, z) for every detection
3. Flag the **target object** (`obj`) and known fixtures (`cabinet`, `counter`)
4. Return / print the inventory; raise a clear error if the scene is empty

## Usage

```python
from main import detect_scene_objects
detections = detect_scene_objects()
# detections = [{"name": "mug", "position": [x, y, z]}, ...]
```

## Success Signal

Prints `Result: SUCCESS` followed by a JSON list of detections when at least the
target object and one fixture are found.
Prints `Result: FAILED – <reason>` on any failure.
