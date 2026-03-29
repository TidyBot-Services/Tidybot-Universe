---
name: pick-object
description: Detect the target object on the counter (or nearest surface), navigate to it with whole-body motion, and grasp it. Tries TopDown then Angled45 grasp strategies with yaw offsets for robustness. Returns SUCCESS when object is securely lifted.
---

# Pick Object

High-level pick primitive: detect → navigate → grasp → lift.

## Pipeline

1. Call `sensors.find_objects()` — filter graspable objects, prefer counter context
2. Select target by name or pick nearest graspable object
3. For each grasp strategy (TopDown × 3 z-offsets, Angled45 × 3 yaw offsets):
   a. Open gripper
   b. `wb.move_to_pose` to pre-grasp approach (whole_body)
   c. `wb.move_to_pose` to grasp pose
   d. Close gripper — verify contact via gripper width threshold
   e. On success: lift object 0.20 m and return
4. If all attempts fail, return FAILED

## Usage

```python
from main import pick_object

# Pick nearest counter object
success = pick_object()

# Pick specific named object
success = pick_object(target_name="mug_0")
```

## Parameters

| Parameter     | Default | Description                                      |
|---------------|---------|--------------------------------------------------|
| `target_name` | None    | Object name to pick; None = nearest graspable    |

## Success Signal

Prints `Result: SUCCESS – picked <name>` on success.
Prints `Result: FAILED – <reason>` on any failure.
