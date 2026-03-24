# /xbot-plan — Plan or replan the skill tree

Plan (or extend) the skill dependency tree for a robotics task.

## Usage

```
/xbot-plan <goal or new sub-goal>
```

If a graph is already loaded with existing entries, this is a **replan** — the new goal is added on top of the existing tree. If the tree is empty, this is a fresh plan.

## What to do

1. **Check what's running.** Verify the orchestrator is up:

```bash
curl -sf http://localhost:8766/entries
```

If it's not running, tell the user they need to start the orchestrator first:

```bash
cd ~/tidybot_uni/marketing/Tidybot-Universe/skill-agent-setup/claude-code && \
  env -u ANTHROPIC_API_KEY PYTHONPATH="$HOME/.local/lib/python3.10/site-packages:$PYTHONPATH" \
  PYTHONUNBUFFERED=1 python3 agent_orchestrator.py \
  --graph graphs/<graph-name> &
```

Also check the sim and agent server:
```bash
curl -sf http://localhost:8080/state   # agent server
curl -sf http://localhost:5500/state   # sim (optional, for RoboCasa tasks)
```

2. **Show the current tree** (if any entries exist). Print the tree as a dependency list so the user sees what's already planned/done.

3. **Call the planner.** Send the user's goal to the orchestrator's plan endpoint:

```bash
curl -X POST http://localhost:8766/plan \
  -H "Content-Type: application/json" \
  -d '{"prompt": "$ARGUMENTS"}'
```

4. **Stream progress.** The planner agent will modify the tree via `POST/PATCH/DELETE /entries`. Poll `GET /entries` periodically (every ~10s) and show the user what changed — new skills added, dependencies wired up, etc.

5. **Summarize.** When the planner finishes, print the final tree and ask the user if they want to start development (`/xbot-dev`).

## Replan behavior

When the tree already has entries, the planner sees them in its system prompt (`current_tree`). The user's new goal is additive — the planner should:
- Reuse existing skills where possible
- Add new skills and wire them into the existing graph
- Not delete existing "done" skills unless the user explicitly asks

## Available graphs

Graphs live in `~/tidybot_uni/marketing/Tidybot-Universe/skill-agent-setup/claude-code/graphs/`:

```
single-stage-tasks/   — RoboCasa single-stage task pipeline
cook-all-food/        — open drawers, collect food into pot, turn on stove
open-fixtures/        — kitchen fixture manipulation (drawers, cabinets)
open-all-drawers/     — open all kitchen drawers
```

A graph can have metadata (`task_env`, `task_source`) that tells the planner what sim task to target.
