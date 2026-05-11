# Module — orchestrator (skill DAG manager)

The agentic skill development orchestrator. Lives at `skill-agent-setup/claude-code/`.

## What it does

- Loads a skill graph (`graphs/<name>/graph.json` + `skills/<skill>/SKILL.md`)
- Spawns dev/evaluator agents per skill via a harness backend
- Tracks skill statuses across a state machine
- Auto-spawns downstream skills when deps are done
- Runs mechanical tests on root skills (via sim `/task/success`)
- Exposes WebSocket on `:8765` and HTTP on `:8766` for dashboard

## Key code paths

| File | Role |
|---|---|
| `agent_orchestrator.py` | Main orchestrator (3500+ lines). State machine, dispatch to harness, evaluator agent, mechanical test, broadcast loop. |
| `agent_orchestrator_openclaw.py` | OpenClaw harness backend. Sibling module imported when `HARNESS=openclaw`. |
| `submit_and_wait.py` | CLI to submit code synchronously through orch (testing helper) |
| `graphs/<name>/graph.json` | Skill graph definition |
| `graphs/<name>/skills/<skill>/SKILL.md` | Per-skill spec read by dev + evaluator |
| `graphs/<name>/skills/<skill>/scripts/main.py` | Dev's output code |
| `graphs/<name>/skills/<skill>/tests/run_trials.py` | Auto-generated mechanical test (root skills only) |

## Skill state machine

```
planned → writing (dev) → evaluating (LLM eval)
                              ├─ pass + root → testing (mechanical) → review → done
                              ├─ pass + sub  → review → done
                              └─ fail → auto-spawn dev with feedback (or human review)
```

- `planned`: waiting for deps
- `writing`: dev agent is iterating
- `evaluating`: LLM evaluator reviewing the latest exec recording
- `testing`: root-only — running auto-generated mechanical test via sim `/task/success`
- `review`: waiting for human approval on dashboard
- `failed`: exhausted retry attempts
- `done`: human-confirmed, unblocks downstream skills

## Important globals (avoid module-duplication bugs)

The shim layer for `--harness openclaw` does `from agent_orchestrator import ...` which can cause Python to load the module twice (once as `__main__`, once as the sibling import). Then mutable globals (skill_entries, ws_clients, agents, etc.) become out-of-sync.

**Bootstrap at end of `agent_orchestrator.py`** rebinds module-level mutables to `__main__`'s instances:

```python
_main_mod = sys.modules.get("__main__")
_self_mod = sys.modules.get(__name__)
if _main_mod is not _self_mod:
    for name in ("skill_entries", "targets", "agents", "ws_clients", ...):
        v = getattr(_main_mod, name, None)
        if v is not None: globals()[name] = v
```

If a new global mutable is added, **add it to this list** or expect heisenbugs.

## Evaluator prompt

`SYSTEM_PROMPT_EVALUATOR` lives at ~line 2266 of `agent_orchestrator.py`. Tells the evaluator how to:

- Read `stdout.log` + `stderr.log` from the exec dir as primary evidence (Evidence Hierarchy rule 1)
- Cross-reference with `state_log.jsonl` for state values
- Use camera images sparingly (Evidence Hierarchy rule 3)
- Output a single-line `EVAL_RESULT: {"passed": ..., "feedback": "..."}` envelope

Updated 2026-05-09 to read `.log` files instead of expecting stdout in metadata.json — see `decisions/0005-stdout-via-files.md`.

## Dev prompt

`SYSTEM_PROMPT_DEV` at ~line 1000-2200. Sections include:
- SDK overview (what's importable)
- Critical patterns (qpos→world frames, arm.move_to_pose persists nothing, etc.)
- Code template
- How to debug iteratively
- Skill.md success criteria

The empirical qpos→world mapping and warning about `arm.move_to_pose` not persisting roll/pitch/yaw came from a debugging marathon — see `~/.claude/projects/.../memory/feedback_dev_silent_success_misread.md`.

## Harness dispatch

```python
if HARNESS == "openclaw":
    import agent_orchestrator_openclaw as _openclaw_backend
    _runner = _openclaw_backend._run_agent_openclaw
else:
    _runner = _run_agent_sdk
```

The two backends present the same surface to the orch core. See `decisions/0003-multi-harness-llm-agnostic.md`.

## Autonomous mode

`--autonomous` flag enables auto-retry of failed skills with evaluator feedback injected as next dev's prompt. See `_auto_spawn_ready_skills` and `_resolve_completion`.

Cap at 3 attempts per skill before giving up. Subsequent runs need manual `/spawn` or `/xbot-start`.

## REST API surface

| Endpoint | Purpose |
|---|---|
| `GET /entries` | All skill entries with status |
| `POST /xbot-start` | Spawn all ready leaf skills |
| `POST /spawn` | Spawn one specific skill |
| `PATCH /entries/{name}` | Manual status update (e.g. mark `done`) |
| `GET /agents` | Currently active agents |
| `POST /confirm` | Human approval gate |
| `WebSocket :8765` | Dashboard live updates |

## Restart procedure

```bash
# Kill cleanly
ORCH=$(pgrep -f "agent_orchestrator.py")
kill $ORCH; sleep 2
# Verify ports
ss -tnl | grep -E ':8765 |:8766 '
# Restart (openclaw harness, autonomous)
cd ~/文档/Tidybot-Universe/skill-agent-setup/claude-code
conda run -n maniskill --no-capture-output \
  env -u ANTHROPIC_API_KEY \
       LD_PRELOAD=$HOME/miniconda3/envs/maniskill/lib/libstdc++.so.6 \
       PYTHONUNBUFFERED=1 \
       ~/bin/with-litellm.sh \
       python3 agent_orchestrator.py --graph graphs/<name> --harness openclaw --autonomous \
       > /tmp/orch_autonomous.log 2>&1 &
```

For setup of `--harness openclaw` (LiteLLM keys, agent creation), see `modules/harness-openclaw.md` and `skill-agent-setup/claude-code/CLAUDE-OPENCLAW-HARNESS.md`.

## Related

- `decisions/0002-skill-dag-decomposition.md`
- `decisions/0003-multi-harness-llm-agnostic.md`
- `decisions/0005-stdout-via-files.md`
- `modules/agent-server.md` — what dev's submitted code talks to
- `modules/harness-openclaw.md` — OpenClaw setup details
- `patterns/dev-model-failure-modes.md` — what to expect when dev agent gets stuck
