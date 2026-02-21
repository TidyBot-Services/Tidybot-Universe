---
name: robot-hardware
description: Robot physical specs, coordinate frames, camera setup, and morphology — Franka Panda arm, mobile base, Robotiq gripper, camera positions and IDs. Use when (1) writing arm or base control code, (2) referencing world-frame coordinates or axis conventions, (3) computing end-effector targets or grasp positions, (4) using camera IDs or understanding camera placement, (5) reasoning about spatial layout, reach, or workspace boundaries.
---

# Robot Hardware

## Morphology

- **Arm:** Franka Emika Panda, 7-DOF
- **Mobile Base:** Platform height 0.47m. Arm base at Z=0.47m.
- **Gripper:** Robotiq, on Franka end effector
- **Arm orientation:** Faces +X (front of mobile base)

Ground target: Z ~ -0.47m (EE pose reported in arm base frame).

## World Frame

Origin: mobile base pose at server start (0, 0, 0).

| Axis | Positive | Negative |
|------|----------|----------|
| X | Front | Back |
| Y | Left | Right |
| Z | Up | Down |

## Cameras

1. **Base camera** — front edge of base, facing +X
2. **Wrist camera** — inner wrist of Franka arm
   - At rest: faces -Z with slight tilt toward gripper
   - Offset from gripper: -0.09m in X
   - Camera ID: *(fill in from robot, e.g. `309622300814`)*

## End Effector

Straight-down grasp orientation: `(roll=pi, pitch=0, yaw=0)`

## Safety

All movements are recorded and reversible via rewind.
