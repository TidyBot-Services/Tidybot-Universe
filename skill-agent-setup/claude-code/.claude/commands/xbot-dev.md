# /xbot-dev — Start development on ready skills

Kick off the dev pipeline for all skills whose dependencies are satisfied.

## Usage

```
/xbot-dev
```

## What to do

1. **Check prerequisites.** All three services must be running:

```bash
curl -sf http://localhost:8766/entries  # orchestrator
curl -sf http://localhost:8080/state    # agent server
```

If the sim is needed (RoboCasa tasks), also check:
```bash
nc -z localhost 5500                    # sim
```

If any service is down, tell the user what to start and stop.

2. **Show the current tree.** Fetch `GET /entries` and display it with statuses:

```bash
curl -s http://localhost:8766/entries | python3 -c "
import json, sys
entries = json.load(sys.stdin)
for e in entries:
    deps = ', '.join(e.get('dependencies', [])) or '(leaf)'
    status = e.get('status', 'planned')
    print(f'  [{status:>10}] {e[\"name\"]:30s} deps: {deps}')
"
```

3. **Generate SKILL.md for each skill.** Before starting dev agents, generate a `SKILL.md`
   for every skill in the graph that doesn't have one yet. For each skill:

   - Read the graph entry (name, description, dependencies)
   - Read the graph metadata (`graph.json`) for task context
   - If it has dependencies, read those dependency skills' SKILL.md files to understand
     what inputs/outputs they provide
   - Write `SKILL.md` to `skills/<skill-name>/SKILL.md` with this structure:

   ```markdown
   # <skill-name>

   ## Description
   <What the skill does — 2-3 sentences>

   ## Preconditions (Input State)
   - <What state the robot/scene must be in before this skill runs>
   - <e.g. "Robot arm is at home position", "Object is visible on counter">

   ## Postconditions (Output State)
   - <What state the robot/scene will be in after successful execution>
   - <e.g. "Object is grasped and lifted 15cm above surface">

   ## Success Criteria
   - <Concrete, measurable criteria for the evaluator to check>
   - <e.g. "Gripper width < 0.01m (object held)", "Base within 10cm of target">

   ## Dependencies
   - <List dependency skills and what they provide>

   ## Notes
   - <Approach hints, constraints, known issues>
   ```

   Also create the `scripts/` directory if it doesn't exist:
   ```bash
   mkdir -p skills/<skill-name>/scripts
   ```

4. **Show summary and ask for confirmation.** Display a table of all skills with their
   preconditions and postconditions. Format it clearly so the user can verify the I/O
   contracts between skills make sense (one skill's postconditions should match the next
   skill's preconditions). Example:

   ```
   Skill Tree I/O Summary:

   [leaf] detect-objects
     IN:  Robot at home, cameras active
     OUT: Object positions known (printed to stdout)

   [leaf] navigate-to-object
     IN:  Target position known
     OUT: Base within reach of target

   [depends: detect-objects, navigate-to-object] grasp-object
     IN:  Object within arm reach, position known
     OUT: Object grasped, lifted 15cm

   Ready to start development? (y/n)
   ```

   **STOP HERE and wait for the user to confirm.** Do NOT call xbot-start until the user
   says yes. If the user wants changes, edit the SKILL.md files and re-display the summary.

5. **Start development.** Only after user confirms, call xbot-start:

```bash
curl -X POST http://localhost:8766/xbot-start
```

This auto-spawns dev agents for every skill whose:
- Status is "planned" or "failed"
- All dependencies are "done"

Leaf skills (no dependencies) start immediately.

6. **Report what spawned.** Show the user which skills started and which are still blocked.

7. **Explain the review gate.** Remind the user:
   - Skills go through: dev → evaluator → **review** (human confirms) → done
   - Root skill also gets a mechanical test (sim _check_success) before review
   - After confirming a skill on the dashboard, downstream skills auto-spawn
   - Dashboard: `http://localhost:8070/local/`

## Development pipeline

```
planned → dev agent implements skill
        → evaluator reviews execution (images + logs)
        → review (human confirms on dashboard)
        → done → downstream skills auto-spawn
```

## Monitoring

After starting, remind the user of these URLs:

- **Dashboard:** http://localhost:8070/local/ — visual skill tree, agent chat logs, inject hints, confirm skills
- **Agent Server:** http://localhost:8080 — robot API, execution logs, camera feeds, `/services/dashboard` for service status

Or poll via CLI:

```bash
curl -s http://localhost:8766/status   # agent statuses
curl -s http://localhost:8766/entries   # skill tree with statuses
```
