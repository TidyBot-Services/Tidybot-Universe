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
import time
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

try:
    import websockets
except ImportError:
    raise ImportError("pip install websockets")

try:
    from claude_agent_sdk import (
        ClaudeSDKClient,
        ClaudeAgentOptions,
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
    )
    HAS_SDK = True
except ImportError:
    HAS_SDK = False
    ClaudeSDKClient = None
    ClaudeAgentOptions = None
    AssistantMessage = None
    ResultMessage = None
    TextBlock = None
    ToolUseBlock = None
    print("[ORCH] WARNING: claude-agent-sdk not found, falling back to CLI subprocess mode")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WS_PORT = 8765
PROJECT_DIR = Path.home() / "tidybot_uni"
WEBSITE_DIR = PROJECT_DIR / "marketing" / "TidyBot-Services.github.io"
AGENT_SERVER = "http://localhost:8080"

# Parse required --graph argument
import argparse
_parser = argparse.ArgumentParser(description="Claude Agent Orchestrator")
_parser.add_argument("--graph", required=True, type=Path,
                     help="Path to an existing JSON file with skill entries")
_args = _parser.parse_args()
if not _args.graph.exists():
    _parser.error(f"graph file does not exist: {_args.graph}")
LOCAL_REPOS = _args.graph

# ---------------------------------------------------------------------------
# Agent type system prompts
# ---------------------------------------------------------------------------

SKILLS_DIR = PROJECT_DIR / "skills"

SYSTEM_PROMPT_PLANNER = """\
You are a robotics skill planner for the TidyBot Universe project.

## Your Job
Break down high-level goals into a dependency tree of robot skills.
You DO NOT write skill code — you design the skill tree structure.

## Your Robot
- Franka Panda 7-DOF arm + mobile base + Robotiq gripper + cameras
- Runs in RoboCasa (MuJoCo kitchen sim) or on real hardware
- Skills use robot_sdk (arm, base, gripper, sensors, rewind)

## Existing Skills on Disk
Skills in ~/tidybot_uni/skills/:
{existing_skills}

## Current Skill Tree
{current_tree}

## How to Modify the Tree
You have a tool: the orchestrator HTTP API at http://localhost:{http_port}

To add a skill:
```bash
curl -X POST http://localhost:{http_port}/entries -H "Content-Type: application/json" -d '{{"name": "skill-name", "description": "what it does", "dependencies": ["dep1", "dep2"]}}'
```

To remove a skill:
```bash
curl -X DELETE http://localhost:{http_port}/entries/skill-name
```

To update a skill (e.g. change dependencies):
```bash
curl -X PATCH http://localhost:{http_port}/entries/skill-name -H "Content-Type: application/json" -d '{{"dependencies": ["new-dep1"], "description": "updated desc"}}'
```

To list all entries:
```bash
curl http://localhost:{http_port}/entries
```

## Rules
1. Read existing skills on disk first — reuse what's already built
2. Skills should be small and composable — one behavior per skill
3. Leaf skills (no dependencies) are primitives (navigate, grasp, detect)
4. Higher-level skills compose lower ones
5. Name skills with kebab-case (e.g. pull-drawer-open, not pullDrawerOpen)
6. Every skill needs a clear description of what it does
7. Use curl to call the API — changes appear on the dashboard immediately
8. After building the tree, summarize what you created and why
"""


def _has_test(skill: str) -> bool:
    """Check if a skill has a test file."""
    test_file = SKILLS_DIR / skill / "tests" / "run_trials.py"
    return test_file.exists()


SYSTEM_PROMPT_TEST_WRITER = """\
You are a robotics skill test designer for the TidyBot Universe project.

## Your Job
Write a test file for the skill **{{skill_name}}**.
You ONLY write tests — do not write or modify skill code in scripts/.

## Existing Skills
{existing_skills}

Read SKILL.md and scripts/main.py of existing skills that have tests/ to understand the test pattern.
For example, look at ~/tidybot_uni/skills/pick-up-object/tests/run_trials.py for a good reference.

## What to Create
Create ~/tidybot_uni/skills/{{skill_name}}/tests/run_trials.py that:
1. Imports and runs the skill's main function
2. Defines clear success criteria (what does "success" mean for this skill?)
3. Runs N trials (configurable, default 3)
4. Prints a JSON summary to stdout:
```json
{{"skill": "{{skill_name}}", "success_rate": 80, "total_trials": 5, "passed": 4, "failed": 1, "failure_modes": ["..."]}}
```

The test runs via the robot code execution API:
- POST {agent_server}/code/execute submits code with access to robot_sdk
- The test script should use robot_sdk modules (arm, base, gripper, sensors, rewind)
- After each trial, rewind to reset the robot state

Also create the skill directory structure if it doesn't exist:
```
{{skill_name}}/
├── SKILL.md              # Brief description of what the skill should do
├── scripts/
│   └── (leave empty or create placeholder main.py)
└── tests/
    ├── run_trials.py     # YOUR MAIN OUTPUT
    └── results/          # Empty dir for results
```

Be concise. Focus on defining what success looks like.
"""

SYSTEM_PROMPT_DEV = """\
You are a robotics skill developer for the TidyBot Universe project.

## Your Robot
- Franka Panda 7-DOF arm + mobile base + Robotiq gripper + cameras
- API at {agent_server} — read the guide at {agent_server}/docs/guide/html before writing code
- SDK reference at {agent_server}/code/sdk/markdown
- Skills run via POST /code/execute with access to robot_sdk (arm, base, gripper, sensors, rewind)

## Existing Skills
Skills live in ~/tidybot_uni/skills/. First check what's already there:
{existing_skills}

Read SKILL.md of relevant existing skills to understand patterns and avoid reinventing.

## Skill Structure
Each skill is a directory under ~/tidybot_uni/skills/<skill-name>/ with:
```
<skill-name>/
├── SKILL.md              # Skill description, pipeline, usage, config
├── scripts/
│   ├── main.py           # Main skill code (uses robot_sdk)
│   └── deps.txt          # Skill dependencies (other skills needed)
└── tests/
    ├── run_trials.py     # Test script that runs trials and reports results
    └── results/          # Trial results (JSON, images)
```

## Your Job
You are working on the skill: **{{skill_name}}**
If ~/tidybot_uni/skills/{{skill_name}}/ does not exist, create it with the structure above.
If it exists, read it and modify as requested.

## Rules
1. Check existing skills first — reuse and chain, don't reinvent
2. Read the SDK docs before writing robot code — don't guess APIs
3. Use rewind as your safety net — every movement is reversible
4. **DO NOT modify anything in tests/ — tests are written separately and are read-only to you**
5. Read tests/run_trials.py to understand what success looks like before writing code
6. Be concise in your reasoning
7. The sim (RoboCasa/ManiSkill) uses the same API — test there first if hardware unavailable
"""

SYSTEM_PROMPT_TEST = """\
You are a robotics skill tester for the TidyBot Universe project.

## Your Job
Test the skill **{{skill_name}}** by running it against the robot (or sim) and evaluating results.
You DO NOT write new skill code — you run existing code and analyze outcomes.

## Existing Skills
Skills live in ~/tidybot_uni/skills/. The skill you're testing:
{existing_skills}

Read the skill's SKILL.md and scripts/main.py to understand what it does before testing.

## Skill Structure
```
<skill-name>/
├── SKILL.md              # Skill description, pipeline, usage
├── scripts/main.py       # Main skill code
└── tests/
    ├── run_trials.py     # Test runner (if exists, use it)
    └── results/          # Trial results output
```

## How to Test
1. Read the skill code: ~/tidybot_uni/skills/{{skill_name}}/scripts/main.py
2. If tests/run_trials.py exists, read it to understand the test protocol
3. Acquire a lease: POST {agent_server}/lease/acquire with {{"holder": "test-agent"}}
4. Submit the skill code: POST {agent_server}/code/execute with the code and X-Lease-Id header
5. Poll for results: GET {agent_server}/code/status (use ?stdout_offset=N for incremental output)
6. Get final result: GET {agent_server}/code/result
7. Check execution recordings: GET {agent_server}/code/recordings for camera frames and state logs
8. Release the lease: POST {agent_server}/lease/release

## Execution Recordings
Each code execution is recorded to ~/tidybot_uni/agent_server/logs/code_executions/{{execution_id}}/:
- *.jpg — camera frames captured every 2 seconds
- state_log.jsonl — robot state at 10 Hz (arm joints, base pose, gripper)
- metadata.json — execution summary (duration, frame count, timestamps)

Access recordings via API:
- GET {agent_server}/code/recordings — list all execution IDs
- GET {agent_server}/code/recordings/{{id}} — frames matched with nearest state

## System Logger (Trajectory)
The system logger records trajectory waypoints at 10 Hz (threshold-filtered).
Check trajectory via:
- GET {agent_server}/trajectory — recorded waypoints
- GET {agent_server}/rewind/status — trajectory info and rewind state

## Evaluation
After running the skill:
1. Check exit code and stdout/stderr for errors
2. Review camera frames — did the robot do what was expected?
3. Check state_log.jsonl — did arm/base/gripper follow expected trajectory?
4. Run multiple trials (3-5) to estimate success rate
5. Report results: success_rate, total_trials, failure modes, and trial images

## Output Format
After testing, output a JSON summary:
```json
{{
  "skill": "skill-name",
  "success_rate": 80,
  "total_trials": 5,
  "passed": 4,
  "failed": 1,
  "failure_modes": ["gripper didn't close fully on trial 3"],
  "trial_images": ["path/to/frame1.jpg", "path/to/frame2.jpg"]
}}
```

Be concise. Focus on what failed and why.
"""

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

ws_clients: set = set()

# In-memory skill entries (seeded from local_repos.json, updated dynamically)
skill_entries: list[dict] = []


def _load_entries():
    """Load entries from graph file into memory."""
    global skill_entries
    skill_entries = json.loads(LOCAL_REPOS.read_text())


def _save_entries():
    """Persist entries back to local_repos.json."""
    LOCAL_REPOS.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_REPOS.write_text(json.dumps(skill_entries, indent=2))


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
        "status": "done",
        "agent_id": None,
        "agent_status_text": None,
        "agent_log": [],
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
    agent_type: str = "dev"           # dev | test | test_writer | planner
    status: str = "starting"          # starting | running | stopped | done | confirmed_done | error
    session_id: Optional[str] = None
    client: Optional[object] = None   # ClaudeSDKClient (SDK mode)
    proc: Optional[object] = None     # subprocess (CLI fallback)
    task: Optional[asyncio.Task] = None
    log: list = field(default_factory=list)


agents: dict[str, AgentState] = {}    # agent_id -> AgentState


# ---------------------------------------------------------------------------
# WebSocket: browser <-> orchestrator
# ---------------------------------------------------------------------------

async def ws_broadcast(msg: dict):
    data = json.dumps(msg)
    gone = set()
    for c in ws_clients:
        try:
            await c.send(data)
        except websockets.ConnectionClosed:
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
    if agent_type in ("test", "test_writer"):
        return {
            "starting": "testing",
            "running": "testing",
            "stopped": "failed",
            "done": "agent_done",
            "confirmed_done": "done",
            "error": "failed",
        }.get(internal, internal)
    if agent_type == "planner":
        # Planner doesn't map to a specific skill hex — uses _planner pseudo-skill
        return {
            "starting": "studying",
            "running": "studying",
            "stopped": "failed",
            "done": "done",
            "error": "failed",
        }.get(internal, internal)
    return {
        "starting": "writing",
        "running": "writing",
        "stopped": "failed",
        "done": "agent_done",
        "confirmed_done": "done",
        "error": "failed",
    }.get(internal, internal)


def build_full_sync() -> list[dict]:
    """Build a full_sync payload from in-memory entries with live agent state overlay."""
    import copy
    repos = copy.deepcopy(skill_entries)

    # Overlay live agent state onto matching entries
    agent_by_skill = {a.skill: a for a in agents.values()}
    for repo in repos:
        a = agent_by_skill.get(repo["name"])
        if a:
            repo["status"] = _map_status(a.status, a.agent_type)
            repo["agent_id"] = a.agent_id
            repo["agent_status_text"] = f"{a.status}"
            repo["agent_type"] = a.agent_type
            repo["agent_log"] = a.log[-50:]  # last 50 messages

    return repos


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

                elif t == "test":
                    skill = msg.get("skill", "")
                    print(f"[WS] test -> {skill}")
                    test_prompt = (
                        f"Test the '{skill}' skill. "
                        f"Read the skill code in ~/tidybot_uni/skills/{skill}/, "
                        f"then run it against the robot API at {AGENT_SERVER}. "
                        f"Run 3 trials and report results."
                    )
                    asyncio.create_task(spawn_agent(skill, test_prompt, agent_type="test"))

                elif t == "spawn":
                    skill = msg.get("skill", "new-task")
                    prompt = msg.get("prompt", "")
                    agent_type = msg.get("agent_type", "dev")
                    print(f"[WS] spawn -> {skill} ({agent_type}): {prompt[:80]}")
                    asyncio.create_task(spawn_agent(skill, prompt, agent_type=agent_type))

                elif t == "confirm_done":
                    skill = msg.get("skill", "")
                    agent_id = msg.get("agent_id", "")
                    print(f"[WS] confirm_done -> {skill}")
                    # Mark as confirmed done (bypasses agent_done mapping)
                    state = agents.get(agent_id)
                    if state:
                        state.status = "confirmed_done"
                    asyncio.create_task(ws_broadcast_status(skill, agent_id, "confirmed_done", "Done"))

                elif t == "plan":
                    prompt = msg.get("prompt", "")
                    print(f"[WS] plan -> {prompt[:80]}")
                    asyncio.create_task(spawn_agent("_planner", prompt, agent_type="planner"))

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

async def spawn_agent(skill: str, prompt: str, agent_type: str = "dev") -> str:
    """Spawn a new Claude Code agent for a skill/task."""
    # If there's already an active agent for this skill, stop it first
    for aid, a in list(agents.items()):
        if a.skill == skill and a.status in ("starting", "running"):
            await stop_agent(aid)

    agent_id = f"agent-{uuid.uuid4().hex[:8]}"
    state = AgentState(agent_id=agent_id, skill=skill, agent_type=agent_type)
    agents[agent_id] = state

    labels = {"test": "Testing...", "test_writer": "Writing tests...", "dev": "Developing..."}
    await ws_broadcast_status(skill, agent_id, "starting", labels.get(agent_type, "Spawning..."))

    if HAS_SDK:
        state.task = asyncio.create_task(_run_agent_sdk(state, prompt))
    else:
        state.task = asyncio.create_task(_run_agent_cli(state, prompt))

    return agent_id


async def spawn_skill_pipeline(skill: str, user_prompt: str):
    """Orchestrate the skill development pipeline: test_writer → dev → mechanical test."""
    if _has_test(skill):
        # Test exists — go straight to dev
        print(f"[ORCH] {skill}: test exists, spawning dev agent")
        await spawn_agent(skill, user_prompt, agent_type="dev")
    else:
        # No test — spawn test writer first, dev will be spawned when it finishes
        print(f"[ORCH] {skill}: no test found, spawning test writer first")
        tw_prompt = (
            f"Write tests for the '{skill}' skill. "
            f"The user wants this skill to: {user_prompt}\n"
            f"Create ~/tidybot_uni/skills/{skill}/tests/run_trials.py with clear success criteria."
        )
        await spawn_agent(skill, tw_prompt, agent_type="test_writer")


async def run_mechanical_test(skill: str):
    """Run the skill's test mechanically (no LLM). Broadcasts results to dashboard."""
    test_file = SKILLS_DIR / skill / "tests" / "run_trials.py"
    if not test_file.exists():
        await ws_broadcast_agent_msg(skill, "No test file found — skipping mechanical test", "test")
        return

    # Read test code
    test_code = test_file.read_text()

    await ws_broadcast_agent_msg(skill, "Running mechanical test...", "test")

    # TODO: When agent server is running, execute via POST /code/execute
    # For now, just report that tests would run
    import subprocess as sp
    try:
        # Try running directly (won't work without agent server, but shows the flow)
        result = await asyncio.create_subprocess_exec(
            "python3", str(test_file),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(SKILLS_DIR / skill),
        )
        stdout, stderr = await asyncio.wait_for(result.communicate(), timeout=300)
        output = stdout.decode().strip()
        errors = stderr.decode().strip()

        if result.returncode == 0:
            await ws_broadcast_agent_msg(skill, f"Test passed:\n{output}", "test")
            # Try to parse JSON results
            try:
                results = json.loads(output.split('\n')[-1])
                await ws_broadcast({"type": "test_results", "skill": skill, "results": results})
            except (json.JSONDecodeError, IndexError):
                pass
        else:
            await ws_broadcast_agent_msg(skill, f"Test failed (exit {result.returncode}):\n{errors or output}", "test")
    except asyncio.TimeoutError:
        await ws_broadcast_agent_msg(skill, "Test timed out (5 min limit)", "test")
    except Exception as e:
        await ws_broadcast_agent_msg(skill, f"Test error: {e}", "test")


def _get_system_prompt(agent_type: str, skill_name: str = "") -> str:
    """Get system prompt for agent type, populated with live skills list."""
    # List existing skills on disk
    skills_list = ""
    if SKILLS_DIR.exists():
        skill_dirs = sorted(d.name for d in SKILLS_DIR.iterdir() if d.is_dir() and not d.name.startswith('.'))
        has_target = skill_name in skill_dirs
        skills_list = "\n".join(f"  - {s}{'  ← (your target)' if s == skill_name else ''}" for s in skill_dirs)
        if skill_name and not has_target:
            skills_list += f"\n  - {skill_name}  ← DOES NOT EXIST YET (create it)"
    else:
        skills_list = "  (no skills directory found)"

    # For planner: also include current tree
    if agent_type == "planner":
        tree_lines = []
        for e in skill_entries:
            deps = e.get("dependencies", [])
            dep_str = f" → depends on: {', '.join(deps)}" if deps else " (leaf)"
            tree_lines.append(f"  - {e['name']}: {e.get('description', '(no description)')}{dep_str}")
        current_tree = "\n".join(tree_lines) if tree_lines else "  (empty — no skills in tree yet)"

        return SYSTEM_PROMPT_PLANNER.format(
            existing_skills=skills_list,
            current_tree=current_tree,
            http_port=WS_PORT + 1,
        )

    templates = {
        "test": SYSTEM_PROMPT_TEST,
        "test_writer": SYSTEM_PROMPT_TEST_WRITER,
        "dev": SYSTEM_PROMPT_DEV,
    }
    template = templates.get(agent_type, SYSTEM_PROMPT_DEV)
    return template.format(
        agent_server=AGENT_SERVER,
        existing_skills=skills_list,
        skill_name=skill_name,
    )


async def _run_agent_sdk(state: AgentState, prompt: str):
    """Run an agent using the Claude Agent SDK (ClaudeSDKClient)."""
    options = ClaudeAgentOptions(
        cwd=str(PROJECT_DIR),
        permission_mode="bypassPermissions",
        system_prompt=_get_system_prompt(state.agent_type, state.skill),
    )

    try:
        client = ClaudeSDKClient(options=options)
        state.client = client

        async with client:
            state.status = "running"
            await ws_broadcast_status(state.skill, state.agent_id, "running", "Working...")

            # First query
            await client.query(prompt)
            await _consume_sdk_response(state, client)

    except asyncio.CancelledError:
        state.status = "stopped"
        await ws_broadcast_status(state.skill, state.agent_id, "stopped", "Cancelled")
    except Exception as e:
        state.status = "error"
        err = str(e)[:200]
        state.log.append(f"ERROR: {err}")
        await ws_broadcast_status(state.skill, state.agent_id, "error", err)
        await ws_broadcast_agent_msg(state.skill, f"Error: {err}", state.agent_type)
    finally:
        if state.status == "running":
            state.status = "done"
            await _handle_agent_done(state)


async def _handle_agent_done(state: AgentState):
    """Handle agent completion — chain to next step in pipeline."""
    if state.agent_type == "test_writer":
        # Test writer done → spawn dev agent
        await ws_broadcast_agent_msg(state.skill, "Tests written — launching dev agent...", state.agent_type)
        # The original user prompt was embedded in the test_writer prompt; extract a dev-friendly version
        await spawn_agent(state.skill, f"Develop the '{state.skill}' skill. Read tests/run_trials.py to understand what success looks like, then implement scripts/main.py.", agent_type="dev")
    elif state.agent_type == "dev":
        # Dev done → run mechanical test
        await ws_broadcast_status(state.skill, state.agent_id, "done", "Dev done, running tests...")
        await ws_broadcast_agent_msg(state.skill, "Dev complete — running tests...", state.agent_type)
        asyncio.create_task(run_mechanical_test(state.skill))
        # After mechanical test, go to review
        await ws_broadcast_status(state.skill, state.agent_id, "done", "Finished")
    else:
        await ws_broadcast_status(state.skill, state.agent_id, "done", "Finished")


async def _consume_sdk_response(state: AgentState, client: ClaudeSDKClient):
    """Consume the async iterator from a ClaudeSDKClient and broadcast to dashboard."""
    async for message in client.receive_response():
        if state.status == "stopped":
            break

        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    # Truncate long text for the chat log
                    text = block.text.strip()
                    if len(text) > 500:
                        text = text[:500] + "..."
                    state.log.append(text)
                    await ws_broadcast_agent_msg(state.skill, text, state.agent_type)

                elif isinstance(block, ToolUseBlock):
                    tool_msg = f"Using {block.name}..."
                    await ws_broadcast_status(
                        state.skill, state.agent_id, "running", tool_msg
                    )

        elif isinstance(message, ResultMessage):
            state.session_id = message.session_id
            cost = f"${message.total_cost_usd:.4f}" if message.total_cost_usd else "?"
            done_msg = f"Done — {message.num_turns} turns, {cost}"
            state.log.append(done_msg)
            await ws_broadcast_agent_msg(state.skill, done_msg, state.agent_type)


async def inject_hint(agent_id: str, text: str):
    """Send a follow-up message to a running agent."""
    state = agents.get(agent_id)
    if not state:
        print(f"[ORCH] inject: unknown agent {agent_id}")
        return

    if HAS_SDK and state.client and state.status == "running":
        # SDK mode: interrupt current work, send follow-up in same session
        try:
            await state.client.interrupt()
            # Small delay to let the interrupt settle
            await asyncio.sleep(0.5)
            await state.client.query(text)
            # Consume the new response in a background task
            state.task = asyncio.create_task(_consume_sdk_response(state, state.client))
        except Exception as e:
            print(f"[ORCH] inject SDK error: {e}")
            # Fallback: resume via session ID
            if state.session_id:
                await _inject_via_resume(state, text)
    elif state.session_id:
        await _inject_via_resume(state, text)
    else:
        print(f"[ORCH] inject: agent {agent_id} has no session to resume")


async def _inject_via_resume(state: AgentState, text: str):
    """Fallback: stop current proc and resume session with the hint."""
    # Stop the current task
    if state.task and not state.task.done():
        state.task.cancel()
        try:
            await state.task
        except asyncio.CancelledError:
            pass

    if state.proc and state.proc.returncode is None:
        import signal
        state.proc.send_signal(signal.SIGINT)
        await state.proc.wait()

    state.status = "running"
    await ws_broadcast_status(state.skill, state.agent_id, "running", "Resuming with hint...")

    if HAS_SDK:
        # Re-open SDK client with resume
        options = ClaudeAgentOptions(
            cwd=str(PROJECT_DIR),
            permission_mode="bypassPermissions",
            resume=state.session_id,
        )
        try:
            client = ClaudeSDKClient(options=options)
            state.client = client
            async with client:
                await client.query(text)
                await _consume_sdk_response(state, client)
        except Exception as e:
            state.log.append(f"Resume error: {e}")
            state.status = "error"
    else:
        state.task = asyncio.create_task(_run_agent_cli_resume(state, text))


async def stop_agent(agent_id: str):
    """Stop a running agent."""
    state = agents.get(agent_id)
    if not state:
        return

    state.status = "stopped"

    # SDK mode: interrupt
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

    state.log.append("Stopped by user")
    if state.agent_type == "test":
        # Stopping a test agent goes back to review, not failed
        await ws_broadcast_status(state.skill, state.agent_id, "done", "Test stopped")
        await ws_broadcast_agent_msg(state.skill, "Test stopped by user", state.agent_type)
    else:
        await ws_broadcast_status(state.skill, state.agent_id, "stopped", "Stopped by user")
        await ws_broadcast_agent_msg(state.skill, "Stopped by user", state.agent_type)


# ---------------------------------------------------------------------------
# Agent lifecycle — CLI fallback (no SDK)
# ---------------------------------------------------------------------------

async def _run_agent_cli(state: AgentState, prompt: str):
    """Run an agent via `claude` CLI subprocess with stream-json output."""
    cmd = [
        "claude",
        "--print",
        "--output-format", "stream-json",
        "--dangerously-skip-permissions",
        "-p", prompt,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(PROJECT_DIR),
    )
    state.proc = proc
    state.status = "running"
    await ws_broadcast_status(state.skill, state.agent_id, "running", "Working...")

    try:
        await _stream_cli_output(state, proc)
    except asyncio.CancelledError:
        if proc.returncode is None:
            import signal
            proc.send_signal(signal.SIGINT)
        state.status = "stopped"
        await ws_broadcast_status(state.skill, state.agent_id, "stopped", "Cancelled")
        return

    rc = await proc.wait()
    if state.status == "running":
        if rc == 0:
            state.status = "done"
            await _handle_agent_done(state)
        else:
            state.status = "error"
            await ws_broadcast_status(state.skill, state.agent_id, "error", f"Exit code {rc}")


async def _stream_cli_output(state: AgentState, proc):
    """Parse stream-json lines from Claude CLI and relay to dashboard."""
    async for line in proc.stdout:
        try:
            msg = json.loads(line.decode().strip())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        # Capture session ID
        if state.session_id is None and "session_id" in msg:
            state.session_id = msg["session_id"]

        msg_type = msg.get("type", "")

        # Assistant text
        if msg_type == "assistant":
            for block in msg.get("content", []):
                if block.get("type") == "text":
                    text = block["text"].strip()
                    if text:
                        if len(text) > 500:
                            text = text[:500] + "..."
                        state.log.append(text)
                        await ws_broadcast_agent_msg(state.skill, text, state.agent_type)

        # Tool use
        elif msg_type == "tool_use":
            name = msg.get("name", "unknown")
            tool_msg = f"Using {name}..."
            await ws_broadcast_status(
                state.skill, state.agent_id, "running", tool_msg
            )

        # Result (final message)
        elif msg_type == "result":
            sid = msg.get("session_id")
            if sid:
                state.session_id = sid
            cost = msg.get("total_cost_usd")
            turns = msg.get("num_turns", "?")
            cost_str = f"${cost:.4f}" if cost else "?"
            done_msg = f"Done — {turns} turns, {cost_str}"
            state.log.append(done_msg)
            await ws_broadcast_agent_msg(state.skill, done_msg, state.agent_type)


async def _run_agent_cli_resume(state: AgentState, text: str):
    """Resume a CLI agent session with a new prompt."""
    cmd = [
        "claude",
        "--print",
        "--output-format", "stream-json",
        "--dangerously-skip-permissions",
        "--resume", state.session_id,
        "-p", text,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(PROJECT_DIR),
    )
    state.proc = proc
    state.status = "running"
    await ws_broadcast_status(state.skill, state.agent_id, "running", "Resuming...")

    await _stream_cli_output(state, proc)

    rc = await proc.wait()
    if state.status == "running":
        state.status = "done" if rc == 0 else "error"
        label = "Finished" if rc == 0 else f"Exit code {rc}"
        await ws_broadcast_status(state.skill, state.agent_id, state.status, label)


# ---------------------------------------------------------------------------
# HTTP API (optional — for spawning agents from scripts/curl)
# ---------------------------------------------------------------------------

async def handle_http(reader, writer):
    """Minimal HTTP endpoint for spawning/controlling agents from scripts.

    POST /spawn   {"skill": "...", "prompt": "..."}
    POST /stop    {"agent_id": "..."}
    POST /inject  {"agent_id": "...", "text": "..."}
    GET  /status  -> all agents
    """
    data = await reader.read(8192)
    request = data.decode()
    lines = request.split("\r\n")
    method, path, _ = lines[0].split(" ", 2)

    # Extract body
    body = ""
    if "\r\n\r\n" in request:
        body = request.split("\r\n\r\n", 1)[1]

    response_body = ""
    status = "200 OK"

    try:
        if method == "POST" and path == "/plan":
            params = json.loads(body)
            aid = await spawn_agent("_planner", params["prompt"], agent_type="planner")
            response_body = json.dumps({"agent_id": aid})

        elif method == "POST" and path == "/spawn":
            params = json.loads(body)
            aid = await spawn_agent(params["skill"], params["prompt"])
            response_body = json.dumps({"agent_id": aid})

        elif method == "POST" and path == "/stop":
            params = json.loads(body)
            await stop_agent(params["agent_id"])
            response_body = json.dumps({"ok": True})

        elif method == "POST" and path == "/inject":
            params = json.loads(body)
            await inject_hint(params["agent_id"], params["text"])
            response_body = json.dumps({"ok": True})

        elif method == "GET" and path == "/status":
            response_body = json.dumps({
                aid: {
                    "skill": a.skill,
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
            response_body = json.dumps(entry or {"error": "not found"})

        else:
            status = "404 Not Found"
            response_body = json.dumps({"error": "not found"})

    except Exception as e:
        status = "500 Internal Server Error"
        response_body = json.dumps({"error": str(e)})

    http_response = (
        f"HTTP/1.1 {status}\r\n"
        f"Content-Type: application/json\r\n"
        f"Access-Control-Allow-Origin: *\r\n"
        f"Content-Length: {len(response_body)}\r\n"
        f"\r\n"
        f"{response_body}"
    )
    writer.write(http_response.encode())
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
    print(f"  Browser:  open http://localhost:8080/local/ and click 'Edit Skill'")
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
