# Eval: RoboCasa Single-Stage Task Pipeline

Run the full skill planning and development pipeline for a single RoboCasa kitchen task.

## What to Do

1. **Start services** (sim, agent server, orchestrator, dashboard) — see CLAUDE.md for commands
2. **Read the task source** to understand the scene and success condition
3. **Plan the skill tree** — decompose the task into sub-skills via the orchestrator API
4. **Run `/xbot-start`** to kick off development

The orchestrator handles the rest automatically:
- Sub-skills: test_writer → dev → mechanical_test → review → done
- Root skill (the full task): dev → mechanical_test (sim's `_check_success()`) → review → done

## Step-by-Step

### 1. Start the sim with the target task

Check `graphs/single-stage-tasks/graph.json` — it has `task_env` (e.g. `RoboCasa-Pn-P-Counter-To-Cab-v0`).
Start the sim with that task:

```bash
cd ~/tidybot_uni/sims/maniskill && \
  conda run -n maniskill \
  DISPLAY=${DISPLAY:-:1} PYTHONUNBUFFERED=1 \
  python3 -m maniskill_server --task <task_env> &
```

Wait for port 5555, then start agent server and orchestrator (see CLAUDE.md).
Point orchestrator at: `--graph graphs/single-stage-tasks`

### 2. Read the task source

The `task_source` field in graph.json points to the Python file. Read it to understand:
- `_setup_kitchen_references()` — which fixtures (counter, cabinet, stove, etc.)
- `_get_obj_cfgs()` — what objects start where
- `_check_success()` — what "done" means

Also read the robot SDK docs: `curl http://localhost:8080/code/sdk/markdown`

### 3. Plan the skill tree

Add skills to the orchestrator via HTTP. Design one root skill (the full task) and
leaf/intermediate sub-skills. Example for `RoboCasa-Pn-P-Counter-To-Cab-v0`:

```bash
# Leaf skills
curl -X POST http://localhost:8766/entries \
  -H "Content-Type: application/json" \
  -d '{"name": "navigate-to-fixture", "description": "Move base to within arm reach of a target fixture"}'

curl -X POST http://localhost:8766/entries \
  -H "Content-Type: application/json" \
  -d '{"name": "grasp-from-surface", "description": "Grasp a small object from a flat surface (counter, shelf)"}'

curl -X POST http://localhost:8766/entries \
  -H "Content-Type: application/json" \
  -d '{"name": "place-in-container", "description": "Place a held object inside an open container (cabinet, drawer, sink)"}'

# Root skill — depends on all sub-skills
curl -X POST http://localhost:8766/entries \
  -H "Content-Type: application/json" \
  -d '{"name": "pnp-counter-to-cab", "description": "Pick object from counter, place in cabinet", "dependencies": ["navigate-to-fixture", "grasp-from-surface", "place-in-container"]}'
```

**Key rules:**
- The root skill (no other skill depends on it) gets its test FREE from the sim
- Sub-skills need custom tests — the test_writer agent handles this
- Reuse skills across tasks — design for composability

### 4. Start development

```bash
curl -X POST http://localhost:8766/xbot-start
```

This spawns:
- For each **sub-skill**: test_writer → dev → mechanical_test → review
- For the **root skill**: dev → mechanical_test (using sim's `_check_success()`) → review

Leaf skills start immediately. When a skill passes review (human confirms on dashboard),
downstream skills auto-spawn.

### 5. Monitor and review

- Dashboard: `http://localhost:8070/local/`
- Entries API: `curl http://localhost:8766/entries`
- When a skill reaches "review" status, check its results on the dashboard and confirm

### 6. After task 1 is done — move to task 2

Update `graph.json` with a new `task_env` and `task_source`. Existing skills on disk
(`~/tidybot_uni/skills/`) carry over — the planner will reuse them for the next task.

Progression example:
1. `RoboCasa-Pn-P-Counter-To-Cab-v0` → builds: navigate, grasp, place-in-container
2. `RoboCasa-Open-Drawer-v0` → reuses navigate, adds: pull-drawer-open
3. `RoboCasa-Pn-P-Counter-To-Stove-v0` → reuses navigate + grasp, adds: place-on-burner

Each new task builds on skills from previous tasks.

## Architecture

```
graph.json (task_env + entries)
    │
    ▼
Orchestrator (port 8766)
    │
    ├── Planner agent → reads task source → builds skill tree
    │
    ├── /xbot-start
    │   ├── Sub-skills: test_writer → dev → mechanical_test → review
    │   └── Root skill: dev → mechanical_test (sim _check_success) → review
    │
    └── Human confirms review → status=done → downstream auto-spawns
        │
        ▼
    Sim (port 5555) + Agent Server (port 8080)
        └── /task/success (port 5500) — sim's _check_success() for root eval
```

## Success Check

The sim exposes `GET http://localhost:5500/task/success` which calls the env's
`_check_success()`. This is used automatically by the mechanical test for the root skill.
Sub-skills define their own success checks via the test_writer agent.
