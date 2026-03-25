---
name: pick-object
description: Detect the target object ('obj') on the counter using detect-scene-objects, then grasp-and-lift it. Composes detection with grasping into a reliable pick sequence.
---

# Pick Object

High-level pick primitive that chains scene perception with pose-based grasping.

## Pipeline

1. Call `detect_scene_objects()` to locate 'obj' and key fixtures in the scene
2. Extract the world-frame (x, y, z) position of 'obj' from the detection list
3. Verify 'obj' is on/near the counter (z within expected counter-height band)
4. Call `grasp_and_lift(target_x, target_y, target_z)` to execute the pick

## Usage

```python
from main import pick_object
success = pick_object()
```

## Success Signal

Prints `Result: SUCCESS` to stdout when the object has been successfully detected
and lifted off the counter.
Prints `Result: FAILED – <reason>` on any failure.
