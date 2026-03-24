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
curl -sf http://localhost:5500/state    # sim
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

3. **Start development.** Call xbot-start:

```bash
curl -X POST http://localhost:8766/xbot-start
```

This auto-spawns the pipeline (test_writer -> dev -> mechanical_test -> review) for every skill whose:
- Status is "planned"
- All dependencies are "done"

Leaf skills (no dependencies) start immediately.

4. **Report what spawned.** Show the user which skills started and which are still blocked.

5. **Explain the review gate.** Remind the user:
   - Skills go through: test_writer -> dev -> mechanical_test -> **review** (human confirms) -> done
   - After confirming a skill on the dashboard, downstream skills auto-spawn
   - Dashboard: `http://localhost:8070/local/`

## Development pipeline

```
planned → test_writer writes tests
        → dev agent implements skill
        → mechanical_test runs trials
        → review (human confirms on dashboard)
        → done → downstream skills auto-spawn
```

## Monitoring

After starting, the user can monitor progress on the dashboard at `http://localhost:8070/local/` or by polling:

```bash
curl -s http://localhost:8766/status   # agent statuses
curl -s http://localhost:8766/entries   # skill tree with statuses
```
