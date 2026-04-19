#!/usr/bin/env python3
"""
Agent Orchestrator: bridges Claude Code agents <-> WebSocket dashboard.

Spawns Claude Code agents using the Agent SDK, streams their output to the
browser hex gallery, and relays inject/stop/spawn commands back.

Usage:
    pip install websockets claude-agent-sdk
    python agent_orchestrator.py

    # Then serve the site:
    python -m http.server 8080

    # Open http://localhost:8080/local/
"""

import asyncio
import json
import os
import re
import sys
import traceback
import time
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

try:
    import websockets
except ImportError:
    raise ImportError("pip install websockets")

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    SystemMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)
HAS_SDK = True

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WS_PORT = 8765
_script_dir = Path(__file__).resolve()
# WORKSPACE_DIR = directory containing agent_orchestrator.py = claude-code/
# This is the cwd we want SDK agents to run from, so relative paths like
# `graphs/<name>/skills/<skill>/scripts/main.py` resolve correctly.
WORKSPACE_DIR = _script_dir.parent
# PROJECT_DIR = workspace root (contains agent_server/, sims/, etc.)
# Used for execution recordings, system prompts, etc.
PROJECT_DIR = _script_dir.parents[3]  # default: claude-code -> skill-agent-setup -> Tidybot-Universe -> workspace
for i in range(2, 5):
    candidate = _script_dir.parents[i]
    if (candidate / "agent_server").is_dir():
        PROJECT_DIR = candidate
        break

AGENT_SERVER = "http://localhost:8080"  # default, overridden by graph targets

# Multi-target support (populated in _load_entries from graph.json "targets" field)
targets: list[dict] = []
primary_target: dict = {"name": "default", "agent_server": AGENT_SERVER, "sim_api": "http://localhost:5500", "primary": True}

# Mode flags (set by CLI args or /xbot-start endpoint)
dev_mode: bool = False  # True after /xbot-start; blocks agent spawning during planning
autonomous_mode: bool = False  # True = skip review gate, auto-promote done skills

# Parse required --graph argument
import argparse
_parser = argparse.ArgumentParser(description="Claude Agent Orchestrator")
_parser.add_argument("--graph", required=True, type=Path,
                     help="Path to a graph folder (containing graph.json) or a JSON file")
_parser.add_argument("--autonomous", action="store_true",
                     help="Autonomous mode: skip review gate, auto-promote skills to done")
_args = _parser.parse_args()
_graph_path = _args.graph
if _graph_path.is_dir():
    GRAPH_DIR = _graph_path
    _graph_file = _graph_path / "graph.json"
    if not _graph_file.exists():
        _parser.error(f"graph.json not found in: {_graph_path}")
else:
    GRAPH_DIR = _graph_path.parent
    _graph_file = _graph_path
    if not _graph_file.exists():
        _parser.error(f"graph file does not exist: {_graph_file}")
LOCAL_REPOS = _graph_file
SESSION_LOG = GRAPH_DIR / "agent_sessions.jsonl"

if _args.autonomous:
    autonomous_mode = True
    dev_mode = True  # autonomous implies dev mode (no planning gate either)

# ---------------------------------------------------------------------------
# Agent type system prompts
# ---------------------------------------------------------------------------

SKILLS_DIR = GRAPH_DIR / "skills"



def _has_test(skill: str) -> bool:
    """Check if a skill has a test file."""
    test_file = SKILLS_DIR / skill / "tests" / "run_trials.py"
    return test_file.exists()


def _is_task_root(skill: str) -> bool:
    """Check if a skill is the root of a task graph (sim provides its test).

    A skill is the task root if:
    - The graph has task_env metadata, AND
    - No other skill in the tree depends on this skill
    """
    if not graph_meta.get("task_env"):
        return False
    for entry in skill_entries:
        if skill in entry.get("dependencies", []):
            return False
    return True


def _auto_generate_task_root_test(skill: str):
    """Auto-generate a test for the task root skill using sim's /task/success endpoint."""
    test_dir = SKILLS_DIR / skill / "tests"
    test_file = test_dir / "run_trials.py"
    if test_file.exists():
        return  # already has a test

    task_env = graph_meta.get("task_env", "unknown")
    test_dir.mkdir(parents=True, exist_ok=True)

    # Also ensure scripts/ dir exists
    (SKILLS_DIR / skill / "scripts").mkdir(parents=True, exist_ok=True)

    agent_server_url = primary_target["agent_server"]
    sim_api_url = primary_target.get("sim_api", "http://localhost:5500")

    test_code = f'''\
#!/usr/bin/env python3
"""Auto-generated test for task root skill: {skill}
Task: {task_env}

Runs the skill's main.py via the agent server, then checks success
via the sim's /task/success endpoint.
"""
import json
import time
import urllib.request
import urllib.error

AGENT_SERVER = "{agent_server_url}"
SIM_API = "{sim_api_url}"
NUM_TRIALS = 1


def submit_code(code: str) -> str:
    """Submit code to agent server, return job_id."""
    data = json.dumps({{"code": code}}).encode()
    req = urllib.request.Request(
        f"{{AGENT_SERVER}}/code/submit",
        data=data,
        headers={{"Content-Type": "application/json"}},
    )
    resp = json.loads(urllib.request.urlopen(req).read())
    return resp["job_id"]


def wait_for_job(job_id: str, timeout: int = 300) -> dict:
    """Poll until job completes."""
    url = f"{{AGENT_SERVER}}/code/jobs/{{job_id}}"
    start = time.time()
    while time.time() - start < timeout:
        try:
            job = json.loads(urllib.request.urlopen(url).read())
            if job["status"] in ("completed", "failed"):
                return job
        except urllib.error.URLError:
            pass
        time.sleep(2)
    return {{"status": "timeout"}}


def check_success() -> bool:
    """Check task success via sim endpoint."""
    try:
        resp = json.loads(urllib.request.urlopen(f"{{SIM_API}}/task/success").read())
        return resp.get("success", False)
    except Exception as e:
        print(f"Could not check sim success: {{e}}")
        return False


def run_trial(trial_num: int) -> bool:
    """Run one trial of the skill and check success."""
    import os
    import subprocess
    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    skills_dir = os.path.dirname(skill_dir)
    skill_name = os.path.basename(skill_dir)
    bundler = os.path.join(os.path.dirname(skills_dir), "..", "tidybot-bundle", "scripts", "tidybot-bundle.py")

    if not os.path.exists(bundler):
        print(f"Trial {{trial_num}}: FAIL - bundler not found at {{bundler}}")
        return False

    print(f"Trial {{trial_num}}: bundling skill with dependencies...")
    result = subprocess.run(
        ["python3", bundler, skill_name, "--skills-dir", skills_dir],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"Trial {{trial_num}}: FAIL - bundler error: {{result.stderr[:200]}}")
        return False
    code = result.stdout

    print(f"Trial {{trial_num}}: submitting skill code...")
    job_id = submit_code(code)
    job = wait_for_job(job_id)

    result = job.get("result", {{}})
    status = job.get("status", "unknown")
    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "")

    if status == "failed":
        error = result.get("error", stderr[:200])
        print(f"Trial {{trial_num}}: FAIL - execution error: {{error}}")
        return False

    # Check task success via sim
    success = check_success()
    label = "PASS" if success else "FAIL"
    print(f"Trial {{trial_num}}: {{label}} (sim _check_success={{success}})")
    return success


def main():
    passed = 0
    failed = 0
    failures = []

    for i in range(1, NUM_TRIALS + 1):
        try:
            if run_trial(i):
                passed += 1
            else:
                failed += 1
                failures.append(f"trial_{{i}}")
        except Exception as e:
            failed += 1
            failures.append(f"trial_{{i}}: {{e}}")
            print(f"Trial {{i}}: ERROR - {{e}}")

    total = passed + failed
    rate = (passed / total * 100) if total > 0 else 0

    result = {{
        "skill": "{skill}",
        "task_env": "{task_env}",
        "success_rate": rate,
        "total_trials": total,
        "passed": passed,
        "failed": failed,
        "failure_modes": failures,
    }}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
'''

    test_file.write_text(test_code)
    print(f"[ORCH] Auto-generated test for task root: {test_file}")



SYSTEM_PROMPT_DEV = """\
You are a robotics skill developer for the TidyBot Universe project.

## Your Robot
- Franka Panda 7-DOF arm + mobile base + Robotiq gripper + cameras
- API at {agent_server} — read the guide at {agent_server}/docs/guide/html before writing code
- SDK reference at {agent_server}/code/sdk/markdown
- Skills run via POST /code/submit (fire-and-forget) with access to robot_sdk

## Existing Skills
Skills live in {skills_dir}/. First check what's already there:
{existing_skills}

Read SKILL.md of relevant existing skills to understand patterns and avoid reinventing.
IMPORTANT: Ignore the `deprecated/` folder — it contains old skills from previous sessions that are no longer active.

## Skill Structure
Each skill is a directory under {skills_dir}/<skill-name>/ with:
```
<skill-name>/
├── SKILL.md              # Skill description, pipeline, usage, config
├── scripts/
│   ├── main.py           # Main skill code (uses robot_sdk)
│   └── deps.txt          # Skill dependencies (other skills needed)
```

## Your Job
You are working on the skill: **{{skill_name}}**
If {skills_dir}/{{skill_name}}/ does not exist, create it with the structure above.
If it exists, read it and modify as requested.

## Development & Testing Workflow
You must write code AND test it. Do not stop until you have a working skill.

1. **Read SDK docs first**: `curl {agent_server}/code/sdk/markdown` — understand available APIs
2. **Write scripts/main.py**: implement the skill using robot_sdk
3. **Test by submitting**: `python {submit_script} scripts/main.py --holder dev:{{skill_name}} --agent-server {agent_server}`
4. **Debug and iterate**: if it fails, read the error, fix the code, resubmit

### How to submit and test code

**Exploration / debugging** (quick tests, checking objects, probing the scene):
```bash
python {submit_script} /tmp/test.py --no-eval --holder dev:{{skill_name}} --agent-server {agent_server}
```
Returns raw stdout/stderr. No evaluator. Use this for quick probes.

**Full skill test** (runs evaluator on execution recording):
```bash
python {submit_script} scripts/main.py --holder dev:{{skill_name}} --agent-server {agent_server}
```
Submits code, resets sim, waits for completion, then an evaluator agent reviews
the camera recordings and robot behavior. Returns JSON with `passed` (bool)
and `feedback` (detailed evaluation).

### Testing loop
- First, explore the scene with `--no-eval` to understand object positions, etc.
- Then write/edit scripts/main.py and run the full test (without --no-eval)
- The output includes `passed` (true/false) and `feedback` from an automated evaluator
- If `passed` is false: read the `feedback`, fix the code, resubmit
- If `passed` is true: continue to the next iteration or finish
- Run at least 1 passed evaluation before declaring done

### Important
- The code sandbox allows robot_sdk imports but NOT `requests` or network libraries
- Your submitted code runs inside the agent server with full robot_sdk access
- If the arm enters error state, use rewind: the SDK has `from robot_sdk import rewind`

### Stdout guidelines
Your stdout is read by an evaluator agent (not you) to assess the execution.
Print useful context at each major step so the evaluator can follow what happened:
- Object detections: name, position, fixture_context
- Grasp attempts: strategy, target position, result (success/fail), gripper width after
- Navigation: target pose, whether it converged
- Final outcome: clear SUCCESS or FAILURE with a one-line reason

Do NOT print raw data dumps, full joint arrays, or per-timestep logs — the evaluator
also has camera images and state logs. Keep stdout to ~20-40 lines of high-level trace.

## Perception & Grasping Guide

When writing skills that detect and manipulate objects, follow these patterns:

### RoboCasa object naming
In RoboCasa tasks, `sensors.find_objects()` returns objects with **semantic category
names** (e.g. `"mug"`, `"banana"`, `"canned_food"`, `"pan"`). If multiple objects
share the same category, they get suffixed: `"mug_0"`, `"mug_1"`.
Distractors also have real category names — use the task prompt (from
`GET http://localhost:5500/task/info`) to understand which object is the target.

### Task prompt
Your skill code should fetch the task description at runtime to know what to do:
```python
from robot_sdk import sensors
info = sensors.get_task_info()
print(info)  # {{"task": "RoboCasa-Pn-P-Counter-To-Cab-v0", "lang": "pick the mug from the counter and place it in the cabinet"}}
```
The `"lang"` field describes the task in natural language with the actual object names.
Use this to determine which object to target rather than hardcoding object names.

### Perception pipeline
1. **Use `sensors.find_objects()`** — this calls the sim's depth + segmentation perception
   (NOT a neural network). It returns world-frame 3D positions, sizes, and fixture context.
2. **Multi-camera merge** — `find_objects()` runs on all available cameras (base + wrist)
   and merges results, deduplicating by name (keeping the detection with more pixels).
3. **Fixture context** — each detection includes `fixture_context` (e.g. "counter",
   "cabinet_interior", "drawer_interior", "stove") which tells you WHERE the object is.

### Grasp strategy selection
Choose grasp approach based on object context and shape:
- **Always prefer Angled45 first**, then TopDown as fallback
- **Try multiple yaw angles**: for each strategy, try the direct yaw (arm→object),
  then +30° and -30° offsets. This gives 6 candidates (2 strategies × 3 yaws).

### Setting grasp poses
`wb.move_to_pose(x, y, z, quat=[w, x, y, z])` takes a world-frame position and
orientation quaternion (wxyz convention). The quaternion controls the gripper
orientation — which direction the fingers point and the approach direction.

**Key orientations** (all quaternions in [w, x, y, z] order):
- **TopDown** (gripper points straight down): `quat=[0, 1, 0, 0]`
  This is a 180° rotation about X, so the EE Z-axis points down.
  Good for objects on flat open surfaces.
- **Angled45** (gripper tilted 45° toward object): use `euler2quat(0, 3π/4, yaw)`
  from `transforms3d.euler`. The pitch (3π/4 ≈ 135°) tilts the gripper partway
  between horizontal and vertical. Add a small XY offset (~2cm) back along the
  approach direction so the fingertips meet the object center.
- **Front-facing** (gripper horizontal, approaching from the side): use
  `Rotation.from_euler('yz', [π/2, yaw])` from `scipy.spatial.transform`.
  This points the gripper forward along the approach direction.
  Convert to wxyz: `q_scipy[[3,0,1,2]]`.

**Computing yaw** (approach direction):
```python
import numpy as np
# yaw = angle from arm base to object in the XY plane
yaw = np.arctan2(obj_y - arm_base_y, obj_x - arm_base_x)
# Try yaw offsets: [0, +30°, -30°] for robustness
for yaw_offset in [0, np.radians(30), np.radians(-30)]:
    grasp_yaw = yaw + yaw_offset
```

**Pre-grasp offset** (approach from above or behind):
- TopDown: pre-grasp is directly above the object (same XY, +0.15m Z)
- Angled45: offset ~2cm in XY away from approach direction, +2cm Z
  `pos = obj_pos + [-0.02*cos(yaw), -0.02*sin(yaw), 0.02]`
- Front: offset ~6cm back along approach, +8cm Z
  `pos = obj_pos + [-0.06*cos(yaw), -0.06*sin(yaw), 0.08]`

### Grasp execution pattern
1. Open gripper
2. Move to pre-grasp (approach from above, ~0.15m clearance)
3. Lower to grasp height
4. Close gripper
5. Check gripper width to confirm object in hand (not fully closed = object present)
6. If miss: open, retreat, try next strategy/yaw
7. On success: lift object ~0.15–0.20m

### Handle grasps (for doors, drawers, cabinets)
Handles require a front-facing approach rather than top-down:
- Compute approach yaw from arm base to handle position
- Offset ~6cm back along approach direction, ~8cm up
- Use horizontal gripper orientation (Ry 90° + Rz yaw)
- For horizontal bar handles, rotate fingers 90° around approach axis

## External Vision & Grasp Services (sim + hardware)

Three remote services are wrapped by `robot_sdk.yolo` and `robot_sdk.graspgen`.
Unlike `sensors.find_objects()` (sim-only, uses sim perception), **these work
in both sim and hardware** — prefer them when building skills meant to transfer
to HW. URLs come from env vars set in agent_server's env: `YOLO_SERVER_URL`,
`GROUNDEDSAM_SERVER_URL`, `GRASPGEN_SERVER_URL`. If a function raises about a
missing URL, tell the user to set those env vars and restart agent_server.

### Health check before first real call
```python
from robot_sdk import yolo, graspgen
assert yolo.health_check(),     "YOLO unreachable — check YOLO_SERVER_URL"
assert graspgen.health_check(), "GraspGen unreachable — check GRASPGEN_SERVER_URL"
```

### YOLO-E — open-vocab detection + segmentation
```python
r = yolo.segment_camera("yogurt cup, milk carton")     # 2D bbox + mask
r = yolo.segment_camera_3d("yogurt cup")               # 3D world-frame position
for d in r.detections:
    print(d.class_name, d.confidence, d.position_3d)
```

### GraspGen — end-to-end 6-DOF grasp (Robotiq 2F-140 tuned)
```python
from robot_sdk import graspgen, wb, gripper
g = graspgen.get_grasp_poses("yogurt cup")   # segment → point cloud → grasp
best = g.poses[0]                             # sorted by score, descending
wb.move_to_pose(best.position[0], best.position[1], best.position[2],
                quat=best.quaternion)
gripper.close(force=255)
```

**Prefer `graspgen` over hardcoded quaternions.** It's trained for the actual
Robotiq 2F-140 gripper and handles approach direction, yaw optimization, and
collision-aware pose ranking. Hand-tuned quats tend to miss objects; GraspGen
gives ranked candidates you can iterate through if the first fails.

### First-call latency
- `yolo.segment_camera`: ~1 s
- `graspgen.get_grasp_poses`: 1–3 s per call
- Grounded-SAM (used internally by graspgen when YOLO lacks the class): **first
  call 30 s to load ~5 GB models**, <1 s thereafter. If you see a 30 s hang on
  the first grasp call, it's just the initial model load — wait for it.

### Which to choose

| Situation | Use |
|---|---|
| Sim, want exact sim-truth positions, no HW transfer | `sensors.find_objects()` |
| Sim, code must also work on HW | `yolo.segment_camera_3d()` + `graspgen.get_grasp_poses()` |
| HW | `yolo.*` / `graspgen.*` — `find_objects()` does not exist on HW |
| Don't know gripper orientation | Always `graspgen` — never hardcode a quat |

## CRITICAL: Known Bugs & Lessons (from previous agent sessions)

### 0. World frame vs arm frame coordinate conversion (CRITICAL)
- The robot heading is ~π/2 — the robot faces the **world Y+** direction.
- `arm.move_to_pose(x, y, z)` takes **arm frame** coordinates, NOT world coordinates.
- In arm frame: x = forward (toward cabinet/wall), y = left, z = up.
- **NEVER** do `arm_pos = world_pos - arm_base_pos` — this ignores heading rotation.
- The correct conversion (heading ≈ π/2):
  ```
  arm_x = world_target_y - arm_base_y   # world Y diff → arm forward
  arm_y = -(world_target_x - arm_base_x) # neg world X diff → arm left/right
  arm_z = world_target_z - arm_base_z   # Z unchanged
  ```
- Use this helper:
  ```python
  def world_to_arm(world_pos, arm_base):
      return [world_pos[1] - arm_base[1],
              -(world_pos[0] - arm_base[0]),
              world_pos[2] - arm_base[2]]
  ```
- Example: cabinet at world (3.48, -0.14, 1.45), arm base at (3.49, -0.85, 0.47)
  → CORRECT arm frame: (0.71, 0.01, 0.98) — reaches 0.71m forward
  → WRONG (without rotation): (-0.01, 0.71, 0.98) — swings 0.71m LEFT
- Getting this wrong makes the arm swing sideways instead of reaching forward.
  This is the #1 cause of placement failures.

### 1. Gripper position interpretation
- `gripper.get_state()["position"]` = 0 means **FULLY CLOSED ON NOTHING** (empty grasp)
- `position` = 20–150 means **object is in hand** (closed on something)
- `position` = 250–255 means **fully open**
- ALWAYS check `object_detected` field as well
- Do NOT treat pos=0 as success — it means you missed the object

### 2. ee_pose is a 4x4 matrix (16 elements, column-major)
- `ee_pose[12]` = x, `ee_pose[13]` = y, `ee_pose[14]` = z (in ARM frame)
- `ee_pose[0:12]` = rotation matrix elements — NOT position!
- **World z = ee_pose[14] + 0.472** (arm base height offset)
- Do NOT use ee_pose[0], ee_pose[1], ee_pose[2] as position — those are rotation

### 3. Stale coordinates after base movement
- After ANY `base.forward()`, `base.backward()`, or wb movement, sensor coordinates are STALE
- You MUST call `sensors.find_objects()` again AFTER base movement to get updated positions
- `wb.move_to_pose()` also has stale base coordinates after base movement — use `arm.move_to_pose()` in arm frame as a workaround if needed

### 4. Verify with camera, not just numbers
- After each major movement, visually verify by checking what happened
- The robot may report plausible-looking numbers that are actually wrong
- A "successful grasp" with pos=0 is actually a miss

### 5. Placement at high Z (upper cabinets)
- Top-down IK fails above world z ≈ 1.20m (arm frame z ≈ 0.73m)
- For z > 1.25m world, use pitch=120° orientation (not vertical)
- The arm can reach world z ≈ 1.53m with pitch=120° if arm_x is small (0.15–0.20m)
- Larger arm_x reduces max reachable Z

### 6. RoboCasa kitchen layout: cabinets are recessed into the wall
- Upper cabinets are **recessed openings (cubbies) in the wall** above the counter
- They are NOT flat shelves — the cabinet is a box-shaped cavity with depth
- To place an object inside: the arm must reach the correct Z height AND extend
  forward in Y (toward the wall) to get INSIDE the cabinet opening
- Just reaching the right Z height is NOT enough — the object will fall on the
  counter if the arm doesn't penetrate into the cabinet cavity
- The cabinet opening is ~0.2–0.3m deep (Y direction, toward the wall)
- `distr_cab_0` coordinates show the CENTER of the cabinet interior, not the front edge
- After picking, you likely need to move the base forward to get closer to the cabinet

### 7. Success checking: translate RoboCasa _check_success(), NEVER guess
- **IRON RULE**: Your success criteria MUST be translated from the RoboCasa task's
  `_check_success()` method in the task source code. NEVER guess from the task description.
- The task source is in `~/tidybot_uni/sims/robocasa_tasks/` (single_stage/, multi_stage/).
  Read the actual `_check_success()` code to understand what "success" means.
- Example: `PnPCounterToCab._check_success()` checks:
  1. `OU.obj_inside_of(self, "obj", self.cab)` — object physically inside cabinet 3D volume
  2. `OU.gripper_obj_far(self)` — gripper is away from object (released)
  This is a 3D bounding box check, NOT a height check.
- Your code MUST call `GET http://localhost:5500/task/success` to verify — this runs
  the actual `_check_success()` from the RoboCasa environment.
- Do NOT invent your own success criteria (e.g. `wz >= 1.20`) — always use `/task/success`.
- If `/task/success` returns false, the placement failed even if z looks correct.
- Common failure: arm reaches correct height but object is in front of the cabinet,
  not inside it — always check `/task/success` after releasing.

## Rules
1. Check existing skills first — reuse and chain, don't reinvent
2. Read the SDK docs before writing robot code — don't guess APIs
3. Use rewind as your safety net — every movement is reversible
4. **You MUST test your code before finishing** — submit via /code/submit and verify it works
5. Debug failures — don't just write code and leave
6. Be concise in your reasoning
7. The sim (RoboCasa/ManiSkill) uses the same API — you are testing against the sim
8. **IRON RULE — BEFORE writing ANY code, you MUST:**
   a. Read the task's `_check_success()` source code from the task source file
      (in `~/tidybot_uni/sims/robocasa_tasks/single_stage/` or `multi_stage/`)
   b. Understand EXACTLY what conditions make the task succeed
   c. Your code's success check must be derived FROM that source code, NOT from
      the task description. The task description is vague — only the source code
      is ground truth.
   d. Use `GET http://localhost:5500/task/success` to call `_check_success()` at runtime.
   e. NEVER invent your own success criteria (e.g. height checks, distance checks).
   This is non-negotiable. Skipping this step wastes hours of debugging.

## Before finishing
Once the skill works:
1. Print a brief summary (5-10 lines) of how the skill works — the pipeline steps,
   key decisions (grasp strategy, perception approach, retry logic), and any gotchas
   you discovered during testing. This helps the reviewer and downstream skill agents
   understand your implementation without reading all the code.
Then stop.
"""

SYSTEM_PROMPT_DEV_HARDWARE = """\
You are a robotics skill developer for the TidyBot Universe project.
You are developing against **real hardware** (no simulator).

## Your Robot
- Franka Panda 7-DOF arm + mobile base + Robotiq gripper + RealSense cameras
- API at {agent_server} — read the guide at {agent_server}/docs/guide/html before writing code
- SDK reference at {agent_server}/code/sdk/markdown
- Skills run via POST /code/submit (fire-and-forget) with access to robot_sdk

## Hardware vs Sim — Key Differences
- **No sim reset** — there is no `POST /reset`. The scene is real, not resettable.
- **No `sensors.find_objects()`** — this requires a sim perception server. Use YOLO instead.
- **No `/task/success`** — there is no ground-truth success check. You verify success by
  checking gripper state, arm position, and YOLO detections after execution.
- **Evaluator works on hardware** — it reviews camera recordings from the agent server.
  Use `--no-eval` ONLY for quick exploration/debugging. For real tests, omit `--no-eval`.
- **Be cautious with movements** — real hardware can collide with real objects. Start slow,
  use small deltas, and verify positions before large moves.
- **Arm frame** — `arm.move_to_pose(x, y, z)` is in the arm's base frame.
  x = forward, y = left, z = up. Check `arm.get_state()` to understand current pose
  before moving. Do NOT assume a fixed heading — read the actual pose.

## Existing Skills
Skills live in {skills_dir}/. First check what's already there:
{existing_skills}

Read SKILL.md of relevant existing skills to understand patterns and avoid reinventing.
IMPORTANT: Ignore the `deprecated/` folder — it contains old skills from previous sessions that are no longer active.

## Skill Structure
Each skill is a directory under {skills_dir}/<skill-name>/ with:
```
<skill-name>/
├── SKILL.md              # Skill description, pipeline, usage, config
├── scripts/
│   ├── main.py           # Main skill code (uses robot_sdk)
│   └── deps.txt          # Skill dependencies (other skills needed)
```

## Your Job
You are working on the skill: **{{skill_name}}**
If {skills_dir}/{{skill_name}}/ does not exist, create it with the structure above.
If it exists, read it and modify as requested.

## Development & Testing Workflow
You must write code AND test it. Do not stop until you have a working skill.

1. **Read SDK docs first**: `curl {agent_server}/code/sdk/markdown` — understand available APIs
2. **Write scripts/main.py**: implement the skill using robot_sdk
3. **Test by submitting**: `python {submit_script} scripts/main.py --holder dev:{{skill_name}} --agent-server {agent_server}`
4. **Debug and iterate**: if it fails, read the evaluator feedback, fix the code, resubmit

### How to submit and test code

**Exploration / debugging** (quick sanity checks only — checking arm state, running a single YOLO detection, verifying objects exist):
```bash
python {submit_script} /tmp/test.py --no-eval --holder dev:{{skill_name}} --agent-server {agent_server}
```
Returns raw stdout/stderr. No evaluator. Use ONLY for short probes, NOT for testing the full skill.

**Full skill test** (ALWAYS use this for testing your skill):
```bash
python {submit_script} scripts/main.py --holder dev:{{skill_name}} --agent-server {agent_server}
```
Submits code, waits for completion, then an evaluator agent reviews
the camera recordings and robot behavior. Returns JSON with `passed` (bool)
and `feedback` (detailed evaluation).

Note: on hardware there is no sim reset before each test — the scene is real.
The evaluator reviews camera recordings fetched from the agent server.

**IMPORTANT: Do NOT use `--no-eval` when testing your skill. Always let the evaluator
review the execution. The evaluator sees camera recordings and robot state logs that
you cannot see — it is your only source of visual feedback on hardware.**

### Testing loop
- First, explore the scene with `--no-eval`: check arm state, run YOLO to see what's visible
- Then write/edit scripts/main.py and run the full test (WITHOUT --no-eval)
- The output includes `passed` (true/false) and `feedback` from an automated evaluator
- If `passed` is false: read the `feedback`, fix the code, resubmit
- If `passed` is true: continue to the next iteration or finish
- Run at least 1 passed evaluation before declaring done
- Do NOT look at recordings yourself — that is the evaluator's job

### Important
- The code sandbox allows robot_sdk imports but NOT `requests` or network libraries
- Your submitted code runs inside the agent server with full robot_sdk access
- If the arm enters error state, use rewind: the SDK has `from robot_sdk import rewind`
- **REAL HARDWARE** — movements are irreversible. Double-check positions before large moves.

### Stdout guidelines
Print useful context at each major step:
- Object detections: name, confidence, bbox, position
- Arm movements: target pose, current pose before/after
- Grasp attempts: gripper state before/after close (position, object_detected)
- Final outcome: clear SUCCESS or FAILURE with a one-line reason

Do NOT print raw data dumps, full joint arrays, or per-timestep logs.
Keep stdout to ~20-40 lines of high-level trace.

## Perception on Hardware

**Use YOLO for all object detection.** `sensors.find_objects()` is NOT available on hardware.

### YOLO 2D detection
```python
from robot_sdk import yolo
result = yolo.segment_camera("orange block, cup, bottle")
for det in result.detections:
    print(f"{{det.class_name}}: conf={{det.confidence:.2f}}, bbox={{det.bbox}}")
```

### YOLO with segmentation masks
```python
result = yolo.segment_camera("orange block", mask_format="npz")
for det in result.detections:
    if det.mask is not None:
        pixels = (det.mask > 0.5).sum()
        print(f"{{det.class_name}}: {{pixels}} mask pixels")
```

### YOLO 3D detection (if depth camera is available)
```python
result = yolo.segment_camera_3d("orange block")
for det in result.detections:
    print(f"{{det.class_name}} at {{det.position_3d}} ({{det.depth_meters:.2f}}m)")
```
Note: 3D detection requires camera intrinsics service. If it fails, fall back to 2D
detection and use visual servoing (IBVS) for positioning instead of 3D coordinates.

### Visual servoing (IBVS) pattern
When 3D positions are unreliable, use image-based visual servoing:
1. Detect object in camera image → get bbox centroid
2. Move arm to center object in frame (small delta moves)
3. Descend incrementally while keeping object centered
4. Grasp when at target height

### GraspGen — end-to-end 6-DOF grasp (Robotiq 2F-140 tuned)
```python
from robot_sdk import graspgen, wb, gripper
assert graspgen.health_check(), "GraspGen unreachable — check GRASPGEN_SERVER_URL"
g = graspgen.get_grasp_poses("orange block")   # one call: segment → point cloud → grasp
best = g.poses[0]                               # sorted by score, descending
wb.move_to_pose(best.position[0], best.position[1], best.position[2],
                quat=best.quaternion)
gripper.close(force=255)
```
**Prefer `graspgen` over hardcoded quaternions** — it's trained for this gripper
and returns ranked candidates you can iterate through if the first fails. Internally
uses Grounded-SAM when YOLO lacks the class; first call may take 30 s to load
SAM models (~5 GB), subsequent calls <3 s.

## Gripper interpretation
- `gripper.get_state()["position"]` = 0 means **FULLY CLOSED ON NOTHING** (missed)
- `position` = 20–150 means **object is in hand** (closed on something)
- `position` = 250–255 means **fully open**
- ALWAYS check `object_detected` field as well
- Do NOT treat pos=0 as success — it means you missed

## ee_pose format
- `ee_pose` is a 4x4 matrix (16 elements, column-major)
- `ee_pose[12]` = x, `ee_pose[13]` = y, `ee_pose[14]` = z (in ARM frame)
- `ee_pose[0:12]` = rotation matrix elements — NOT position!

## Rules
1. Check existing skills first — reuse and chain, don't reinvent
2. Read the SDK docs before writing robot code — don't guess APIs
3. Use rewind as your safety net — every movement is reversible
4. **You MUST test your code before finishing** — submit via /code/submit and verify it works
5. Debug failures — don't just write code and leave
6. Be concise in your reasoning
7. This is REAL HARDWARE — be careful, start with small movements, verify before committing to large moves

## Before finishing
Once the skill works:
1. Print a brief summary (5-10 lines) of how the skill works — the pipeline steps,
   key decisions (grasp strategy, perception approach, retry logic), and any gotchas
   you discovered during testing. This helps the reviewer and downstream skill agents
   understand your implementation without reading all the code.
Then stop.
"""

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

ws_clients: set = set()

# In-memory skill entries (seeded from local_repos.json, updated dynamically)
skill_entries: list[dict] = []
graph_meta: dict = {}  # top-level metadata (task_env, task_source, etc.)


def _load_entries():
    """Load entries from graph file into memory. Resets stale non-done statuses."""
    global skill_entries, graph_meta, targets, primary_target, AGENT_SERVER
    data = json.loads(LOCAL_REPOS.read_text())
    if isinstance(data, dict):
        # New format: {"task_env": "...", "entries": [...], ...}
        skill_entries = data.get("entries", [])
        graph_meta = {k: v for k, v in data.items() if k != "entries"}
    else:
        # Legacy format: flat list of entries
        skill_entries = data
        graph_meta = {}

    # Load multi-target config
    targets = graph_meta.get("targets", [])
    if targets:
        primary_target = next((t for t in targets if t.get("primary")), targets[0])
        AGENT_SERVER = primary_target["agent_server"]
        print(f"[ORCH] Loaded {len(targets)} targets, primary: {primary_target['name']} ({AGENT_SERVER})")
    else:
        primary_target = {"name": "default", "agent_server": AGENT_SERVER, "sim_api": "http://localhost:5500", "primary": True}
        targets = [primary_target]

    # Reset stale statuses from previous sessions — anything not "done" or "planned"
    # is leftover state (writing, testing, review, failed) with no live agent behind it.
    reset_count = 0
    for entry in skill_entries:
        status = entry.get("status", "planned")
        if status not in ("done", "confirmed_done", "planned"):
            entry["status"] = "failed"
            reset_count += 1
    if reset_count:
        print(f"[ORCH] Reset {reset_count} stale skill(s) to 'failed'")
        _save_entries()


def _save_entries():
    """Persist entries back to local_repos.json."""
    LOCAL_REPOS.parent.mkdir(parents=True, exist_ok=True)
    if graph_meta:
        data = {**graph_meta, "entries": skill_entries}
    else:
        data = skill_entries
    LOCAL_REPOS.write_text(json.dumps(data, indent=2))


def _find_entry(name: str) -> dict | None:
    """Find an entry by skill name."""
    for e in skill_entries:
        if e["name"] == name:
            return e
    return None


def _add_entry(name: str, description: str = "", dependencies: list[str] | None = None) -> dict:
    """Add a new skill entry. Returns the entry."""
    existing = _find_entry(name)
    if existing:
        return existing
    entry = {
        "id": f"sc-{len(skill_entries)+1:03d}",
        "name": name,
        "description": description,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "html_url": "",
        "language": "Python",
        "stars": 0,
        "is_private": False,
        "default_branch": "main",
        "success_rate": None,
        "total_trials": 0,
        "institutions_tested": 0,
        "trial_images": [],
        "dependencies": dependencies or [],
        "service_dependencies": [],
        "sdk_functions": [],
        "status": "planned",
        "agent_id": None,
        "agent_status_text": None,
        "progress_history": [],
    }
    skill_entries.append(entry)
    _save_entries()
    return entry


def _remove_entry(name: str) -> bool:
    """Remove a skill entry by name. Returns True if found."""
    global skill_entries
    before = len(skill_entries)
    skill_entries = [e for e in skill_entries if e["name"] != name]
    if len(skill_entries) < before:
        _save_entries()
        return True
    return False


def _update_entry(name: str, updates: dict) -> dict | None:
    """Update fields on an existing entry."""
    entry = _find_entry(name)
    if not entry:
        return None
    for k, v in updates.items():
        if k != "name":  # don't allow renaming via update
            entry[k] = v
    entry["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save_entries()
    return entry


@dataclass
class AgentState:
    agent_id: str
    skill: str
    agent_type: str = "dev"           # dev | evaluator
    status: str = "starting"          # starting | running | stopped | done | confirmed_done | error
    session_id: Optional[str] = None
    client: Optional[object] = None   # ClaudeSDKClient (SDK mode)
    proc: Optional[object] = None     # subprocess (CLI fallback)
    task: Optional[asyncio.Task] = None
    log: list = field(default_factory=list)
    exit_event: asyncio.Event = field(default_factory=asyncio.Event)  # set to tear down client context
    target_name: str = ""             # which target this agent is bound to (for parallel multi-target dev)
    _agent_server_url: str = ""       # agent_server URL for this target (empty = use global AGENT_SERVER)


agents: dict[str, AgentState] = {}    # agent_id -> AgentState
_spawn_lock = asyncio.Lock()          # prevents double-spawning in _auto_spawn_ready_skills

# Per-submission eval results: skill -> {"future": asyncio.Future, "execution_id": str}
_submission_evals: dict[str, dict] = {}


def _update_trial_images(skill: str, execution_id: str, agent_server_url: str = ""):
    """Update entry's trial_images with frames from the latest execution recording.

    When agent_server_url is provided (from /job-done), uses it to build image URLs
    and stores per-target trial_images.
    """
    if not agent_server_url:
        agent_server_url = AGENT_SERVER

    # Find which target this agent_server belongs to
    target_name = ""
    for t in targets:
        if t.get("agent_server") == agent_server_url:
            target_name = t["name"]
            break

    # Try local first, fall back to remote agent server
    exec_dir = PROJECT_DIR / "logs" / "code_executions" / execution_id
    frames: list[str] = []
    if exec_dir.exists():
        frames = sorted(f.name for f in exec_dir.iterdir() if f.suffix == ".jpg")
    if not frames:
        try:
            import urllib.request
            url = f"{agent_server_url}/code/recordings/{execution_id}"
            data = json.loads(urllib.request.urlopen(url, timeout=10).read())
            frames = [f for f in data.get("frames", []) if f.endswith(".jpg")]
        except Exception as e:
            print(f"[ORCH] {skill}: could not fetch remote frames for {execution_id}: {e}")
            return
    if not frames:
        return
    image_urls = [
        f"{agent_server_url}/code/recordings/{execution_id}/frames/{f}"
        for f in frames
    ]
    if len(image_urls) > 20:
        step = len(image_urls) / 20
        image_urls = [image_urls[int(i * step)] for i in range(20)]

    # Always update main trial_images (backward compat)
    update = {"trial_images": image_urls}

    # Also store per-target trial_images
    if target_name:
        entry = _find_entry(skill)
        tti = dict(entry.get("target_trial_images", {})) if entry else {}
        tti[target_name] = image_urls
        update["target_trial_images"] = tti

    _update_entry(skill, update)
    label = f"{skill}@{target_name}" if target_name else skill
    print(f"[ORCH] {label}: updated trial_images ({len(image_urls)} frames from {execution_id})")


async def _run_submission_eval(skill: str, execution_id: str, job_agent_server: str = ""):
    """Run evaluator for a specific submission, store result in _submission_evals."""
    # Update dashboard images from this execution
    _update_trial_images(skill, execution_id, agent_server_url=job_agent_server)
    await broadcast_full_sync()

    try:
        result = await run_evaluator(skill, execution_id=execution_id)
    except Exception as e:
        result = {"passed": True, "feedback": f"Evaluator error: {e}"}
    entry = _submission_evals.get(skill)
    if entry and not entry["future"].done():
        entry["future"].set_result(result)


# ---------------------------------------------------------------------------
# WebSocket: browser <-> orchestrator
# ---------------------------------------------------------------------------

async def ws_broadcast(msg: dict):
    data = json.dumps(msg)
    gone = set()
    for c in ws_clients:
        try:
            await asyncio.wait_for(c.send(data), timeout=5.0)
        except (websockets.ConnectionClosed, asyncio.TimeoutError):
            gone.add(c)
    ws_clients.difference_update(gone)


async def ws_broadcast_status(skill: str, agent_id: str, status: str, text: str,
                              extra: dict | None = None):
    # Look up agent type
    state = agents.get(agent_id)
    agent_type = state.agent_type if state else "dev"

    payload = {
        "name": skill,
        "status": _map_status(status, agent_type),
        "agent_id": agent_id,
        "agent_status_text": text,
        "agent_type": agent_type,
    }
    if extra:
        payload.update(extra)
    await ws_broadcast({"type": "status_update", "payload": payload})


async def ws_broadcast_agent_msg(entry_id: str, text: str, agent_type: str = "dev"):
    """Send a chat bubble to the agent's chat log in the browser."""
    await ws_broadcast({
        "type": "agent_message",
        "entry_id": entry_id,
        "text": text,
        "agent_type": agent_type,
    })


def _map_status(internal: str, agent_type: str = "dev") -> str:
    """Map internal status to the frontend's expected status strings."""
    if agent_type == "evaluator":
        return {
            "starting": "evaluating",
            "running": "evaluating",
            "stopped": "failed",
            "done": "review",
            "confirmed_done": "done",
            "error": "failed",
        }.get(internal, internal)
    return {
        "starting": "writing",
        "running": "writing",
        "paused": "writing",
        "stopped": "failed",
        "done": "review",
        "confirmed_done": "done",
        "error": "failed",
    }.get(internal, internal)


_session_log_cache: dict[str, list] | None = None


def _load_session_logs() -> dict[str, list]:
    """Return cached session logs, loading from disk on first call."""
    global _session_log_cache
    if _session_log_cache is not None:
        return _session_log_cache
    logs_by_skill: dict[str, list] = {}
    if SESSION_LOG.exists():
        for line in SESSION_LOG.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                logs_by_skill[rec["skill"]] = rec.get("log", [])[-50:]
            except (json.JSONDecodeError, KeyError):
                continue
    _session_log_cache = logs_by_skill
    return _session_log_cache


def _invalidate_session_log_cache():
    """Invalidate the session log cache so next read reloads from disk."""
    global _session_log_cache
    _session_log_cache = None


def build_full_sync() -> dict:
    """Build a full_sync payload from in-memory entries with live agent state overlay."""
    import copy
    repos = copy.deepcopy(skill_entries)

    session_logs = _load_session_logs()

    # Overlay live agent state onto matching entries
    # Collect ALL agents per skill (for multi-target parallel dev)
    agents_by_skill: dict[str, list[AgentState]] = {}
    for a in agents.values():
        agents_by_skill.setdefault(a.skill, []).append(a)
    for repo in repos:
        name = repo["name"]
        skill_agents = agents_by_skill.get(name, [])
        if skill_agents:
            # Pick best agent for top-level status (prefer running)
            best = next((a for a in skill_agents if a.status in ("starting", "running")), skill_agents[0])
            repo["status"] = _map_status(best.status, best.agent_type)
            repo["agent_id"] = best.agent_id
            repo["agent_status_text"] = f"{best.status}"
            repo["agent_type"] = best.agent_type
            repo["agent_log"] = list(best.log[-50:])
            # Per-target agent status + logs (for dashboard multi-target display)
            repo["target_agents"] = {}
            for a in skill_agents:
                tname = a.target_name or "default"
                ta_entry = {
                    "agent_id": a.agent_id,
                    "status": a.status,
                    "agent_type": a.agent_type,
                    "agent_log": list(a.log[-50:]),
                }
                # Include per-target agent_server URL so frontend can build recording URLs
                target_dict = next((t for t in targets if t["name"] == tname), None)
                if target_dict:
                    ta_entry["agent_server"] = target_dict["agent_server"]
                repo["target_agents"][tname] = ta_entry
        else:
            # No live agent — use persisted session log
            repo["agent_log"] = session_logs.get(name, [])

    # Build agents list
    agents_list = []
    for a in agents.values():
        agents_list.append({
            "agent_id": a.agent_id,
            "skill": a.skill,
            "agent_type": a.agent_type,
            "status": a.status,
            "target": a.target_name,
        })

    # Session-demo aggregates for dashboard's live session-demo hex (index.html)
    # Reads persisted session log (dev + evaluator runs across time) + currently
    # live agents. `graph` lets the frontend key hexes per-graph; the counts
    # let it render env bars with session totals.
    session_count = 0
    per_env_session_count: dict[str, int] = {}
    if SESSION_LOG.exists():
        try:
            for line in SESSION_LOG.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("agent_type") != "dev":
                    continue  # only count dev sessions, not evaluator noise
                session_count += 1
                env = rec.get("target") or "default"
                per_env_session_count[env] = per_env_session_count.get(env, 0) + 1
        except Exception as e:
            print(f"[ORCH] warn: failed to aggregate session counts: {e}")

    live_sessions = []
    for a in agents.values():
        if a.session_id:
            live_sessions.append({
                "agent_id": a.agent_id,
                "session_id": a.session_id,
                "skill": a.skill,
                "agent_type": a.agent_type,
                "target": a.target_name or "default",
                "status": a.status,
                # Include current log snapshot so sessions.html renders live
                # agent messages (not just "No messages" placeholder). Truncated
                # to last 50 lines to keep payload small.
                "log": list(a.log[-50:]),
                "num_turns": len(a.log),  # approximate — one entry per reply
                "cost_usd": 0,             # unknown until ResultMessage arrives
                "in_progress": a.status in ("starting", "running", "paused", "writing"),
                "timestamp": time.time(),
            })

    return {
        "entries": repos,
        "agents": agents_list,
        "targets": targets,
        "graph": GRAPH_DIR.name,
        "session_count": session_count,
        "per_env_session_count": per_env_session_count,
        "live_sessions": live_sessions,
    }


async def broadcast_full_sync():
    """Broadcast full_sync to all connected browsers (call after entry changes)."""
    await ws_broadcast({"type": "full_sync", "payload": build_full_sync()})


async def ws_handler(websocket):
    ws_clients.add(websocket)
    print(f"[WS] client connected ({len(ws_clients)} total)")

    # Send current state on connect
    await websocket.send(json.dumps({"type": "full_sync", "payload": build_full_sync()}))

    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
                t = msg.get("type")

                if t == "inject":
                    agent_id = msg.get("agent_id", "")
                    text = msg.get("text", "")
                    print(f"[WS] inject -> {agent_id}: {text}")
                    asyncio.create_task(inject_hint(agent_id, text))

                elif t == "stop":
                    agent_id = msg.get("agent_id", "")
                    print(f"[WS] stop -> {agent_id}")
                    asyncio.create_task(stop_agent(agent_id))

                elif t == "kill":
                    agent_id = msg.get("agent_id", "")
                    print(f"[WS] kill -> {agent_id}")
                    asyncio.create_task(kill_agent(agent_id))

                elif t == "retry":
                    skill = msg.get("skill", "")
                    print(f"[WS] retry -> {skill}")
                    asyncio.create_task(spawn_agent(
                        skill,
                        f"Continue working on the '{skill}' skill. Pick up where the previous agent left off.",
                    ))

                elif t == "edit":
                    skill = msg.get("skill", "")
                    text = msg.get("text", "")
                    print(f"[WS] edit/spawn -> {skill}: {text}")
                    asyncio.create_task(spawn_skill_pipeline(skill, text))

                elif t == "spawn":
                    skill = msg.get("skill", "new-task")
                    prompt = msg.get("prompt", "")
                    print(f"[WS] spawn -> {skill}: {prompt[:80]}")
                    asyncio.create_task(spawn_agent(skill, prompt))

                elif t == "confirm_done":
                    skill = msg.get("skill", "")
                    agent_id = msg.get("agent_id", "")
                    print(f"[WS] confirm_done -> {skill}")
                    # Keep agent alive (paused) — can still receive hints
                    state = agents.get(agent_id)
                    if state:
                        state.status = "confirmed_done"
                    asyncio.create_task(ws_broadcast_status(skill, agent_id, "confirmed_done", "Done"))
                    # Confirm in graph and auto-spawn downstream skills
                    asyncio.create_task(_confirm_skill_done(skill))


                elif t == "add_entry":
                    name = msg.get("name", "")
                    desc = msg.get("description", "")
                    deps = msg.get("dependencies", [])
                    print(f"[WS] add_entry -> {name}")
                    _add_entry(name, desc, deps)
                    asyncio.create_task(broadcast_full_sync())

                elif t == "remove_entry":
                    name = msg.get("name", "")
                    print(f"[WS] remove_entry -> {name}")
                    _remove_entry(name)
                    asyncio.create_task(broadcast_full_sync())

                elif t == "update_entry":
                    name = msg.get("name", "")
                    updates = msg.get("updates", {})
                    print(f"[WS] update_entry -> {name}: {list(updates.keys())}")
                    _update_entry(name, updates)
                    asyncio.create_task(broadcast_full_sync())

                else:
                    print(f"[WS] unknown: {t}")

            except json.JSONDecodeError:
                print(f"[WS] bad JSON: {raw[:100]}")
    except websockets.ConnectionClosed:
        pass
    finally:
        ws_clients.discard(websocket)
        print(f"[WS] client disconnected ({len(ws_clients)} total)")


# ---------------------------------------------------------------------------
# Agent lifecycle — SDK mode
# ---------------------------------------------------------------------------

async def spawn_agent(skill: str, prompt: str, agent_type: str = "dev",
                      target: dict | None = None) -> str:
    """Spawn a new Claude Code agent for a skill/task.

    Args:
        target: optional target dict with 'name', 'agent_server', 'sim_api'.
                If provided, the agent uses this target's agent_server instead of
                the global primary. Used for parallel multi-target development.
    """
    global dev_mode
    if not dev_mode:
        msg = f"[ORCH] Blocked spawn of {agent_type} agent for '{skill}' — still in planning mode. Run /xbot-start first."
        print(msg)
        await ws_broadcast({"type": "error", "message": msg})
        return ""

    target_name = target["name"] if target else ""

    # Stop any active agent FOR THIS TARGET and preserve session_id for resume
    prev_session_id = None
    for aid, a in list(agents.items()):
        if a.skill == skill and a.target_name == target_name:
            if a.session_id:
                prev_session_id = a.session_id
            if a.status in ("starting", "running"):
                await stop_agent(aid)
            a.exit_event.set()  # unblock pause-wait so client context closes
            agents.pop(aid, None)

    # Also check graph.json entry for persisted session_id (survives orchestrator restarts)
    if not prev_session_id and not target_name:
        entry = _find_entry(skill)
        if entry and entry.get("session_id"):
            prev_session_id = entry["session_id"]

    label = f"{skill}@{target_name}" if target_name else skill
    if prev_session_id:
        print(f"[ORCH] {label}: will resume session {prev_session_id}")
    else:
        print(f"[ORCH] {label}: no previous session, starting fresh")

    agent_id = f"agent-{uuid.uuid4().hex[:8]}"
    state = AgentState(agent_id=agent_id, skill=skill, agent_type=agent_type)
    state.session_id = prev_session_id  # carry over for resume
    state.target_name = target_name
    state._agent_server_url = target["agent_server"] if target else ""
    agents[agent_id] = state

    status_label = f"Developing on {target_name}..." if target_name else "Developing..."
    await ws_broadcast_status(skill, agent_id, "starting", status_label)

    DEV_AGENT_TIMEOUT = 14400  # 4 hours max for dev agent
    async def _wrapped():
        try:
            await asyncio.wait_for(_run_agent_sdk(state, prompt), timeout=DEV_AGENT_TIMEOUT)
        except asyncio.TimeoutError:
            print(f"[SDK] {skill}: TIMEOUT after {DEV_AGENT_TIMEOUT}s")
            state.status = "failed"
            _update_entry(skill, {"status": "failed"})
            await broadcast_full_sync()
            await ws_broadcast_agent_msg(skill, f"Dev agent timed out after {DEV_AGENT_TIMEOUT // 60} minutes.", state.agent_type)
        except Exception as e:
            print(f"[SDK] {skill}: UNHANDLED EXCEPTION: {e}")
            import traceback; traceback.print_exc()
    state.task = asyncio.create_task(_wrapped())
    print(f"[ORCH] {skill}: task created, id={agent_id}")

    return agent_id


async def spawn_skill_pipeline(skill: str, user_prompt: str):
    """Spawn a dev agent for the skill. The dev agent writes, tests, and debugs the code."""
    # Auto-generate ground-truth test for root skill (task_env)
    if _is_task_root(skill):
        _auto_generate_task_root_test(skill)
    print(f"[ORCH] {skill}: spawning dev agent")
    await spawn_agent(skill, user_prompt, agent_type="dev")


async def run_mechanical_test(skill: str) -> dict:
    """Run the skill's test as a subprocess and parse results.
    Returns {"passed": bool, "success_rate": float, "total_trials": int, "stdout": str, "stderr": str}.
    Also updates the entry status and broadcasts."""
    test_file = SKILLS_DIR / skill / "tests" / "run_trials.py"
    if not test_file.exists():
        await ws_broadcast_agent_msg(skill, "No test file found", "test")
        _update_entry(skill, {"status": "failed"})
        await broadcast_full_sync()
        return {"passed": False, "success_rate": 0, "total_trials": 0, "stdout": "", "stderr": "No test file"}

    await ws_broadcast_agent_msg(skill, "Running test subprocess...", "test")

    try:
        # Run test as a subprocess (tests use requests to call agent server)
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        # Ensure user site-packages are available (for requests, etc.)
        extra_py = os.path.expanduser(f"~/.local/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
        if extra_py not in env.get("PYTHONPATH", ""):
            env["PYTHONPATH"] = f"{extra_py}:{env.get('PYTHONPATH', '')}"

        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(test_file.resolve()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str((SKILLS_DIR / skill).resolve()),
            env=env,
        )

        start_time = time.time()

        # Poll proc.wait() for progress updates; communicate() once after exit
        while True:
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
                break  # process finished
            except asyncio.TimeoutError:
                elapsed = int(time.time() - start_time)
                if elapsed > 600:
                    proc.kill()
                    await proc.wait()
                    await ws_broadcast_agent_msg(skill, "Test timed out (10 min limit)", "test")
                    _update_entry(skill, {"status": "failed"})
                    await broadcast_full_sync()
                    return {"passed": False, "success_rate": 0, "total_trials": 0, "stdout": "", "stderr": "Timed out"}

                await ws_broadcast_status(skill, "", "running", f"Testing... {elapsed}s")

        stdout_bytes, stderr_bytes = await proc.communicate()
        exit_code = proc.returncode
        stdout = stdout_bytes.decode(errors="replace")
        stderr = stderr_bytes.decode(errors="replace")

        print(f"[TEST] {skill}: exit_code={exit_code}")
        if stderr.strip():
            print(f"[TEST] {skill}: stderr={stderr[-500:]}")

        await ws_broadcast_agent_msg(skill, f"Test completed (exit {exit_code})", "test")

        # Parse JSON results — check summary.json first, then last line of stdout
        sr = None
        total_trials = 0
        summary_file = SKILLS_DIR / skill / "tests" / "results" / "summary.json"

        if summary_file.exists():
            try:
                summary = json.loads(summary_file.read_text())
                sr = summary.get("success_rate",
                                 summary.get("passed", 0) / max(summary.get("total_trials", 1), 1) * 100)
                total_trials = summary.get("total_trials", 0)
                print(f"[TEST] {skill}: parsed summary.json — sr={sr}%, trials={total_trials}")
            except (json.JSONDecodeError, ValueError) as e:
                print(f"[TEST] {skill}: could not parse summary.json: {e}")

        if sr is None:
            # Fallback: parse last line of stdout as JSON
            try:
                results = json.loads(stdout.strip().split('\n')[-1])
                sr = results.get("success_rate",
                                 results.get("passed", 0) / max(results.get("total_trials", 1), 1) * 100)
                total_trials = results.get("total_trials", 0)
            except (json.JSONDecodeError, IndexError, ValueError):
                sr = 100.0 if exit_code == 0 else 0.0
                total_trials = 1

        # Update entry and broadcast
        passed = sr is not None and sr > 0
        if passed and autonomous_mode:
            status_label = "done"
        elif passed:
            status_label = "review"
        else:
            status_label = "failed"

        _update_entry(skill, {
            "success_rate": sr,
            "total_trials": total_trials,
            "status": status_label,
        })
        if status_label == "review":
            msg = f"Tests complete (success_rate={sr:.0f}%). Waiting for review."
        elif status_label == "done":
            msg = f"Tests complete (success_rate={sr:.0f}%). Auto-promoted (autonomous mode)."
        else:
            msg = f"Tests complete (success_rate={sr:.0f}%). Failed."
        await ws_broadcast_agent_msg(skill, msg, "test")
        await broadcast_full_sync()
        print(f"[TEST] {skill}: {status_label} (sr={sr}%, trials={total_trials})")

        # Autonomous: auto-promote and spawn downstream
        if status_label == "done":
            await _auto_spawn_ready_skills()

        return {"passed": passed, "success_rate": sr, "total_trials": total_trials, "stdout": stdout, "stderr": stderr}

    except Exception as e:
        await ws_broadcast_agent_msg(skill, f"Test error: {e}", "test")
        traceback.print_exc()
        _update_entry(skill, {"status": "failed"})
        await broadcast_full_sync()
        return {"passed": False, "success_rate": 0, "total_trials": 0, "stdout": "", "stderr": str(e)}


async def run_multi_target_test(skill: str) -> dict:
    """Run skill code on all targets in parallel, collect per-target results.

    Returns {"target_results": {name: {passed, execution_id}}, "aggregate_pass": bool, "success_rate": float}.
    """
    import urllib.request, urllib.error

    # 1. Bundle the skill
    bundler = str(Path(__file__).parent / ".." / "tidybot-bundle" / "scripts" / "tidybot-bundle.py")
    bundler = str(Path(bundler).resolve())
    skills_dir = str(SKILLS_DIR)

    bundle_out = SKILLS_DIR / skill / "scripts" / "_bundled.py"
    proc = await asyncio.create_subprocess_exec(
        sys.executable, bundler, skill, "--skills-dir", skills_dir, "-o", str(bundle_out),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode(errors="replace")[-300:]
        await ws_broadcast_agent_msg(skill, f"Bundle failed: {err}", "test")
        return {"target_results": {}, "aggregate_pass": False, "success_rate": 0}

    if not bundle_out.exists():
        # Fallback: use main.py directly
        bundle_out = SKILLS_DIR / skill / "scripts" / "main.py"
    code = bundle_out.read_text()

    # 2. Submit to all targets in parallel
    async def _test_one_target(target: dict) -> tuple[str, dict]:
        name = target["name"]
        server = target["agent_server"]
        sim_api = target.get("sim_api", "")

        await ws_broadcast_agent_msg(skill, f"Testing on {name}...", "test")
        try:
            # Reset sim
            if sim_api:
                try:
                    req = urllib.request.Request(f"{sim_api}/reset", data=b'{}',
                        headers={"Content-Type": "application/json"}, method="POST")
                    urllib.request.urlopen(req, timeout=10)
                except Exception:
                    pass

            # Submit code
            data = json.dumps({"code": code, "holder": f"test:{skill}", "reset_env": True}).encode()
            req = urllib.request.Request(f"{server}/code/submit", data=data,
                headers={"Content-Type": "application/json"})
            resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
            job_id = resp["job_id"]

            # Poll until done (up to 5 min)
            for _ in range(150):
                await asyncio.sleep(2)
                try:
                    job = json.loads(urllib.request.urlopen(f"{server}/code/jobs/{job_id}", timeout=5).read())
                    if job.get("status") in ("completed", "failed"):
                        break
                except Exception:
                    continue
            else:
                return name, {"passed": False, "error": "timeout", "execution_id": ""}

            execution_id = job.get("execution_id", "")

            # Check sim success
            passed = False
            if sim_api:
                try:
                    result = json.loads(urllib.request.urlopen(f"{sim_api}/task/success", timeout=5).read())
                    passed = result.get("success", False)
                except Exception:
                    pass

            return name, {"passed": passed, "execution_id": execution_id}

        except Exception as e:
            return name, {"passed": False, "error": str(e)[:200], "execution_id": ""}

    # Run all targets concurrently
    results_list = await asyncio.gather(*[_test_one_target(t) for t in targets])
    target_results = dict(results_list)

    # Compute aggregate
    passed_count = sum(1 for r in target_results.values() if r.get("passed"))
    total = len(target_results)
    aggregate_pass = passed_count == total
    success_rate = (passed_count / total * 100) if total > 0 else 0

    # Update entry
    _update_entry(skill, {
        "target_results": target_results,
        "success_rate": success_rate,
        "total_trials": total,
    })

    # Broadcast per-target results
    summary_parts = [f"{n}: {'PASS' if r.get('passed') else 'FAIL'}" for n, r in target_results.items()]
    msg = f"Multi-target test: {passed_count}/{total} passed — " + ", ".join(summary_parts)
    await ws_broadcast_agent_msg(skill, msg, "test")
    await broadcast_full_sync()

    return {"target_results": target_results, "aggregate_pass": aggregate_pass, "success_rate": success_rate}


MAX_ROOT_TEST_ATTEMPTS = 3
_skills_in_test_loop: set[str] = set()

async def _root_skill_test_loop(skill: str):
    """Run ground-truth test for root skill. On failure, re-spawn dev up to MAX_ROOT_TEST_ATTEMPTS times.
    After passing or exhausting attempts, go to review."""
    _skills_in_test_loop.add(skill)
    try:
        await _root_skill_test_loop_inner(skill)
    finally:
        _skills_in_test_loop.discard(skill)


async def _root_skill_test_loop_inner(skill: str):
    entry = _find_entry(skill)
    attempt = entry.get("test_attempts", 0) if entry else 0

    while attempt < MAX_ROOT_TEST_ATTEMPTS:
        attempt += 1
        _update_entry(skill, {"test_attempts": attempt, "status": "testing"})
        await broadcast_full_sync()

        await ws_broadcast_agent_msg(skill, f"Ground-truth test attempt {attempt}/{MAX_ROOT_TEST_ATTEMPTS}", "test")
        result = await run_mechanical_test(skill)

        if result["passed"]:
            # Primary target passed — run multi-target validation if multiple targets
            if len(targets) > 1:
                await ws_broadcast_agent_msg(skill, f"Primary test passed — validating on all {len(targets)} targets...", "test")
                mt_result = await run_multi_target_test(skill)
                if not mt_result["aggregate_pass"]:
                    # Some targets failed — treat as test failure for retry
                    result["passed"] = False
                    result["success_rate"] = mt_result["success_rate"]
                    result["stdout"] = json.dumps(mt_result["target_results"], indent=2)
                    result["stderr"] = ""
                    # Fall through to failure handling below
                else:
                    await ws_broadcast_agent_msg(skill, f"All {len(targets)} targets PASSED (attempt {attempt}). Waiting for review.", "test")
                    _update_entry(skill, {"status": "review"})
                    await broadcast_full_sync()
                    return
            else:
                # Single target — go straight to review
                await ws_broadcast_agent_msg(skill, f"Ground-truth test PASSED (attempt {attempt}). Waiting for review.", "test")
                _update_entry(skill, {"status": "review"})
                await broadcast_full_sync()
                return

        if attempt >= MAX_ROOT_TEST_ATTEMPTS:
            break

        # Test failed — re-spawn dev with feedback
        stdout_tail = result["stdout"][-500:] if result["stdout"] else ""
        stderr_tail = result["stderr"][-500:] if result["stderr"] else ""
        feedback = f"Ground-truth test failed (attempt {attempt}/{MAX_ROOT_TEST_ATTEMPTS}). " \
                   f"Success rate: {result['success_rate']:.0f}%.\n" \
                   f"Test stdout:\n{stdout_tail}\nTest stderr:\n{stderr_tail}\n\n" \
                   f"Fix the skill code and try again."

        await ws_broadcast_agent_msg(skill, f"Test failed (attempt {attempt}) — re-spawning dev agent.", "test")

        desc = entry.get("description", skill) if entry else skill
        prompt = f"Implement the '{skill}' skill: {desc}\n\n## Previous test failure\n{feedback}"
        lessons_file = SKILLS_DIR / skill / "LESSONS.md"
        if lessons_file.exists():
            prompt += f"\n\n## Previous Debugging Lessons (READ CAREFULLY)\n{lessons_file.read_text().strip()}"
        await spawn_agent(skill, prompt, agent_type="dev")
        # spawn_agent cleans up old agent state and broadcasts "starting"/"writing"

        # Wait for the dev agent to finish before testing again
        # Find the agent we just spawned
        dev_state = None
        for a in agents.values():
            if a.skill == skill and a.status in ("starting", "running"):
                dev_state = a
                break
        if dev_state and dev_state.task:
            await dev_state.task

    # Exhausted all attempts — go to review anyway so user can inspect
    await ws_broadcast_agent_msg(skill, f"Ground-truth test failed after {attempt} attempts. Sending to review.", "test")
    _update_entry(skill, {"status": "review"})
    await broadcast_full_sync()


def _get_system_prompt(agent_type: str, skill_name: str = "",
                       agent_server_url: str = "") -> str:
    """Get system prompt for agent type, populated with live skills list and dependency code."""
    # List existing skills on disk
    skills_list = ""
    if SKILLS_DIR.exists():
        skill_dirs = sorted(d.name for d in SKILLS_DIR.iterdir() if d.is_dir() and not d.name.startswith('.') and d.name != 'deprecated')
        has_target = skill_name in skill_dirs
        skills_list = "\n".join(f"  - {s}{'  ← (your target)' if s == skill_name else ''}" for s in skill_dirs)
        if skill_name and not has_target:
            skills_list += f"\n  - {skill_name}  ← DOES NOT EXIST YET (create it)"
    else:
        skills_list = "  (no skills directory found)"

    # For dev agents: tell them about dependencies and how to bundle
    dep_context = ""
    if agent_type == "dev" and skill_name:
        entry = next((e for e in skill_entries if e["name"] == skill_name), None)
        if entry and entry.get("dependencies"):
            dep_lines = []
            for dep_name in entry["dependencies"]:
                dep_dir = SKILLS_DIR / dep_name
                dep_lines.append(f"- `{dep_name}` — located at `{dep_dir}/`")
            bundler_path = Path(__file__).resolve().parent / "tidybot-bundle" / "scripts" / "tidybot-bundle.py"
            dep_context = (
                "\n\n## Dependency Skills\n\n"
                "Your skill depends on these (read their SKILL.md for the interface):\n"
                + "\n".join(dep_lines)
                + "\n\nList them in `scripts/deps.txt` (one per line). "
                + "When submitting code for execution, use the bundler to produce a single script:\n"
                + f"```bash\npython {bundler_path} {skill_name} --skills-dir {SKILLS_DIR} -o bundled.py\n```\n"
                + "The bundler resolves deps.txt, topologically sorts, deduplicates imports, "
                + "and inlines everything into one file ready for `/code/execute`.\n"
            )

    # Choose prompt based on whether we're targeting hardware or sim
    is_hardware = primary_target.get("sim_api") is None
    base_prompt = SYSTEM_PROMPT_DEV_HARDWARE if is_hardware else SYSTEM_PROMPT_DEV

    effective_server = agent_server_url or AGENT_SERVER
    result = base_prompt.format(
        agent_server=effective_server,
        existing_skills=skills_list,
        skill_name=skill_name,
        skills_dir=SKILLS_DIR,
        submit_script=Path(__file__).parent / "submit_and_wait.py",
        project_dir=PROJECT_DIR,
    )
    # Append dependency context for dev agents
    if dep_context:
        result += dep_context

    if not is_hardware:
        # Tell the agent the current task_env so it doesn't need to guess or restart sim
        task_env = graph_meta.get("task_env")
        if task_env:
            result += (
                f"\n\n## Current Task Environment\n\n"
                f"The sim is already running `{task_env}`. "
                f"**Do NOT kill or restart the sim or agent server.** "
                f"They are managed externally. If you see a different task in `/task/info`, "
                f"it means the sim is still loading — wait and retry, do not restart.\n"
            )

    return result



def _save_session_mapping(state: AgentState, message):
    """Append a line to agent_sessions.jsonl mapping session_id → skill + agent type + log."""
    import datetime
    entry = {
        "session_id": message.session_id,
        "skill": state.skill,
        "agent_type": state.agent_type,
        "agent_id": state.agent_id,
        "cost_usd": message.total_cost_usd,
        "num_turns": message.num_turns,
        "log": list(state.log),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    with open(SESSION_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    _invalidate_session_log_cache()
    print(f"[SDK] {state.skill}: session {message.session_id} logged to {SESSION_LOG.name}")


async def _run_agent_sdk(state: AgentState, prompt: str):
    """Run an agent using the Claude Agent SDK (ClaudeSDKClient)."""
    # Resume previous session if available, otherwise start fresh
    resume_id = state.session_id
    options = ClaudeAgentOptions(
        cwd=str(WORKSPACE_DIR),  # claude-code/, so `graphs/<name>/...` paths land in the right place
        permission_mode="bypassPermissions",
        system_prompt=_get_system_prompt(state.agent_type, state.skill,
                                        agent_server_url=state._agent_server_url),
        model="claude-opus-4-6",
        resume=resume_id if resume_id else None,
    )

    try:
        if resume_id:
            print(f"[SDK] {state.skill}: RESUMING session {resume_id}")
        else:
            print(f"[SDK] {state.skill}: creating new session")
        client = ClaudeSDKClient(options=options)
        state.client = client

        print(f"[SDK] {state.skill}: entering client context...")
        async with client:
            state.status = "running"
            print(f"[SDK] {state.skill}: client ready, sending query...")
            await ws_broadcast_status(state.skill, state.agent_id, "running",
                                      "Resuming..." if resume_id else "Working...")

            # First query (or resume prompt)
            if resume_id:
                await client.query(f"Continue working on this skill. Your previous session is being resumed.\n\n{prompt}")
            else:
                await client.query(prompt)
            print(f"[SDK] {state.skill}: query sent, consuming response...")
            await _consume_sdk_response(state, client)
            print(f"[SDK] {state.skill}: response consumed")

            await _resolve_completion(state)

            if state.status in ("paused", "done", "confirmed_done"):
                print(f"[SDK] {state.skill}: keeping client alive (status={state.status}), waiting for exit...")
                await state.exit_event.wait()
                print(f"[SDK] {state.skill}: exit_event received, closing client")

    except asyncio.CancelledError:
        state.status = "stopped"
        await ws_broadcast_status(state.skill, state.agent_id, "stopped", "Cancelled")
    except Exception as e:
        state.status = "error"
        err = str(e)[:200]
        print(f"[SDK] {state.skill}: ERROR: {err}")
        traceback.print_exc()
        state.log.append({"text": f"ERROR: {err}", "role": "agent"})
        await ws_broadcast_status(state.skill, state.agent_id, "error", err)
        await ws_broadcast_agent_msg(state.skill, f"Error: {err}", state.agent_type)


async def _resolve_completion(state: AgentState) -> None:
    """Transition state after consuming a response: broadcast outcome, optionally pause."""
    if state.status != "running":
        return
    entry = _find_entry(state.skill)
    if entry and entry.get("status") == "done":
        state.status = "confirmed_done"
        await ws_broadcast_agent_msg(state.skill, "Hint applied — skill still confirmed done.", state.agent_type)
    else:
        state.status = "done"
        await _handle_agent_done(state)
        # Only pause if _handle_agent_done didn't set a different status (e.g. "running" for eval retry)
        if state.status == "done" and not _is_task_root(state.skill) and not autonomous_mode:
            state.status = "paused"


MAX_EVAL_RETRIES = 2  # max times evaluator can send feedback before giving up
_eval_attempt_count: dict[str, int] = {}  # skill -> attempt count

SYSTEM_PROMPT_EVALUATOR = """\
You are a robotics skill evaluator for the TidyBot Universe project.

## Your Job
Review the execution recording of skill **{{skill_name}}** and determine if it worked correctly.
You do NOT write or fix code — you only evaluate and report.

## Skill Description
{{skill_description}}

## What to Review
1. Read the skill documentation: {{skill_dir}}/SKILL.md (if it exists)
2. Read the skill code: {{skill_code_path}}
3. Review the execution recording at: {{exec_dir}}/

**IMPORTANT: Read logs first, images selectively. Do NOT read every image.**

**Step 1 — Logs first:**
- Read metadata.json for execution summary, stdout, stderr, duration
- Read state_log.jsonl for robot state trajectory (arm joints, base pose, gripper width, object_detected)
- From the logs, identify the KEY MOMENTS: when gripper opened/closed, when base stopped moving,
  when EE reached grasp height, the final state. Note the frame numbers for these moments.

**Step 2 — Selective images (max 10-15):**
Only read images at the key moments you identified from the logs:
- First frame (starting state)
- Frame when robot reaches the object area
- Frame right before grasp (gripper about to close)
- Frame right after grasp (gripper closed)
- Frame after lift (if applicable)
- Last frame (final state)
Use both base_camera and wrist_camera for the most critical moment (grasp attempt).
Skip all other frames — the logs already tell you what happened.

## Evaluation Criteria
- Did the robot move to the expected positions?
- Did the gripper open/close at the right times?
- Did the robot interact with the correct objects?
- Did stdout/stderr indicate errors?
- Does the trajectory make sense for the skill's goal?

## Output
Your evaluation will be sent directly to the dev agent as their ONLY feedback.
They cannot see stdout, stderr, camera images, or logs — only what you write here.
Be thorough and actionable.

Write your evaluation in this format:

### What happened
Describe what the camera images show: robot poses, object interactions, scene state.
Include relevant stdout/stderr excerpts (errors, printed values, object detections).

### Result
State clearly whether the skill achieved its goal and why/why not.

### Issues (if failed)
For each issue, describe:
- What went wrong (be specific: which object, which step, what values)
- What the likely cause is
- What the dev agent should try to fix it

End with exactly one line of JSON:
```
EVAL_RESULT: {{"passed": true/false, "feedback": "detailed paragraph summarizing the above"}}
```
The `feedback` field should be a full paragraph (not one line) covering what happened,
what went wrong, and what to fix. This is the dev agent's primary debugging input.

Only fail if something clearly went wrong (wrong object, missed grasp,
collision, error in output, robot didn't move, etc.). Minor imperfections are OK.
"""


async def _fetch_remote_recording(execution_id: str, skill: str) -> Path | None:
    """Download a recording from the remote agent server to a local cache.

    Fetches metadata, stdout/stderr (from job), state timeline, and camera frames.
    Returns the local cache directory path, or None on failure.
    """
    import urllib.request, urllib.error

    cache_dir = PROJECT_DIR / "logs" / "code_executions" / execution_id
    metadata_path = cache_dir / "metadata.json"

    # Already cached?
    if metadata_path.exists():
        return cache_dir

    print(f"[EVAL] {skill}: fetching recording {execution_id} from {AGENT_SERVER}")

    try:
        # 1. Fetch recording metadata (has timeline, frames list, cameras, duration)
        rec_url = f"{AGENT_SERVER}/code/recordings/{execution_id}"
        rec_data = json.loads(await asyncio.to_thread(
            lambda: urllib.request.urlopen(rec_url, timeout=15).read()
        ))
    except Exception as e:
        print(f"[EVAL] {skill}: failed to fetch recording {execution_id}: {e}")
        return None

    cache_dir.mkdir(parents=True, exist_ok=True)

    # 2. Fetch job info for stdout/stderr
    stdout, stderr = "", ""
    try:
        # Find the job that produced this execution
        jobs_url = f"{AGENT_SERVER}/code/jobs"
        jobs_data = json.loads(await asyncio.to_thread(
            lambda: urllib.request.urlopen(jobs_url, timeout=10).read()
        ))
        job_list = jobs_data.get("jobs", jobs_data) if isinstance(jobs_data, dict) else jobs_data
        for job in job_list:
            if job.get("execution_id") == execution_id:
                result = job.get("result", {})
                if isinstance(result, dict):
                    stdout = result.get("stdout", "")
                    stderr = result.get("stderr", "")
                break
    except Exception as e:
        print(f"[EVAL] {skill}: could not fetch job stdout for {execution_id}: {e}")

    # 3. Write metadata.json (what the evaluator agent expects)
    metadata = {
        "execution_id": execution_id,
        "started_at": rec_data.get("started_at"),
        "stopped_at": rec_data.get("stopped_at"),
        "duration": rec_data.get("duration"),
        "cameras": rec_data.get("cameras", []),
        "frame_count": rec_data.get("frame_count", 0),
        "stdout": stdout,
        "stderr": stderr,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2))

    # 4. Write state_log.jsonl from timeline
    timeline = rec_data.get("timeline", [])
    if timeline:
        with open(cache_dir / "state_log.jsonl", "w") as f:
            for entry in timeline:
                state = entry.get("state")
                if state:
                    f.write(json.dumps({
                        "timestamp": entry.get("timestamp"),
                        "frame": entry.get("frame"),
                        **state,
                    }) + "\n")

    # 5. Download frames — sample evenly, max 30 frames to keep it fast
    all_frames = rec_data.get("frames", [])
    if all_frames:
        if len(all_frames) > 30:
            step = len(all_frames) / 30
            sampled = [all_frames[int(i * step)] for i in range(30)]
        else:
            sampled = all_frames

        async def _download_frame(fname: str):
            url = f"{AGENT_SERVER}/code/recordings/{execution_id}/frames/{fname}"
            try:
                data = await asyncio.to_thread(
                    lambda u=url: urllib.request.urlopen(u, timeout=10).read()
                )
                (cache_dir / fname).write_bytes(data)
            except Exception:
                pass  # non-fatal, evaluator can work with fewer frames

        # Download frames concurrently (bounded)
        sem = asyncio.Semaphore(5)
        async def _bounded_download(fname: str):
            async with sem:
                await _download_frame(fname)

        await asyncio.gather(*[_bounded_download(f) for f in sampled])

    frame_count = len([f for f in cache_dir.iterdir() if f.suffix == ".jpg"])
    print(f"[EVAL] {skill}: cached recording {execution_id} ({frame_count} frames, {len(timeline)} state samples)")
    return cache_dir


async def run_evaluator(skill: str, execution_id: str | None = None) -> dict:
    """Run a short-lived evaluator agent (ClaudeSDKClient) that reviews execution recordings.

    Returns {"passed": bool, "feedback": str}.
    """
    # Find execution recording — try local first, then fetch from remote agent server
    exec_dir = PROJECT_DIR / "logs" / "code_executions"

    latest = None

    # Try local recordings
    if exec_dir.exists():
        if execution_id:
            target = exec_dir / execution_id
            if target.exists() and target.is_dir():
                latest = target
        if latest is None:
            all_dirs = [d for d in exec_dir.iterdir() if d.is_dir()]
            if all_dirs:
                latest = max(all_dirs, key=lambda d: d.stat().st_mtime)

    # Fall back to fetching from agent server (works for both local and remote)
    if latest is None:
        try:
            if execution_id:
                # Fetch specific execution
                latest = await _fetch_remote_recording(execution_id, skill)
            else:
                # Find the most recent execution from the remote server
                import urllib.request
                rec_url = f"{AGENT_SERVER}/code/recordings"
                rec_data = json.loads(await asyncio.to_thread(
                    lambda: urllib.request.urlopen(rec_url, timeout=10).read()
                ))
                recordings = rec_data.get("recordings", rec_data) if isinstance(rec_data, dict) else rec_data
                if recordings:
                    latest = await _fetch_remote_recording(recordings[0], skill)
        except Exception as e:
            print(f"[EVAL] {skill}: failed to fetch remote recordings: {e}")

    if latest is None:
        print(f"[EVAL] {skill}: no execution recordings found (local or remote)")
        return {"passed": False, "feedback": "No execution recordings found — cannot evaluate."}

    print(f"[EVAL] {skill}: reviewing execution {latest.name}")
    await ws_broadcast_agent_msg(skill, f"Evaluating execution {latest.name}...", "evaluator")

    # Build evaluator prompt
    entry = _find_entry(skill)
    skill_desc = entry.get("description", skill) if entry else skill
    skill_code_path = SKILLS_DIR / skill / "scripts" / "main.py"

    skill_dir = SKILLS_DIR / skill
    system_prompt = SYSTEM_PROMPT_EVALUATOR.replace(
        "{{skill_name}}", skill
    ).replace(
        "{{skill_description}}", skill_desc
    ).replace(
        "{{exec_dir}}", str(latest)
    ).replace(
        "{{skill_code_path}}", str(skill_code_path)
    ).replace(
        "{{skill_dir}}", str(skill_dir)
    )

    prompt = (
        f"Evaluate the execution recording for skill '{skill}'.\n"
        f"Recording dir: {latest}\n"
        f"Skill code: {skill_code_path}\n\n"
        f"Read the images and metadata, then output your EVAL_RESULT JSON."
    )

    # Run a short-lived SDK agent
    options = ClaudeAgentOptions(
        cwd=str(WORKSPACE_DIR),  # claude-code/, same reason as dev agent
        permission_mode="bypassPermissions",
        system_prompt=system_prompt,
        model="claude-opus-4-6",
    )

    collected_text: list[str] = []
    EVAL_TIMEOUT = 900  # 15 minutes max for evaluator

    try:
        async def _run_eval_client():
            nonlocal collected_text
            client = ClaudeSDKClient(options=options)
            async with client:
                await client.query(prompt)
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock) and block.text.strip():
                                text = block.text.strip()
                                collected_text.append(text)
                                await ws_broadcast_agent_msg(skill, text, "evaluator")
                    elif isinstance(message, ResultMessage):
                        cost = f"${message.total_cost_usd:.4f}" if message.total_cost_usd else "?"
                        await ws_broadcast_agent_msg(skill, f"Eval done — {message.num_turns} turns, {cost}", "evaluator")
                        if message.session_id:
                            import datetime
                            eval_entry = {
                                "session_id": message.session_id,
                                "skill": skill,
                                "agent_type": "evaluator",
                                "agent_id": f"eval-{skill}",
                                "cost_usd": message.total_cost_usd,
                                "num_turns": message.num_turns,
                                "log": collected_text[-10:],
                                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            }
                            with open(SESSION_LOG, "a") as f:
                                f.write(json.dumps(eval_entry) + "\n")
                            _invalidate_session_log_cache()

        await asyncio.wait_for(_run_eval_client(), timeout=EVAL_TIMEOUT)

        # Parse EVAL_RESULT from collected text
        full_text = "\n".join(collected_text)
        match = re.search(r'EVAL_RESULT:\s*(\{.*?\})', full_text)
        if match:
            try:
                result = json.loads(match.group(1))
                return {"passed": result.get("passed", True), "feedback": result.get("feedback", ""), "full_text": full_text}
            except json.JSONDecodeError:
                pass

        # Fallback: try to find any JSON with "passed" key
        match = re.search(r'\{"passed":\s*(true|false)[^}]*\}', full_text, re.IGNORECASE)
        if match:
            try:
                result = json.loads(match.group())
                return {"passed": result.get("passed", True), "feedback": result.get("feedback", full_text[-200:]), "full_text": full_text}
            except json.JSONDecodeError:
                pass

        # Can't parse — assume pass
        print(f"[EVAL] {skill}: could not parse evaluator output, assuming pass")
        return {"passed": True, "feedback": full_text[-200:] if full_text else "No output", "full_text": full_text}

    except asyncio.TimeoutError:
        print(f"[EVAL] {skill}: evaluator timed out after {EVAL_TIMEOUT}s")
        await ws_broadcast_agent_msg(skill, f"Evaluator timed out after {EVAL_TIMEOUT // 60} minutes.", "evaluator")
        return {"passed": False, "feedback": "Evaluator timed out — CLI process may have died."}
    except Exception as e:
        print(f"[EVAL] {skill}: evaluator error: {e}")
        traceback.print_exc()
        return {"passed": True, "feedback": f"Evaluator error: {e}"}


async def _handle_agent_done(state: AgentState):
    """Handle agent completion — chain to next step in pipeline."""
    if state.agent_type != "dev":
        await ws_broadcast_status(state.skill, state.agent_id, "done", "Finished")
        return

    # Skip if this dev agent was re-spawned inside the test loop (loop manages flow)
    if state.skill in _skills_in_test_loop:
        return

    # Run evaluator on the latest execution
    await ws_broadcast_agent_msg(state.skill, "Dev complete — running evaluator...", "evaluator")
    _update_entry(state.skill, {"status": "evaluating"})
    await broadcast_full_sync()

    eval_result = await run_evaluator(state.skill)

    # Add evaluator feedback to in-memory log (persisted via agent_sessions.jsonl on session end)
    eval_feedback = eval_result.get("feedback", "")
    eval_passed = eval_result.get("passed", True)
    eval_full = eval_result.get("full_text", eval_feedback)
    eval_summary = f"{'PASSED' if eval_passed else 'FAILED'}: {eval_feedback}"
    if eval_full and eval_full != eval_feedback:
        state.log.append({"text": eval_full, "role": "evaluator"})
    else:
        state.log.append({"text": eval_summary, "role": "evaluator"})

    if not eval_result["passed"]:
        # Track retry count
        _eval_attempt_count[state.skill] = _eval_attempt_count.get(state.skill, 0) + 1
        attempts = _eval_attempt_count[state.skill]

        feedback = eval_result.get("feedback", "Evaluator found issues.")
        await ws_broadcast_agent_msg(state.skill, f"Evaluator ({attempts}/{MAX_EVAL_RETRIES}): {feedback}", "evaluator")

        if attempts >= MAX_EVAL_RETRIES:
            # Exhausted retries — send to review anyway
            await ws_broadcast_agent_msg(state.skill, f"Evaluator failed {attempts} times — sending to review.", "evaluator")
            _eval_attempt_count.pop(state.skill, None)
            _update_entry(state.skill, {"status": "review"})
            await broadcast_full_sync()
            return

        _update_entry(state.skill, {"status": "writing"})
        await broadcast_full_sync()

        # Inject feedback into the dev agent to continue working
        if state.client and state.status in ("done", "paused"):
            state.status = "running"
            await state.client.query(
                f"## Evaluator Feedback\n\n"
                f"The evaluator reviewed your execution and found issues:\n\n"
                f"{feedback}\n\n"
                f"Fix the code and resubmit."
            )
            state.task = asyncio.create_task(_eval_retry_response(state))
        else:
            # Dev agent is gone — send to review so skill doesn't get stuck
            await ws_broadcast_agent_msg(state.skill, "Dev agent unavailable for retry — sending to review.", "evaluator")
            _eval_attempt_count.pop(state.skill, None)
            _update_entry(state.skill, {"status": "review"})
            await broadcast_full_sync()
        return

    # Evaluator passed — clear retry counter
    _eval_attempt_count.pop(state.skill, None)
    await ws_broadcast_agent_msg(state.skill, "Evaluator: PASSED", "evaluator")

    # Root skill with task_env: run ground-truth mechanical test
    if _is_task_root(state.skill):
        await ws_broadcast_status(state.skill, state.agent_id, "running", "Evaluator passed — running ground-truth test")
        await ws_broadcast_agent_msg(state.skill, "Running mechanical test.", state.agent_type)
        state.exit_event.set()  # release dev agent's client context
        asyncio.create_task(_root_skill_test_loop(state.skill))
        return

    if autonomous_mode:
        await ws_broadcast_status(state.skill, state.agent_id, "done", "Evaluator passed — auto-promoted (autonomous)")
        await _confirm_skill_done(state.skill)
    else:
        await ws_broadcast_status(state.skill, state.agent_id, "done", "Evaluator passed — waiting for review")
        _update_entry(state.skill, {"status": "review"})
        await broadcast_full_sync()


async def _eval_retry_response(state: AgentState):
    """Consume the dev agent's response after evaluator feedback, then re-evaluate."""
    await _consume_sdk_response(state, state.client)
    await _resolve_completion(state)


async def _confirm_skill_done(skill: str):
    """Confirm a skill as done (after review) and auto-spawn downstream skills."""
    entry = _find_entry(skill)
    if not entry:
        return
    _update_entry(skill, {"status": "done"})
    # Release any paused agent for this skill
    for aid, a in list(agents.items()):
        if a.skill == skill:
            a.exit_event.set()
    print(f"[ORCH] Skill '{skill}' confirmed done — checking downstream skills")
    await broadcast_full_sync()
    await _auto_spawn_ready_skills()


async def _auto_spawn_ready_skills() -> list[str]:
    """Find skills whose dependencies are all 'done' and spawn dev pipelines.

    When multiple targets are configured, spawns one dev agent per target for
    each ready skill (parallel multi-target development).
    Returns list of skill names that were spawned."""
    async with _spawn_lock:
        done_skills = {e["name"] for e in skill_entries if e.get("status") == "done"}
        # Skills already being worked on — track per (skill, target_name) pair
        active_pairs = {(a.skill, a.target_name) for a in agents.values()
                        if a.status in ("starting", "running")}

        spawned = []
        for entry in skill_entries:
            name = entry["name"]
            status = entry.get("status", "planned")
            deps = entry.get("dependencies", [])

            # Only auto-spawn skills that are "planned" or "failed" (retriable)
            if status not in ("planned", "failed"):
                continue
            # Check all dependencies are done
            if deps and not all(d in done_skills for d in deps):
                continue

            # Build dev prompt with lessons if available
            desc = entry.get("description", name)
            prompt = f"Implement the '{name}' skill: {desc}"
            lessons_file = SKILLS_DIR / name / "LESSONS.md"
            if lessons_file.exists():
                lessons = lessons_file.read_text().strip()
                prompt += f"\n\n## Previous Debugging Lessons (READ CAREFULLY)\n{lessons}"
                print(f"[ORCH] {name}: attached LESSONS.md ({len(lessons)} chars)")

            # Spawn one agent per target (parallel multi-target development)
            spawned_any = False
            for t in targets:
                if (name, t["name"]) in active_pairs:
                    continue  # already has an agent on this target

                print(f"[ORCH] Auto-spawning dev for '{name}' on target '{t['name']}' ({t['agent_server']})")
                await spawn_agent(name, prompt, agent_type="dev", target=t)
                spawned_any = True

            if spawned_any:
                _update_entry(name, {"status": "writing"})
                spawned.append(name)

        if spawned:
            await ws_broadcast({
                "type": "auto_spawn",
                "skills": spawned,
                "message": f"Auto-started {len(spawned)} skill(s) on {len(targets)} target(s): {', '.join(spawned)}",
            })

        return spawned


SDK_IDLE_TIMEOUT_S = 900  # max wait between messages from a Claude SDK client (15 min, aligned with EVAL_TIMEOUT)


async def _consume_sdk_response(state: AgentState, client: ClaudeSDKClient):
    """Consume the async iterator from a ClaudeSDKClient and broadcast to dashboard.

    Wraps each message wait with SDK_IDLE_TIMEOUT_S to detect SSE long-poll deadlocks.
    On timeout, marks the agent as errored and breaks out, instead of hanging forever.
    """
    response_iter = client.receive_response().__aiter__()
    while True:
        try:
            message = await asyncio.wait_for(response_iter.__anext__(), timeout=SDK_IDLE_TIMEOUT_S)
        except asyncio.TimeoutError:
            err = f"SDK idle timeout ({SDK_IDLE_TIMEOUT_S}s) — no message from Claude SDK"
            print(f"[SDK] {state.skill}: {err}")
            state.status = "error"
            state.log.append({"text": err, "role": "agent"})
            await ws_broadcast_status(state.skill, state.agent_id, "error", err)
            await ws_broadcast_agent_msg(state.skill, err, state.agent_type)
            return
        except StopAsyncIteration:
            return

        if state.status == "stopped":
            break

        if isinstance(message, SystemMessage):
            subtype = message.subtype
            data = message.data

            # Only surface important system messages, skip noisy ones
            if subtype == "init":
                # Just log model confirmation, don't broadcast
                model = data.get("model", "?")
                print(f"[SDK] {state.skill}: init (model={model})")
            elif subtype == "task_started":
                desc = data.get("description", "")
                print(f"[SDK] {state.skill}: agent started: {desc}")
                await ws_broadcast_status(state.skill, state.agent_id, "running", desc)
            elif subtype == "task_progress":
                desc = data.get("description", "")
                tool = data.get("last_tool_name", "")
                usage = data.get("usage", {})
                turns = usage.get("tool_uses", 0)
                # Update status line but don't spam the log
                await ws_broadcast_status(state.skill, state.agent_id, "running", f"{desc}")
            elif subtype in ("error", "api_error", "auth_error"):
                text = data.get("message") or data.get("text") or str(data)
                print(f"[SDK] {state.skill}: ERROR: {text}")
                state.log.append({"text": f"ERROR: {text}", "role": "agent"})
                await ws_broadcast_agent_msg(state.skill, f"ERROR: {text}", state.agent_type)
                state.status = "error"
                await ws_broadcast_status(state.skill, state.agent_id, "error", text[:200])
            else:
                # Log unknown subtypes briefly for debugging
                text = data.get("message") or data.get("description") or subtype
                print(f"[SDK] {state.skill}: {subtype}: {text}")

        elif isinstance(message, AssistantMessage):
            # Persist session_id early (don't wait for ResultMessage)
            if hasattr(message, 'session_id') and message.session_id and not state.session_id:
                state.session_id = message.session_id
                _update_entry(state.skill, {"session_id": message.session_id})
                print(f"[SDK] {state.skill}: session_id captured early: {message.session_id}")
            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    text = block.text.strip()
                    state.log.append({"text": text, "role": "agent"})
                    if len(state.log) > 200:
                        state.log[:] = state.log[-200:]
                    await ws_broadcast_agent_msg(state.skill, text, state.agent_type)

                elif isinstance(block, ToolUseBlock):
                    tool_msg = f"Using {block.name}..."
                    await ws_broadcast_status(
                        state.skill, state.agent_id, "running", tool_msg
                    )

        elif isinstance(message, ResultMessage):
            state.session_id = message.session_id
            # Persist session_id to graph.json so it survives orchestrator restarts
            if message.session_id:
                _update_entry(state.skill, {"session_id": message.session_id})
                _save_session_mapping(state, message)
            cost = f"${message.total_cost_usd:.4f}" if message.total_cost_usd else "?"
            # Detect zero-cost / zero-turn completions as errors (e.g. credit/auth failures)
            if not message.total_cost_usd and message.num_turns == 0:
                err_text = " | ".join(m["text"] if isinstance(m, dict) else m for m in state.log) if state.log else "Agent produced no output"
                print(f"[SDK] {state.skill}: ERROR (zero-cost completion): {err_text}")
                state.status = "error"
                await ws_broadcast_status(state.skill, state.agent_id, "error", err_text[:200])
                await ws_broadcast_agent_msg(state.skill, f"ERROR: {err_text}", state.agent_type)
            else:
                done_msg = f"Done — {message.num_turns} turns, {cost}"
                state.log.append({"text": done_msg, "role": "agent"})
                await ws_broadcast_agent_msg(state.skill, done_msg, state.agent_type)


async def inject_hint(agent_id: str, text: str):
    """Send a follow-up message to a running or paused agent."""
    state = agents.get(agent_id)
    # If agent_id is empty or not found, find the first running/paused agent
    if not state:
        for aid, a in agents.items():
            if a.status in ("running", "paused", "starting"):
                state = a
                agent_id = aid
                print(f"[ORCH] inject: resolved empty agent_id to {aid} (skill={a.skill})")
                break
    if not state:
        print(f"[ORCH] inject: no running agent found (tried id='{agent_id}')")
        return

    # Broadcast user message to dashboard chat log
    await ws_broadcast_agent_msg(state.skill, text, "user")
    state.log.append({"text": text, "role": "user"})

    if HAS_SDK and state.client and state.status in ("running", "paused"):
        # SDK mode: interrupt current work, send follow-up in same session
        try:
            if state.status == "running":
                await state.client.interrupt()
                await asyncio.sleep(0.5)
            state.status = "running"
            await state.client.query(text)

            async def _hint_response():
                await _consume_sdk_response(state, state.client)
                await _resolve_completion(state)

            state.task = asyncio.create_task(_hint_response())
            await ws_broadcast_status(state.skill, state.agent_id, "writing", "Resumed")
        except Exception as e:
            print(f"[ORCH] inject SDK error: {e}")
    else:
        print(f"[ORCH] inject: agent {agent_id} not running/paused or no SDK client")


async def stop_agent(agent_id: str):
    """Pause a running agent — interrupt current work but keep session alive.

    The agent stays in memory with full conversation history. It can be
    resumed later via inject_hint() or re-kicked by xbot-start.
    Only fully killed when the skill is confirmed "done" on the dashboard.
    """
    state = agents.get(agent_id)
    if not state:
        return

    # Mark paused BEFORE interrupt to prevent race with _run_sdk_agent finally block
    # (which checks state.status == "running" to decide whether to trigger tests)
    state.status = "paused"

    # SDK mode: interrupt current work, keep session alive for later hints
    if HAS_SDK and state.client:
        try:
            await state.client.interrupt()
        except Exception as e:
            print(f"[ORCH] stop/pause SDK error: {e}")
    state.log.append({"text": "Paused by user", "role": "agent"})

    if state.agent_type == "test":
        await ws_broadcast_status(state.skill, state.agent_id, "done", "Test paused")
        await ws_broadcast_agent_msg(state.skill, "Test paused by user", state.agent_type)
    else:
        await ws_broadcast_status(state.skill, state.agent_id, "paused", "Paused by user")
        await ws_broadcast_agent_msg(state.skill, "Paused by user — send a hint to resume", state.agent_type)


async def kill_agent(agent_id: str):
    """Fully terminate a running agent — session is destroyed."""
    state = agents.get(agent_id)
    if not state:
        return

    state.status = "stopped"
    state.exit_event.set()

    if HAS_SDK and state.client:
        try:
            await state.client.interrupt()
        except Exception:
            pass

    # Cancel the async task
    if state.task and not state.task.done():
        state.task.cancel()
        try:
            await state.task
        except asyncio.CancelledError:
            pass

    # CLI fallback: kill subprocess
    if state.proc and state.proc.returncode is None:
        import signal
        state.proc.send_signal(signal.SIGINT)

    state.log.append({"text": "Killed by user", "role": "agent"})

    # Clean up skill state
    _eval_attempt_count.pop(state.skill, None)
    _skills_in_test_loop.discard(state.skill)

    entry = _find_entry(state.skill)
    if entry and entry.get("status") not in ("done", "confirmed_done"):
        _update_entry(state.skill, {"status": "failed", "agent_id": None})
    else:
        _update_entry(state.skill, {"agent_id": None})

    await ws_broadcast_status(state.skill, state.agent_id, "stopped", "Stopped")
    await ws_broadcast_agent_msg(state.skill, "Agent killed", state.agent_type)
    agents.pop(agent_id, None)
    await broadcast_full_sync()


# ---------------------------------------------------------------------------
# Agent lifecycle — CLI fallback (no SDK)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# HTTP API (optional — for spawning agents from scripts/curl)
# ---------------------------------------------------------------------------

async def handle_http(reader, writer):
    """Minimal HTTP endpoint for spawning/controlling agents from scripts.

    POST /spawn       {"skill": "...", "prompt": "..."}
    POST /stop        {"agent_id": "..."}
    POST /inject      {"agent_id": "...", "text": "..."}
    POST /xbot-start  Trigger auto-spawn of all skills with satisfied dependencies
    GET  /status      -> all agents
    """
    data = await reader.read(8192)
    request = data.decode()
    lines = request.split("\r\n")
    method, path, _ = lines[0].split(" ", 2)

    # Extract body
    body = ""
    if "\r\n\r\n" in request:
        body = request.split("\r\n\r\n", 1)[1]

    global dev_mode

    response_body = ""
    status = "200 OK"
    content_type = "application/json"

    try:
        if method == "POST" and path == "/xbot-start":
            dev_mode = True
            print("[ORCH] Dev mode enabled — agents can now spawn")
            spawned = await _auto_spawn_ready_skills()
            response_body = json.dumps({
                "ok": True,
                "spawned": spawned if spawned else [],
                "message": f"Started {len(spawned)} skill(s)" if spawned else "No skills ready (all deps not met or already in progress)",
            })

        elif method == "POST" and path == "/spawn":
            params = json.loads(body)
            aid = await spawn_agent(params["skill"], params["prompt"])
            response_body = json.dumps({"agent_id": aid})

        elif method == "POST" and path == "/stop":
            params = json.loads(body)
            await stop_agent(params["agent_id"])
            response_body = json.dumps({"ok": True})

        elif method == "POST" and path == "/kill":
            params = json.loads(body)
            await kill_agent(params["agent_id"])
            response_body = json.dumps({"ok": True})

        elif method == "POST" and path == "/inject":
            params = json.loads(body)
            await inject_hint(params["agent_id"], params["text"])
            response_body = json.dumps({"ok": True})

        elif method == "GET" and path == "/status":
            response_body = json.dumps({
                aid: {
                    "skill": a.skill,
                    "target": a.target_name,
                    "status": a.status,
                    "session_id": a.session_id,
                    "log_tail": a.log[-10:],
                }
                for aid, a in agents.items()
            })

        elif method == "GET" and path == "/entries":
            response_body = json.dumps(skill_entries)

        elif method == "POST" and path == "/entries":
            params = json.loads(body)
            entry = _add_entry(params["name"], params.get("description", ""), params.get("dependencies", []))
            await broadcast_full_sync()
            response_body = json.dumps(entry)

        elif method == "DELETE" and path.startswith("/entries/"):
            name = path.split("/entries/", 1)[1]
            removed = _remove_entry(name)
            await broadcast_full_sync()
            response_body = json.dumps({"ok": removed})

        elif method == "PATCH" and path.startswith("/entries/"):
            name = path.split("/entries/", 1)[1]
            params = json.loads(body)
            entry = _update_entry(name, params)
            await broadcast_full_sync()
            # If status was set to "done", check for newly unblocked skills
            if params.get("status") == "done":
                await _auto_spawn_ready_skills()
            response_body = json.dumps(entry or {"error": "not found"})

        elif method == "POST" and path == "/job-done":
            params = json.loads(body)
            skill = params["skill"]
            execution_id = params.get("execution_id", "")
            job_agent_server = params.get("agent_server", "")
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            _submission_evals[skill] = {"future": fut, "execution_id": execution_id}
            asyncio.create_task(_run_submission_eval(skill, execution_id, job_agent_server=job_agent_server))
            response_body = json.dumps({"ok": True, "message": f"Evaluator spawned for {skill}"})

        elif method == "GET" and path.startswith("/sessions/"):
            # Raw JSONL of a graph's persisted session log — used by
            # sessions.html dashboard page to render full agent history.
            # Returns 404 if graph doesn't exist; 200 with empty body if
            # agent_sessions.jsonl doesn't exist yet (graph created but no runs).
            graph_name = path.split("/sessions/", 1)[1].split("?")[0].strip("/")
            log_path = WORKSPACE_DIR / "graphs" / graph_name / "agent_sessions.jsonl"
            if not (WORKSPACE_DIR / "graphs" / graph_name).is_dir():
                status = "404 Not Found"
                response_body = json.dumps({"error": f"graph {graph_name!r} not found"})
            else:
                content_type = "application/x-ndjson; charset=utf-8"
                response_body = log_path.read_text() if log_path.exists() else ""

        elif method == "GET" and path.startswith("/eval-result/"):
            skill = path.split("/eval-result/", 1)[1]
            entry = _submission_evals.get(skill)
            if not entry:
                response_body = json.dumps({"status": "not_found"})
            elif not entry["future"].done():
                response_body = json.dumps({"status": "pending"})
            else:
                result = entry["future"].result()
                _submission_evals.pop(skill, None)
                # Return full evaluator text as feedback — this is the dev agent's only input
                feedback = result.get("full_text") or result.get("feedback", "")
                response_body = json.dumps({
                    "status": "complete",
                    "passed": result.get("passed", True),
                    "feedback": feedback,
                })

        else:
            status = "404 Not Found"
            response_body = json.dumps({"error": "not found"})

    except Exception as e:
        status = "500 Internal Server Error"
        response_body = json.dumps({"error": str(e)})

    # Use UTF-8 byte length (not char length) so multi-byte content
    # (e.g. Chinese text in session logs) doesn't get truncated.
    body_bytes = response_body.encode("utf-8")
    http_response = (
        f"HTTP/1.1 {status}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Access-Control-Allow-Origin: *\r\n"
        f"Content-Length: {len(body_bytes)}\r\n"
        f"\r\n"
    ).encode("utf-8") + body_bytes
    writer.write(http_response)
    await writer.drain()
    writer.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    _load_entries()
    print(f"[ORCH] Claude Agent Orchestrator")
    print(f"[ORCH] SDK mode: {HAS_SDK}")
    print(f"[ORCH] Project dir: {PROJECT_DIR}")
    print(f"[ORCH] Entries loaded: {len(skill_entries)}")
    print(f"[ORCH] WebSocket: ws://0.0.0.0:{WS_PORT}")
    print(f"[ORCH] HTTP API:  http://0.0.0.0:{WS_PORT + 1}")
    print()
    print(f"[ORCH] Spawn agents via:")
    print(f"  Browser:  open {AGENT_SERVER}/local/ and click 'Edit Skill'")
    print(f"  curl:     curl -X POST localhost:{WS_PORT + 1}/spawn \\")
    print(f"              -d '{{\"skill\":\"my-skill\",\"prompt\":\"Implement ...\"}}'")
    print()

    # Start WebSocket server
    ws_server = await websockets.serve(ws_handler, "0.0.0.0", WS_PORT)

    # Start HTTP API server
    http_server = await asyncio.start_server(handle_http, "0.0.0.0", WS_PORT + 1)

    await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
