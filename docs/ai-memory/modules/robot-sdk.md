# Module — robot_sdk

The Python module that skill code imports. Lives at `agent_server/robot_sdk/`.

## What it does

Presents a unified API surface that works identically on sim and real hardware. Skill code does:

```python
from robot_sdk import arm, base, gripper, wb, sensors, graspgen, rewind
```

Under the hood, each sub-module talks to backends in `agent_server/backends/`, which talk to bridges (sim) or real hardware via ZMQ/RPC/WebSocket on the same ports.

## API surface

| Module | What it does |
|---|---|
| `arm` | `move_to_pose(x, y, z, [roll, pitch, yaw, quat])`, `move_to_joints([q1-q7])`, `go_home()`, `get_state()` |
| `base` | `plan_path(x, y, theta)`, `move_to_pose(x, y, theta)`, `get_state()`, `move_delta(dx, dy, dtheta)` |
| `gripper` | `open()`, `close(force=255)`, `get_state()` |
| `wb` | Whole-body via cuRobo: `move_to_pose(x, y, z, quat, mask='whole_body'|'arm_only')` |
| `sensors` | `find_objects()`, `get_task_info()`, `get_arm_base_world()`, `get_arm_joints()` |
| `graspgen` | `get_grasp_poses(obj_name, camera_id, k)`, `ee_target_from_grasp(grasp, pre=True)` |
| `yolo` | `segment_camera(camera_id, prompt)`, `segment_camera_3d(camera_id, prompt)` |
| `rewind` | `to_safe()`, `to_waypoint(idx)`, `percentage(p)` — trajectory reverse-play for recovery |
| `display` | `show_text(...)`, `show_image(...)` — robot UI |
| `http` | `post_json(url, json_data)`, `get(url)` — internet access from sandbox (no `requests` allowed) |

## Critical frame conventions

These trip up agents repeatedly. Captured in `SYSTEM_PROMPT_DEV`:

- **`arm.move_to_pose(x, y, z, ...)` is in arm frame** (panda_link0 relative). NOT world.
- **`base.plan_path(x, y, theta)` is in odom/qpos frame** (anchored at robot's initial pose). NOT world. Empirical mapping for Counter-To-Cab sim: `world_xy ≈ (1.40, -0.95) - 0.85 × odom_xy`. Sign + scale depend on init pose.
- **`wb.move_to_pose(x, y, z, ...)` is in WORLD frame**. The good news.
- **`sensors.find_objects()` returns WORLD positions**. The `obj["x"], obj["y"], obj["z"]` are world.
- **`sensors.get_arm_base_world()` returns the arm base in world frame**. Useful for "how far is target from arm reach (max 0.85m)?".

## Critical SDK quirks

### `arm.move_to_pose` partial RPY now persists current axes (fixed 2026-05-07)

Prior to commit `2ec94de`: passing `pitch=-math.pi/2` but not `roll` / `yaw` would silently set the unspecified ones to 0, killing your previously-set orientation. Now: unspecified axes preserve current values.

### `wb.move_to_pose` targets ee_link not panda_hand

GraspGen returns `world_T_panda_hand` (the gripper mount). `wb.move_to_pose` moves `ee_link` (panda_hand + 10cm). If you pass `grasp.position` directly, gripper closes on empty space 10cm short.

Fix: use the helper `ee_target_from_grasp(grasp)` which applies the 10cm offset along the quat-rotated +Z axis. See `~/.claude/projects/.../memory/reference_graspgen_pose_convention.md`.

### Code execution sandbox blocks `requests`

Submitted code can use `urllib.request` and `urllib.error` but **not** `requests` or `http.client` or `httpx`. Service client SDKs follow this rule (see `CLIENT_SDK_SPEC.md` in `services_wishlist`).

### `find_objects()` cache

After base motion, you must call `sensors.find_objects()` again — `sensors.get_arm_base_world()` returns stale data from the cached scan.

## How sim differs from real (cheat sheet)

| Feature | Sim | Hardware |
|---|---|---|
| `sensors.find_objects()` | ✅ ground-truth segmentation | ❌ requires perception server (port 5500) |
| `GET /task/success` | ✅ via `_check_success()` | ❌ no equivalent |
| `POST /reset` | ✅ resets sim | ❌ no-op |
| `arm`, `base`, `gripper` | ✅ | ✅ |
| `yolo.segment_camera()` | ✅ uses onboard cams | ✅ |
| `rewind` | ✅ | ✅ |

## Related

- `decisions/0001-shared-hardware-sdk.md`
- `modules/agent-server.md` — server-side SDK implementation
- `modules/simulation.md` — sim-side bridges
- `~/.claude/projects/.../memory/reference_graspgen_pose_convention.md`
