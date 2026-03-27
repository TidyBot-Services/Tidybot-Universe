---
name: place-in-cabinet
description: With an object already grasped, detect the cabinet interior using sensors.find_objects(), move the held object inside the cabinet with arm.move_to_pose, lower it onto the shelf, open the gripper to release it, then retract the arm clear of the cabinet. Use after a grasp-and-lift step when the task is to store an object in a cabinet.
---

# Place in Cabinet

Placement primitive. Assumes the robot arm is already holding an object (gripper closed, contact confirmed). Finds the target cabinet in the scene, plans a two-phase approach (above → lower-in), releases the object, and retracts.

## Pipeline

1. **Detect cabinet** — call `sensors.find_objects()` and locate the cabinet; extract its interior XYZ placement position.
2. **Approach** — move end-effector to a pose directly above the cabinet interior at `place_z + approach_clearance` (straight-down orientation).
3. **Lower** — descend to the final placement height `place_z` inside the cabinet.
4. **Release** — open the gripper; confirm via gripper width ≥ `open_width_threshold`.
5. **Retract** — raise the arm back to the approach clearance height and move clear of the cabinet.

## Usage

```python
from main import place_in_cabinet
success = place_in_cabinet()
```

## Parameters

| Parameter              | Default  | Description                                        |
|------------------------|----------|----------------------------------------------------|
| `approach_clearance`   | 0.15 m   | Height above placement point for approach phase    |
| `open_width_threshold` | 0.030 m  | Min gripper width after open to confirm release    |
| `move_timeout`         | 10 s     | Per-move_to_pose timeout                           |

## Success Signal

Prints `Result: SUCCESS` to stdout when the object is placed and the arm is retracted.
Prints `Result: FAILED – <reason>` on any failure.
