---
name: pick-from-counter
description: Detect the target object on the counter and grasp it. Finds objects with fixture_context=="counter", approaches from above, grasps, and lifts. Returns SUCCESS when object is securely lifted.
---

# Pick From Counter

Detects and picks an object from the counter surface using top-down grasp.

## Pipeline

1. Call `sensors.find_objects()` and filter for counter objects (`fixture_context == "counter"`)
2. Select the nearest counter object (or a named target)
3. Open gripper for pre-grasp clearance
4. Move to approach pose: target XY, Z = grasp_z + 0.15 m, top-down orientation
5. Lower to grasp pose: target XYZ
6. Close gripper — verify contact via gripper width threshold
7. Lift: raise end-effector by 0.20 m

## Usage

```python
from main import pick_from_counter

# Pick nearest counter object
success = pick_from_counter()

# Pick specific object
success = pick_from_counter(target_name="mug_0")
```

## Parameters

| Parameter    | Default | Description                              |
|--------------|---------|------------------------------------------|
| `target_name`| None    | Object name to pick; None = nearest      |

## Success Signal

Prints `Result: SUCCESS – picked <name>` on success.
Prints `Result: FAILED – <reason>` on any failure.
