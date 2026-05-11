# Module — simulation

ManiSkill (SAPIEN-based) and RoboCasa simulators, exposing the same SDK / bridge ports as real hardware.

## What it does

- Renders the kitchen scene + Franka+Tidybot robot
- Implements bridge servers (ZMQ, RPC, WebSocket) on the same ports as real hardware (5500, 5555, 5556, 5570, 5580, 50000)
- Hosts an HTTP API on `:5500` for sim-only ops: `/reset`, `/task/info`, `/task/success`, `/perceive`, `/plan`, `/plan_base`, `/draw_collision_spheres`
- Plans motion via cuRobo (standalone service on `:7000`) + falls back to in-process A* for base when cuRobo's whole_body plan lands the base in a fixture

## Key code paths

| Path | Role |
|---|---|
| `sims/maniskill/maniskill_server/server.py` | Main sim server (~2000 lines). Bridges + HTTP + cuRobo integration. |
| `sims/maniskill/maniskill_server/__main__.py` | CLI entrypoint (`python -m maniskill_server --task X --gui`) |
| `sims/maniskill_tidyverse/` | (separate repo) Kitchen scene assets, robot URDF, cuRobo asset config |
| `sims/robocasa_tasks/` | (separate repo) RoboCasa v0.2 tasks (94 tasks: 86 multi_stage + 8 single_stage) ported to ManiSkill |
| `sims/maniskill_v0_8/` | Standalone cuRobo v0.8 service env (port 7000) |

## How tasks are discovered

```python
# At sim startup:
import robocasa_tasks   # canonical pkg, NOT maniskill_tidyverse.robocasa_tasks
# robocasa_tasks/__init__.py does:
#   from . import single_stage   # triggers @register_env for 8 single-stage tasks
#   from . import multi_stage    # triggers @register_env for 86 multi-stage tasks
# Then:
env = gym.make("RoboCasa-Pn-P-Counter-To-Cab-v0", ...)
```

After 2026-05-09 cleanup (see `patterns/pathfinder-subdir-shadow.md`), the canonical import is `robocasa_tasks` directly. **Do not** use `maniskill_tidyverse.robocasa_tasks` — that path is gone after the shim revert.

## cuRobo whole_body plan + base perturbation split

When cuRobo plans a whole-body trajectory, its base path doesn't include collision checks against world fixtures (cuRobo only checks arm collisions). If the resulting base goal lands inside a fixture, the sim falls back to A* base + cuRobo arm-only:

```python
# server.py:~942 (perturbation search)
shells = [(0.15, 16), (0.30, 16), (0.45, 16)]   # 48 perturbations total
for radius, n_dirs in shells:
    for i in range(n_dirs):
        a = 2 * math.pi * i / n_dirs
        offsets.append((radius * math.cos(a), radius * math.sin(a)))
```

Old version was `±0.1m × 8 dirs` which was too small to escape kitchen fixtures (sink/counter often >0.2m thick). Widened 2026-05-09 — see `progress.md` entry.

## Sim port layout

| Port | Service |
|---|---|
| 5500 | HTTP API (sim-only ops) |
| 5555 | Franka bridge ZMQ command |
| 5556 | Franka bridge ZMQ state pub |
| 5557 | Franka bridge ZMQ stream sub |
| 5570 | Gripper bridge ZMQ command |
| 5571 | Gripper bridge ZMQ state pub |
| 5580 | Camera WebSocket (JPEG) |
| 5590 | Mocap TCP |
| 50000 | Base RPC |

Multi-target mode shifts all ports by `--port-offset N` (so target 0 = 5500, target 100 = 5600, etc.).

## Sim launch

```bash
cd ~/文档/Tidybot-Universe/sims/maniskill
conda run -n maniskill --no-capture-output \
  env LD_PRELOAD=$HOME/miniconda3/envs/maniskill/lib/libstdc++.so.6 \
       DISPLAY=:0 PYTHONUNBUFFERED=1 \
       CUROBO_SERVICE_URL=http://localhost:7000 \
       GRASPGEN_SERVER_URL=http://10.102.245.84:8006 \
       python3 -m maniskill_server --task RoboCasa-Pn-P-Counter-To-Cab-v0 --gui \
       > /tmp/maniskill_server.log 2>&1 &
```

Required env vars:
- `LD_PRELOAD` — workaround for SAPIEN's libstdc++ linking
- `DISPLAY` — needed for `--gui`. If headless, drop both
- `CUROBO_SERVICE_URL` — points at standalone cuRobo v0.8 service
- `GRASPGEN_SERVER_URL` — points at remote GPU node's graspgen service

## Known sim quirks

- **Auto-reset blocker (fixed 2026-04-25)**: Sim's run loop called `env.reset()` the instant `_check_success` returned True, telling task_success endpoint False before the agent could call it. Now `env.reset()` is explicit via `POST /reset`. See `~/.claude/projects/.../memory/project_sim_autoreset_blocker.md`.
- **OBB collision viz frame** was in wrong frame for visualization (cosmetic, not blocking). Memory: `project_sim_infra_bugs.md`.
- **Vulkan ICD warning at startup** is benign — SAPIEN falls back to a stub ICD when it can't find the NVIDIA driver one. Sim still works.

## Related

- `decisions/0001-shared-hardware-sdk.md`
- `patterns/pathfinder-subdir-shadow.md` — the cross-repo import cleanup
- `modules/robot-sdk.md` — what the sim exposes back to skill code
- `~/.claude/projects/.../memory/project_curobo_v08_swap.md` — cuRobo v0.7 → v0.8 migration notes
- `~/.claude/projects/.../memory/project_curobo_migration.md` — original cuRobo integration history
