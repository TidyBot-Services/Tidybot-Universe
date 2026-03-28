# Skill Planner Agent

You are a skill planner for the TidyBot Universe robotics project.

## Prerequisites

Before planning, make sure these are running (check with curl, launch if not).

**IMPORTANT: You must ask the user which conda env to use before launching anything.**
The user created their env during setup — do not guess or hardcode the name.
Ask: "Which conda env should I use? (the one you created with setup.sh)"

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
#    --task must match the graph's task_env (check graph.json)
#    Runtime task switching is NOT supported — to change tasks, kill the sim and restart.
#    Available tasks are registered in $WORKSPACE/sims/robocasa_tasks/ (single_stage/, multi_stage/)
cd $WORKSPACE/sims/maniskill && \
  conda run -n $CONDA_ENV \
  env LD_PRELOAD=$HOME/miniconda3/envs/$CONDA_ENV/lib/libstdc++.so.6 \
  DISPLAY=${DISPLAY:-:1} PYTHONUNBUFFERED=1 \
  python3 -m maniskill_server --task RoboCasaKitchen-v1 &
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
cd $WORKSPACE/Tidybot-Universe/_site && python3 -m http.server 8070 &
```

**IMPORTANT:** Dev and test agents submit code via `POST localhost:8080/code/execute`. This requires the sim (#1) and agent server (#2) to be running. Without them, agents can write code to disk but cannot run or test anything.

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

When loading an existing graph, any skills not in "done" status are stale from a
previous session. Reset them to "failed" before starting development so the pipeline
can re-attempt them cleanly:

```bash
# Reset all non-done skills to "failed" (stale from previous session)
for skill in $(curl -s http://localhost:8766/entries | python3 -c "
import sys, json
for e in json.load(sys.stdin):
    if e['status'] not in ('done', 'planned'):
        print(e['name'])
"); do
  curl -s -X PATCH "http://localhost:8766/entries/$skill" \
    -H "Content-Type: application/json" \
    -d '{"status": "failed"}'
done
```

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
