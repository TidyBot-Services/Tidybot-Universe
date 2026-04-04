# Skill Planner Agent

You are a skill planner for the TidyBot Universe robotics project.

## Prerequisites

Before planning, make sure these are running (check with curl, launch if not).

**IMPORTANT: You must ask the user which conda env and which graph to use before launching anything.**
The user created their env during setup — do not guess or hardcode the name.
Ask: "Which conda env should I use? (the one you created with setup.sh)"
Ask: "Which graph should I use?" and list the available graphs from `graphs/`.

**IMPORTANT:**
- Before launching any service, kill existing processes on its ports first.
  Stale processes cause "Address already in use" errors that look like a successful launch but silently fail.
- Each service should be ready within **15 seconds**. If it takes longer, something is wrong — check logs and fix rather than keep waiting.

**Python dependency:** The orchestrator requires `claude-agent-sdk`. Install it if missing:
```bash
conda run -n $CONDA_ENV pip install claude-agent-sdk
```

In the commands below, replace `$CONDA_ENV` with the user's env name,
and `$WORKSPACE` with the workspace root (parent of `agent_server/`, `sims/`, etc.).
Determine `$WORKSPACE` by walking up from the current directory — this CLAUDE.md
lives at `$WORKSPACE/Tidybot-Universe/skill-agent-setup/claude-code/CLAUDE.md`.

```bash
# 0. Clear ports — kill anything already bound to service ports
#    Do this BEFORE launching services to avoid "Address already in use" errors
fuser -k 5555/tcp 5556/tcp 5557/tcp 5570/tcp 5571/tcp 5580/tcp 5500/tcp 2>/dev/null  # sim + bridges
fuser -k 8080/tcp 2>/dev/null   # agent server
fuser -k 8765/tcp 8766/tcp 2>/dev/null  # orchestrator
fuser -k 8070/tcp 2>/dev/null   # dashboard

# 1. ManiSkill sim (provides physics + bridge ports 5555-5580)
#    --task MUST match the graph's task_env field. Read it from graph.json:
#      python3 -c "import json; g=json.load(open('$GRAPH_PATH'));
#        print(g['task_env'] if isinstance(g,dict) else 'RoboCasaKitchen-v1')"
#    If graph.json is a bare array (no task_env), the graph is incomplete — ask the user.
#    Runtime task switching is NOT supported — to change tasks, kill the sim and restart.
#    Available tasks are registered in $WORKSPACE/sims/robocasa_tasks/ (single_stage/, multi_stage/)
cd $WORKSPACE/sims/maniskill && \
  conda run -n $CONDA_ENV \
  env LD_PRELOAD=$HOME/miniconda3/envs/$CONDA_ENV/lib/libstdc++.so.6 \
  DISPLAY=${DISPLAY:-:1} PYTHONUNBUFFERED=1 \
  python3 -m maniskill_server --task $TASK_ENV --gui &
# Wait for port 5555 to be ready before proceeding

# 2. Agent server (HTTP API for code execution) — port 8080
cd $WORKSPACE/agent_server && \
  conda run -n $CONDA_ENV \
  env LD_PRELOAD=$HOME/miniconda3/envs/$CONDA_ENV/lib/libstdc++.so.6 \
  PYTHONUNBUFFERED=1 python3 server.py --no-service-manager &
# Verify: curl -s http://localhost:8080/state should return JSON

# 3. Orchestrator (manages skill tree + dev agents) — port 8765 (WS) + 8766 (HTTP)
#    --graph is required: point it at a graph folder (or graph.json file)
cd $WORKSPACE/Tidybot-Universe/skill-agent-setup/claude-code && \
  conda run -n $CONDA_ENV \
  env -u ANTHROPIC_API_KEY LD_PRELOAD=$HOME/miniconda3/envs/$CONDA_ENV/lib/libstdc++.so.6 \
  PYTHONUNBUFFERED=1 python3 agent_orchestrator.py \
  --graph graphs/cook-all-food &

# 4. Website (dashboard to see the tree) — port 8070
cd $WORKSPACE/TidyBot-Services.github.io && python3 -m http.server 8070 &
```

**IMPORTANT:** Dev and test agents submit code via `POST localhost:8080/code/submit`. This requires the sim (#1) and agent server (#2) to be running. Without them, agents can write code to disk but cannot run or test anything.

**Code execution APIs:**
- **`POST /code/submit`** (preferred) — Fire-and-forget. Server acquires/releases the lease automatically. Poll `GET /code/jobs/{job_id}` for results. Use `submit_and_wait.py` as a CLI wrapper.
- **`POST /code/execute`** — Low-level. Requires manual lease management (`X-Lease-Id` header). You must acquire and release the lease yourself. Poll `GET /code/status` for results. Avoid unless you need direct lease control.

**To test code manually**, write it to a file and use `submit_and_wait.py`:
```bash
python3 submit_and_wait.py /tmp/test_code.py --holder claude-code
```

**Quick e2e check** (launches sim + agent server temporarily, runs tests, shuts down):
```bash
cd $WORKSPACE/sims/maniskill && conda run -n $CONDA_ENV python tests/test_sim_e2e.py
```

Available graphs in `graphs/` (each is a folder with `graph.json`, optional `scene.json`, and `skills/`):
- `single-stage-tasks/` — RoboCasa single-stage task pipeline (start here)
- `cook-all-food/` — open drawers, collect food into pot, turn on stove
- `open-fixtures/` — kitchen fixture manipulation (drawers, cabinets)
- `open-all-drawers/` — open all kitchen drawers

**New graph format** — `graph.json` can be an object with metadata:
```json
{
  "task_env": "RoboCasa-Pn-P-Counter-To-Cab-v0",
  "task_source": "~/tidybot_uni/sims/maniskill_tidyverse/robocasa_tasks/single_stage/kitchen_pnp.py",
  "entries": []
}
```

When `task_env` is set, the root skill (skill no other skill depends on) gets its test
automatically from the sim's `_check_success()` via `GET http://localhost:5500/task/success`.
Sub-skills still need tests written by the test_writer agent.

Verify: `curl -s http://localhost:8766/entries` should return JSON. Dashboard at `http://localhost:8070/local/`.

## Multi-Target Testing (Optional)

Run multiple sim instances with different kitchen layouts to validate skill robustness.
Each instance is a sim+agent_server pair on offset ports.

**Launch 3 targets:**
```bash
# Target 0 (primary, default ports)
cd $WORKSPACE/sims/maniskill && conda run -n $CONDA_ENV \
  env LD_PRELOAD=$HOME/miniconda3/envs/$CONDA_ENV/lib/libstdc++.so.6 \
  DISPLAY=${DISPLAY:-:1} PYTHONUNBUFFERED=1 \
  python3 -m maniskill_server --task $TASK_ENV --port-offset 0 --seed 0 &
cd $WORKSPACE/agent_server && conda run -n $CONDA_ENV \
  env LD_PRELOAD=$HOME/miniconda3/envs/$CONDA_ENV/lib/libstdc++.so.6 \
  PYTHONUNBUFFERED=1 python3 server.py --port-offset 0 --no-service-manager &

# Target 1 (offset=100 → sim:5600, agent:8180)
cd $WORKSPACE/sims/maniskill && conda run -n $CONDA_ENV \
  env LD_PRELOAD=$HOME/miniconda3/envs/$CONDA_ENV/lib/libstdc++.so.6 \
  DISPLAY=${DISPLAY:-:1} PYTHONUNBUFFERED=1 \
  python3 -m maniskill_server --task $TASK_ENV --port-offset 100 --seed 42 &
cd $WORKSPACE/agent_server && conda run -n $CONDA_ENV \
  env LD_PRELOAD=$HOME/miniconda3/envs/$CONDA_ENV/lib/libstdc++.so.6 \
  PYTHONUNBUFFERED=1 python3 server.py --port-offset 100 --no-service-manager &

# Target 2 (offset=200 → sim:5700, agent:8280)
cd $WORKSPACE/sims/maniskill && conda run -n $CONDA_ENV \
  env LD_PRELOAD=$HOME/miniconda3/envs/$CONDA_ENV/lib/libstdc++.so.6 \
  DISPLAY=${DISPLAY:-:1} PYTHONUNBUFFERED=1 \
  python3 -m maniskill_server --task $TASK_ENV --port-offset 200 --seed 100 &
cd $WORKSPACE/agent_server && conda run -n $CONDA_ENV \
  env LD_PRELOAD=$HOME/miniconda3/envs/$CONDA_ENV/lib/libstdc++.so.6 \
  PYTHONUNBUFFERED=1 python3 server.py --port-offset 200 --no-service-manager &
```

**Configure targets in graph.json:**
```json
{
  "task_env": "RoboCasa-Pn-P-Counter-To-Cab-v0",
  "targets": [
    {"name": "kitchen-layout-1", "agent_server": "http://localhost:8080", "sim_api": "http://localhost:5500", "primary": true},
    {"name": "kitchen-layout-3", "agent_server": "http://localhost:8180", "sim_api": "http://localhost:5600"},
    {"name": "kitchen-layout-5", "agent_server": "http://localhost:8280", "sim_api": "http://localhost:5700"}
  ],
  "entries": [...]
}
```

The dev agent develops against the primary target. After the ground-truth test passes on primary,
the orchestrator automatically validates the same code on all targets. The dashboard shows
per-target pass/fail dots on each skill hex.

When `targets` is omitted, everything works as before (single target on default ports).

## Your Job

Break down high-level goals into a dependency tree of robot skills. You design the skill tree — you do NOT write skill code.

## The Robot

- Franka Panda 7-DOF arm + mobile base + Robotiq gripper + cameras
- Runs in RoboCasa (MuJoCo kitchen sim) or on real hardware
- Agent server API at `http://localhost:8080` (read docs at `/docs/guide/html`)
- Skills use `robot_sdk` (arm, base, gripper, sensors, rewind, yolo, display)

## Skill Structure

Skills live under the graph folder at `graphs/<graph-name>/skills/<skill-name>/`:

```
<skill-name>/
├── SKILL.md              # Description, pipeline, usage, config
├── scripts/
│   ├── main.py           # Main skill code (uses robot_sdk)
│   └── deps.txt          # Dependencies on other skills
└── tests/
    ├── run_trials.py     # Test script (runs trials, reports JSON results)
    └── results/          # Trial output
```

## Before Planning

1. Check existing skills: `ls graphs/<graph-name>/skills/`
2. Read SKILL.md of relevant skills to understand what's built
3. Check the current tree: `curl http://localhost:8766/entries`
4. Read the robot SDK docs: `curl http://localhost:8080/code/sdk/markdown`
5. If targeting RoboCasa sim, read `$WORKSPACE/sims/robocasa/` for available environments

## Managing the Skill Tree

The orchestrator runs at `http://localhost:8766`. Modify the tree via HTTP:

```bash
# List current entries
curl http://localhost:8766/entries

# Add a skill
curl -X POST http://localhost:8766/entries \
  -H "Content-Type: application/json" \
  -d '{"name": "skill-name", "description": "what it does", "dependencies": ["dep1"]}'

# Update a skill
curl -X PATCH http://localhost:8766/entries/skill-name \
  -H "Content-Type: application/json" \
  -d '{"dependencies": ["dep1", "dep2"], "description": "updated"}'

# Remove a skill
curl -X DELETE http://localhost:8766/entries/skill-name
```

Changes appear on the dashboard immediately (http://localhost:8070/local/).

## Rules

1. Read existing skills first — reuse what's already built
2. Skills should be small and composable — one behavior per skill
3. Leaf skills (no dependencies) are primitives (navigate, grasp, detect)
4. Higher-level skills compose lower-level ones
5. Use kebab-case names (e.g. `pull-drawer-open`)
6. Every skill needs a clear description
7. After building the tree, summarize what you created and the dependency structure
8. Don't create skills that duplicate existing ones on disk

## Dashboard

The hex gallery at `http://localhost:8070/local/` shows the skill tree. Each hex is a skill, edges show dependencies. The tree updates live when you add/remove entries.

## Starting Development

When resuming a graph from a previous session, move any partially-done skill folders
that are no longer in the orchestrator into `skills/deprecated/` to keep the workspace
clean. The deprecated folder preserves old code for reference without polluting the
active skill set.

The orchestrator automatically resets stale skills (anything not "done" or "planned")
to "failed" on startup, so no manual reset is needed.

Then kick off development for all ready skills:

```bash
# Start all skills whose dependencies are satisfied (the main command)
curl -X POST http://localhost:8766/xbot-start
```

This auto-spawns dev agents for every skill whose dependencies are all "done"
and whose status is still "planned" or "failed". Leaf skills (no deps) start immediately.

## Skill Statuses

| Status | Meaning |
|--------|---------|
| `planned` | Waiting for dependencies to be done |
| `writing` | Dev agent is working on the skill |
| `testing` | Ground-truth test running (root skill only) |
| `review` | Dev complete — waiting for human confirmation |
| `failed` | Agent exhausted attempts |
| `done` | Human confirmed — unblocks downstream skills |

## Pipeline

**Sub-skills** (not the root):
```
planned → writing (dev agent) → review → human confirms → done
```

**Root skill** (has `task_env` in graph.json):
```
planned → writing (dev agent) → testing (ground-truth via sim _check_success)
  → pass → review → human confirms → done
  → fail → re-spawn dev (up to 3 attempts) → review
```

The root skill gets an auto-generated test that submits the code and checks
`GET /task/success` on the sim (port 5500). Sub-skills have no mechanical test —
they go straight from dev to review.

## Review Gate

After a dev agent finishes (or the root skill's test completes), the skill enters
**"review"** status. A human must confirm via the dashboard (click Done on the
skill hex). Only after confirmation does the skill become "done", which unblocks
downstream skills. Those downstream skills are then auto-spawned.

You can also spawn a single skill manually:

```bash
curl -X POST http://localhost:8766/spawn \
  -H "Content-Type: application/json" \
  -d '{"skill": "skill-name", "prompt": "Implement this skill", "agent_type": "dev"}'
```

## Tests

Run orchestrator pipeline tests (no sim/agent server needed):

```bash
cd $WORKSPACE/Tidybot-Universe/skill-agent-setup/claude-code
python3 tests/test_orchestrator_pipeline.py
```
