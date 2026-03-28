---
name: grasp-and-lift
description: Given a target object's XYZ position, approach from above with a top-down gripper orientation, lower to grasp height, close the gripper to grasp the object, then lift it upward. Use when a task requires picking up an object at a known pose without visual servoing — e.g. after a pose-estimation step.
---

# Grasp and Lift

Pure pose-based grasp primitive. Executes a top-down grasp at a supplied 3-D position using `wb.move_to_pose`.

## Pipeline

1. Open gripper (pre-grasp clearance)
2. Move to **approach pose**: target XY, Z = grasp_z + approach_clearance, top-down orientation (quat=[0,1,0,0])
3. Lower to **grasp pose**: target XYZ at grasp height
4. Close gripper — detect contact via gripper width threshold
5. **Lift**: raise end-effector by lift_height metres while maintaining grip
6. Verify end-effector reached lift height

## Usage

```python
from main import grasp_and_lift
success = grasp_and_lift(target_x=0.4, target_y=0.0, target_z=-0.42)
```

## Parameters

| Parameter               | Default | Description                                   |
|-------------------------|---------|-----------------------------------------------|
| `approach_clearance`    | 0.15 m  | Height above grasp point for approach         |
| `lift_height`           | 0.20 m  | Vertical rise after successful grasp          |
| `grasp_width_threshold` | 0.01 m  | Min gripper width to confirm object contact   |
| `move_timeout`          | 15 s    | Per-move_to_pose timeout                      |

## Success Signal

Prints `Result: SUCCESS` to stdout when the object is securely lifted.
Prints `Result: FAILED – <reason>` on any failure.
