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
import sys
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
PROJECT_DIR = Path.home() / "tidybot_uni"
WEBSITE_DIR = PROJECT_DIR / "marketing" / "TidyBot-Services.github.io"
AGENT_SERVER = "http://localhost:8080"

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

if _args.autonomous:
    autonomous_mode = True
    dev_mode = True  # autonomous implies dev mode (no planning gate either)

# ---------------------------------------------------------------------------
# Agent type system prompts
# ---------------------------------------------------------------------------

SKILLS_DIR = GRAPH_DIR / "skills"

SYSTEM_PROMPT_PLANNER = """\
You are a robotics skill planner for the TidyBot Universe project.

## Your Job
Break down high-level goals into a dependency tree of robot skills.
You DO NOT write skill code — you design the skill tree structure.

## Your Robot
- Franka Panda 7-DOF arm + mobile base + Robotiq gripper + cameras
- Runs in RoboCasa (MuJoCo kitchen sim) or on real hardware
- Skills use robot_sdk (arm, base, gripper, sensors, rewind)

## Task Context
{task_context}

## Existing Skills on Disk
Skills in {skills_dir}/:
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

## Starting Development
When the user approves the plan, run:
```bash
curl -X POST http://localhost:{http_port}/xbot-start
```
This auto-spawns dev pipelines for all skills whose dependencies are satisfied.
Leaf skills (no deps) start immediately. As skills pass tests, downstream skills auto-start.

## Skill Tree Design

Your tree should have ONE root skill at the top — this is the full task. It depends on
sub-skills that each do one thing. Sub-skills can share dependencies.

**Testing rules:**
- The **root skill** (the one no other skill depends on) gets its test automatically
  from the sim's `_check_success()` — you do NOT need to define success criteria for it.
- All **sub-skills** (leaf and intermediate) need custom tests written by the test_writer
  agent. The test_writer will define what "success" means for each sub-skill (e.g. "gripper
  is closed and holding an object" for a grasp skill).

Example tree for "pick from counter, place in cabinet":
```
pnp-counter-to-cab          (root — sim tests this)
├── grasp-from-surface       (sub — test_writer defines: object grasped?)
├── navigate-to-fixture      (sub — test_writer defines: within reach?)
└── place-in-container       (sub — test_writer defines: object released inside?)
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
9. When the user approves the plan, run `curl -X POST http://localhost:{http_port}/xbot-start` to kick off development
"""


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

    test_code = f'''\
#!/usr/bin/env python3
"""Auto-generated test for task root skill: {skill}
Task: {task_env}

Runs the skill's main.py via the agent server, then checks success
via the sim's /task/success endpoint (port 5500).
"""
import json
import time
import urllib.request
import urllib.error

AGENT_SERVER = "http://localhost:8080"
SIM_API = "http://localhost:5500"
NUM_TRIALS = 3


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
    # Read the skill's main.py
    import os
    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    main_py = os.path.join(skill_dir, "scripts", "main.py")
    if not os.path.exists(main_py):
        print(f"Trial {{trial_num}}: SKIP - no scripts/main.py")
        return False

    with open(main_py) as f:
        code = f.read()

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


SYSTEM_PROMPT_TEST_WRITER = """\
You are a robotics skill test designer for the TidyBot Universe project.

## Your Job
Write a test file for the skill **{{skill_name}}**.
You ONLY write tests — do not write or modify skill code in scripts/.

## Existing Skills
{existing_skills}

Read SKILL.md and scripts/main.py of existing skills that have tests/ to understand the test pattern.
For example, look at {skills_dir}/pick-up-object/tests/run_trials.py for a good reference.

## What to Create
Create {skills_dir}/{{skill_name}}/tests/run_trials.py that:
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
- Skills run via POST /code/submit (fire-and-forget) with access to robot_sdk

## Existing Skills
Skills live in {skills_dir}/. First check what's already there:
{existing_skills}

Read SKILL.md of relevant existing skills to understand patterns and avoid reinventing.

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
3. **Test by submitting**: `python {submit_script} scripts/main.py --holder dev:{{skill_name}}`
4. **Debug and iterate**: if it fails, read the error, fix the code, resubmit

### How to submit and test code
```bash
# Submit code, wait for result, get JSON output (stdout, stderr, exit_code, execution_id)
python {submit_script} scripts/main.py --holder dev:{{skill_name}}
```
This submits to the agent server, waits for completion, and prints results.
Every run is recorded (camera + state) — use execution_id to review.

### Testing loop
- Write/edit scripts/main.py
- Run: `python {submit_script} scripts/main.py --holder dev:{{skill_name}}`
- If it fails: read stderr in the output, fix the code, resubmit
- If it succeeds: verify stdout makes sense (e.g. objects detected, arm moved correctly)
- Run at least 2-3 successful executions before declaring done

### Important
- The code sandbox allows robot_sdk imports but NOT `requests` or network libraries
- Your submitted code runs inside the agent server with full robot_sdk access
- Print results to stdout — that's how you verify success
- If the arm enters error state, use rewind: the SDK has `from robot_sdk import rewind`

## Rules
1. Check existing skills first — reuse and chain, don't reinvent
2. Read the SDK docs before writing robot code — don't guess APIs
3. Use rewind as your safety net — every movement is reversible
4. **You MUST test your code before finishing** — submit via /code/submit and verify it works
5. Debug failures — don't just write code and leave
6. Be concise in your reasoning
7. The sim (RoboCasa/ManiSkill) uses the same API — you are testing against the sim

## Before finishing
Once the skill works, run `/simplify` to clean up the code (invoke it via the Skill tool).
Then stop.
"""

SYSTEM_PROMPT_TEST = """\
You are a robotics skill tester for the TidyBot Universe project.

## Your Job
Test the skill **{{skill_name}}** by running it against the robot (or sim) and evaluating results.
You DO NOT write new skill code — you run existing code and analyze outcomes.

## Existing Skills
Skills live in {skills_dir}/. The skill you're testing:
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
1. Read the skill code: {skills_dir}/{{skill_name}}/scripts/main.py
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
graph_meta: dict = {}  # top-level metadata (task_env, task_source, etc.)


def _load_entries():
    """Load entries from graph file into memory."""
    global skill_entries, graph_meta
    data = json.loads(LOCAL_REPOS.read_text())
    if isinstance(data, dict):
        # New format: {"task_env": "...", "entries": [...], ...}
        skill_entries = data.get("entries", [])
        graph_meta = {k: v for k, v in data.items() if k != "entries"}
    else:
        # Legacy format: flat list of entries
        skill_entries = data
        graph_meta = {}


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
_spawn_lock = asyncio.Lock()          # prevents double-spawning in _auto_spawn_ready_skills


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
            "done": "review",
            "confirmed_done": "done",
            "error": "failed",
        }.get(internal, internal)
    if agent_type == "planner":
        # Planner doesn't map to a specific skill hex — uses _planner pseudo-skill
        return {
            "starting": "writing",
            "running": "writing",
            "stopped": "failed",
            "done": "done",
            "error": "failed",
        }.get(internal, internal)
    return {
        "starting": "writing",
        "running": "writing",
        "stopped": "failed",
        "done": "review",
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
                        f"Read the skill code in {SKILLS_DIR}/{skill}/, "
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
                    # Mark as confirmed done (bypasses review mapping)
                    state = agents.get(agent_id)
                    if state:
                        state.status = "confirmed_done"
                    asyncio.create_task(ws_broadcast_status(skill, agent_id, "confirmed_done", "Done"))
                    # Confirm in graph and auto-spawn downstream skills
                    asyncio.create_task(_confirm_skill_done(skill))

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
    global dev_mode
    if agent_type != "planner" and not dev_mode:
        msg = f"[ORCH] Blocked spawn of {agent_type} agent for '{skill}' — still in planning mode. Run /xbot-start first."
        print(msg)
        await ws_broadcast({"type": "error", "message": msg})
        return ""

    # If there's already an active agent for this skill, stop it first
    for aid, a in list(agents.items()):
        if a.skill == skill and a.status in ("starting", "running"):
            await stop_agent(aid)

    agent_id = f"agent-{uuid.uuid4().hex[:8]}"
    state = AgentState(agent_id=agent_id, skill=skill, agent_type=agent_type)
    agents[agent_id] = state

    labels = {"test": "Testing...", "test_writer": "Writing tests...", "dev": "Developing..."}
    await ws_broadcast_status(skill, agent_id, "starting", labels.get(agent_type, "Spawning..."))

    state.task = asyncio.create_task(_run_agent_sdk(state, prompt))

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
            sys.executable, str(test_file),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(SKILLS_DIR / skill),
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
        import traceback; traceback.print_exc()
        _update_entry(skill, {"status": "failed"})
        await broadcast_full_sync()
        return {"passed": False, "success_rate": 0, "total_trials": 0, "stdout": "", "stderr": str(e)}


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
            # Test passed — go to review
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
        _update_entry(skill, {"status": "writing"})
        await broadcast_full_sync()

        desc = entry.get("description", skill) if entry else skill
        prompt = f"Implement the '{skill}' skill: {desc}\n\n## Previous test failure\n{feedback}"
        await spawn_agent(skill, prompt, agent_type="dev")

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


def _get_system_prompt(agent_type: str, skill_name: str = "") -> str:
    """Get system prompt for agent type, populated with live skills list and dependency code."""
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

    # For dev agents: include dependency skill code inline so the agent knows interfaces
    dep_context = ""
    if agent_type == "dev" and skill_name:
        entry = next((e for e in skill_entries if e["name"] == skill_name), None)
        if entry and entry.get("dependencies"):
            dep_sections = []
            for dep_name in entry["dependencies"]:
                dep_dir = SKILLS_DIR / dep_name
                # Read SKILL.md
                skill_md = dep_dir / "SKILL.md"
                skill_md_text = ""
                if skill_md.exists():
                    skill_md_text = skill_md.read_text()[:2000]
                # Read main.py
                main_py = dep_dir / "scripts" / "main.py"
                main_py_text = ""
                if main_py.exists():
                    main_py_text = main_py.read_text()[:3000]
                dep_sections.append(
                    f"### {dep_name}\n"
                    + (f"**SKILL.md:**\n```\n{skill_md_text}\n```\n\n" if skill_md_text else "")
                    + (f"**scripts/main.py:**\n```python\n{main_py_text}\n```\n" if main_py_text else "(no code yet)\n")
                )
            dep_context = (
                "\n\n## Dependency Skills (your skill depends on these — reuse them)\n\n"
                + "\n".join(dep_sections)
            )

    # For planner: also include current tree and task context
    if agent_type == "planner":
        tree_lines = []
        for e in skill_entries:
            deps = e.get("dependencies", [])
            dep_str = f" → depends on: {', '.join(deps)}" if deps else " (leaf)"
            tree_lines.append(f"  - {e['name']}: {e.get('description', '(no description)')}{dep_str}")
        current_tree = "\n".join(tree_lines) if tree_lines else "  (empty — no skills in tree yet)"

        # Build task context from graph metadata
        task_env = graph_meta.get("task_env")
        task_source = graph_meta.get("task_source")
        if task_env:
            task_context = f"**Target task:** `{task_env}`\n"
            if task_source:
                task_context += (
                    f"Read the task source at `{task_source}` to understand:\n"
                    f"- `_get_obj_cfgs()` — what objects start where\n"
                    f"- `_check_success()` — what the success condition is\n"
                    f"- `_setup_kitchen_references()` — which fixtures are involved\n"
                )
            task_context += (
                "\nThe root skill (top of the tree) will be tested automatically by the sim's "
                "`_check_success()`. Sub-skills need custom tests written by the test_writer agent."
            )
        else:
            task_context = "(no specific task — design skills for the goal described by the user)"

        return SYSTEM_PROMPT_PLANNER.format(
            existing_skills=skills_list,
            current_tree=current_tree,
            task_context=task_context,
            http_port=WS_PORT + 1,
            skills_dir=SKILLS_DIR,
        )

    templates = {
        "test": SYSTEM_PROMPT_TEST,
        "test_writer": SYSTEM_PROMPT_TEST_WRITER,
        "dev": SYSTEM_PROMPT_DEV,
    }
    template = templates.get(agent_type, SYSTEM_PROMPT_DEV)
    result = template.format(
        agent_server=AGENT_SERVER,
        existing_skills=skills_list,
        skill_name=skill_name,
        skills_dir=SKILLS_DIR,
        submit_script=Path(__file__).parent / "submit_and_wait.py",
    )
    # Append dependency context for dev agents
    if dep_context:
        result += dep_context
    return result


SESSION_LOG = GRAPH_DIR / "agent_sessions.jsonl"

def _save_session_mapping(state: AgentState, message):
    """Append a line to agent_sessions.jsonl mapping session_id → skill + agent type."""
    import datetime
    entry = {
        "session_id": message.session_id,
        "skill": state.skill,
        "agent_type": state.agent_type,
        "agent_id": state.agent_id,
        "cost_usd": message.total_cost_usd,
        "num_turns": message.num_turns,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    with open(SESSION_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"[SDK] {state.skill}: session {message.session_id} logged to {SESSION_LOG.name}")


async def _run_agent_sdk(state: AgentState, prompt: str):
    """Run an agent using the Claude Agent SDK (ClaudeSDKClient)."""
    options = ClaudeAgentOptions(
        cwd=str(PROJECT_DIR),
        permission_mode="bypassPermissions",
        system_prompt=_get_system_prompt(state.agent_type, state.skill),
        model="claude-sonnet-4-6",
    )

    try:
        print(f"[SDK] {state.skill}: creating ClaudeSDKClient...")
        client = ClaudeSDKClient(options=options)
        state.client = client

        print(f"[SDK] {state.skill}: entering client context...")
        async with client:
            state.status = "running"
            print(f"[SDK] {state.skill}: client ready, sending query...")
            await ws_broadcast_status(state.skill, state.agent_id, "running", "Working...")

            # First query
            await client.query(prompt)
            print(f"[SDK] {state.skill}: query sent, consuming response...")
            await _consume_sdk_response(state, client)
            print(f"[SDK] {state.skill}: response consumed")

    except asyncio.CancelledError:
        state.status = "stopped"
        await ws_broadcast_status(state.skill, state.agent_id, "stopped", "Cancelled")
    except Exception as e:
        state.status = "error"
        err = str(e)[:200]
        print(f"[SDK] {state.skill}: ERROR: {err}")
        import traceback; traceback.print_exc()
        state.log.append(f"ERROR: {err}")
        await ws_broadcast_status(state.skill, state.agent_id, "error", err)
        await ws_broadcast_agent_msg(state.skill, f"Error: {err}", state.agent_type)
    finally:
        if state.status == "running":
            state.status = "done"
            await _handle_agent_done(state)


async def _handle_agent_done(state: AgentState):
    """Handle agent completion — chain to next step in pipeline."""
    if state.agent_type == "dev":
        # Skip if this dev agent was re-spawned inside the test loop (loop manages flow)
        if state.skill in _skills_in_test_loop:
            return

        # Root skill with task_env: run ground-truth mechanical test
        if _is_task_root(state.skill):
            await ws_broadcast_status(state.skill, state.agent_id, "done", "Dev complete — running ground-truth test")
            await ws_broadcast_agent_msg(state.skill, "Dev complete — running mechanical test.", state.agent_type)
            asyncio.create_task(_root_skill_test_loop(state.skill))
            return

        if autonomous_mode:
            # Autonomous: skip review, auto-promote to done and spawn downstream
            await ws_broadcast_status(state.skill, state.agent_id, "done", "Dev complete — auto-promoted (autonomous)")
            await ws_broadcast_agent_msg(state.skill, "Dev complete — auto-promoted to done (autonomous mode).", state.agent_type)
            await _confirm_skill_done(state.skill)
        else:
            # Interactive: go to review, wait for human confirmation
            await ws_broadcast_status(state.skill, state.agent_id, "done", "Dev complete — waiting for review")
            await ws_broadcast_agent_msg(state.skill, "Dev complete — waiting for review.", state.agent_type)
            _update_entry(state.skill, {"status": "review"})
            await broadcast_full_sync()
    elif state.agent_type == "planner":
        # Planner done — user must run /xbot-start to kick off development
        await ws_broadcast_status(state.skill, state.agent_id, "done", "Planning complete — run /xbot-start to begin development")
    else:
        await ws_broadcast_status(state.skill, state.agent_id, "done", "Finished")


async def _confirm_skill_done(skill: str):
    """Confirm a skill as done (after review) and auto-spawn downstream skills."""
    entry = _find_entry(skill)
    if not entry:
        return
    _update_entry(skill, {"status": "done"})
    print(f"[ORCH] Skill '{skill}' confirmed done — checking downstream skills")
    await broadcast_full_sync()
    await _auto_spawn_ready_skills()


async def _auto_spawn_ready_skills() -> list[str]:
    """Find skills whose dependencies are all 'done' and spawn dev pipelines.
    Returns list of skill names that were spawned."""
    async with _spawn_lock:
        done_skills = {e["name"] for e in skill_entries if e.get("status") == "done"}
        # Skills already being worked on
        active_skills = {a.skill for a in agents.values() if a.status in ("starting", "running")}

        spawned = []
        for entry in skill_entries:
            name = entry["name"]
            status = entry.get("status", "planned")
            deps = entry.get("dependencies", [])

            # Only auto-spawn skills that are "planned" or "failed" (retriable)
            if status not in ("planned", "failed"):
                continue
            # Skip if already has an active agent
            if name in active_skills:
                continue
            # Check all dependencies are done
            if deps and not all(d in done_skills for d in deps):
                continue

            # All preconditions met — spawn pipeline
            desc = entry.get("description", name)
            print(f"[ORCH] Auto-spawning pipeline for '{name}' (deps satisfied: {deps})")
            _update_entry(name, {"status": "writing"})
            await spawn_skill_pipeline(name, f"Implement the '{name}' skill: {desc}")
            spawned.append(name)

        if spawned:
            await ws_broadcast({
                "type": "auto_spawn",
                "skills": spawned,
                "message": f"Auto-started {len(spawned)} skill(s): {', '.join(spawned)}",
            })

        return spawned


async def _consume_sdk_response(state: AgentState, client: ClaudeSDKClient):
    """Consume the async iterator from a ClaudeSDKClient and broadcast to dashboard."""
    async for message in client.receive_response():
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
                state.log.append(f"ERROR: {text}")
                await ws_broadcast_agent_msg(state.skill, f"ERROR: {text}", state.agent_type)
                state.status = "error"
                await ws_broadcast_status(state.skill, state.agent_id, "error", text[:200])
            else:
                # Log unknown subtypes briefly for debugging
                text = data.get("message") or data.get("description") or subtype
                print(f"[SDK] {state.skill}: {subtype}: {text}")

        elif isinstance(message, AssistantMessage):
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
            # Save session→skill mapping for later lookup
            if message.session_id:
                _save_session_mapping(state, message)
            cost = f"${message.total_cost_usd:.4f}" if message.total_cost_usd else "?"
            # Detect zero-cost / zero-turn completions as errors (e.g. credit/auth failures)
            if not message.total_cost_usd and message.num_turns == 0:
                err_text = " | ".join(state.log) if state.log else "Agent produced no output"
                print(f"[SDK] {state.skill}: ERROR (zero-cost completion): {err_text}")
                state.status = "error"
                await ws_broadcast_status(state.skill, state.agent_id, "error", err_text[:200])
                await ws_broadcast_agent_msg(state.skill, f"ERROR: {err_text}", state.agent_type)
            else:
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
    else:
        print(f"[ORCH] inject: agent {agent_id} not running or no SDK client")


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

    try:
        if method == "POST" and path == "/plan":
            params = json.loads(body)
            aid = await spawn_agent("_planner", params["prompt"], agent_type="planner")
            response_body = json.dumps({"agent_id": aid})

        elif method == "POST" and path == "/xbot-start":
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
            # If status was set to "done", check for newly unblocked skills
            if params.get("status") == "done":
                await _auto_spawn_ready_skills()
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
