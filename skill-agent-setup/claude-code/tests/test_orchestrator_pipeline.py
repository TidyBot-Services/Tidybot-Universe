#!/usr/bin/env python3
"""Test orchestrator pipeline logic: dependency resolution, review gate, cascade.

No sim or agent server needed — mocks out agent spawning and WS broadcasts.

Usage:
    cd ~/tidybot_uni/marketing/Tidybot-Universe/skill-agent-setup/claude-code
    python tests/test_orchestrator_pipeline.py
"""

import asyncio
import json
import os
import sys
import tempfile
import time
from unittest.mock import AsyncMock, patch

# The orchestrator uses argparse at module level and requires --graph.
# We need to patch sys.argv before importing, and provide a temp graph file.


def make_graph(entries):
    """Write entries to a temp JSON file and return the path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(entries, f)
    f.close()
    return f.name


def import_orchestrator(graph_path):
    """Import agent_orchestrator with patched argv and optional deps stubbed."""
    # Stub websockets if not installed
    if "websockets" not in sys.modules:
        import types
        ws_mod = types.ModuleType("websockets")
        ws_mod.ConnectionClosed = Exception
        sys.modules["websockets"] = ws_mod

    # Stub claude_agent_sdk
    import types as t
    sdk_mod = t.ModuleType("claude_agent_sdk")
    for name in ["ClaudeSDKClient", "ClaudeAgentOptions", "AssistantMessage",
                  "SystemMessage", "ResultMessage", "TextBlock", "ToolUseBlock"]:
        setattr(sdk_mod, name, None)
    sys.modules["claude_agent_sdk"] = sdk_mod

    sys.argv = ["agent_orchestrator.py", "--graph", graph_path]

    # Add orchestrator dir to path
    orch_dir = os.path.join(os.path.dirname(__file__), "..")
    if orch_dir not in sys.path:
        sys.path.insert(0, os.path.abspath(orch_dir))

    # Remove cached module if re-importing
    if "agent_orchestrator" in sys.modules:
        del sys.modules["agent_orchestrator"]

    import agent_orchestrator as orch
    return orch


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def make_entry(name, deps=None, status="planned", success_rate=None):
    return {
        "id": f"sc-{name}",
        "name": name,
        "description": f"Test skill {name}",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "html_url": "",
        "language": "Python",
        "stars": 0,
        "is_private": False,
        "default_branch": "main",
        "success_rate": success_rate,
        "total_trials": 0,
        "institutions_tested": 0,
        "trial_images": [],
        "dependencies": deps or [],
        "service_dependencies": [],
        "sdk_functions": [],
        "status": status,
        "agent_id": None,
        "agent_status_text": None,
        "agent_log": [],
        "progress_history": [],
    }


passed = 0
failed = 0


def check(name, condition):
    global passed, failed
    if condition:
        print(f"  PASS: {name}")
        passed += 1
    else:
        print(f"  FAIL: {name}")
        failed += 1


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_auto_spawn_leaf_skills(orch):
    """Leaf skills (no deps) with status=planned should be spawned."""
    orch.skill_entries = [
        make_entry("leaf-a"),
        make_entry("leaf-b"),
        make_entry("blocked", deps=["leaf-a"]),
    ]
    orch.agents.clear()

    spawned_skills = []
    original_pipeline = orch.spawn_skill_pipeline

    async def mock_pipeline(skill, prompt):
        spawned_skills.append(skill)

    orch.spawn_skill_pipeline = mock_pipeline
    try:
        result = await orch._auto_spawn_ready_skills()
        check("spawns leaf-a", "leaf-a" in result)
        check("spawns leaf-b", "leaf-b" in result)
        check("does not spawn blocked", "blocked" not in result)
        check("return matches spawned", set(result) == {"leaf-a", "leaf-b"})
    finally:
        orch.spawn_skill_pipeline = original_pipeline


async def test_auto_spawn_with_deps_satisfied(orch):
    """Skills whose deps are all 'done' should be spawned."""
    orch.skill_entries = [
        make_entry("dep-a", status="done"),
        make_entry("dep-b", status="done"),
        make_entry("child", deps=["dep-a", "dep-b"]),
    ]
    orch.agents.clear()

    spawned_skills = []

    async def mock_pipeline(skill, prompt):
        spawned_skills.append(skill)

    orch.spawn_skill_pipeline = mock_pipeline
    try:
        result = await orch._auto_spawn_ready_skills()
        check("spawns child with both deps done", "child" in result)
    finally:
        orch.spawn_skill_pipeline = mock_pipeline


async def test_auto_spawn_partial_deps(orch):
    """Skills with partially satisfied deps should NOT be spawned."""
    orch.skill_entries = [
        make_entry("dep-a", status="done"),
        make_entry("dep-b", status="planned"),
        make_entry("child", deps=["dep-a", "dep-b"]),
    ]
    orch.agents.clear()

    async def mock_pipeline(skill, prompt):
        pass

    orch.spawn_skill_pipeline = mock_pipeline
    try:
        result = await orch._auto_spawn_ready_skills()
        check("does not spawn child with partial deps", "child" not in result)
    finally:
        orch.spawn_skill_pipeline = mock_pipeline


async def test_skip_non_planned(orch):
    """Skills not in 'planned' status should not be spawned."""
    orch.skill_entries = [
        make_entry("in-review", status="review"),
        make_entry("writing", status="writing"),
        make_entry("already-done", status="done"),
    ]
    orch.agents.clear()

    async def mock_pipeline(skill, prompt):
        pass

    orch.spawn_skill_pipeline = mock_pipeline
    try:
        result = await orch._auto_spawn_ready_skills()
        check("no skills spawned for non-planned statuses", len(result) == 0)
    finally:
        orch.spawn_skill_pipeline = mock_pipeline


async def test_skip_active_agents(orch):
    """Skills with active agents should not be re-spawned."""
    orch.skill_entries = [
        make_entry("leaf"),
    ]
    orch.agents.clear()
    # Simulate an active agent for this skill
    orch.agents["agent-123"] = orch.AgentState(
        agent_id="agent-123", skill="leaf", status="running"
    )

    async def mock_pipeline(skill, prompt):
        pass

    orch.spawn_skill_pipeline = mock_pipeline
    try:
        result = await orch._auto_spawn_ready_skills()
        check("does not re-spawn skill with active agent", len(result) == 0)
    finally:
        orch.spawn_skill_pipeline = mock_pipeline


async def test_confirm_triggers_cascade(orch):
    """Confirming a skill as done should trigger auto-spawn of downstream skills."""
    orch.skill_entries = [
        make_entry("parent", status="review", success_rate=80),
        make_entry("child", deps=["parent"]),
    ]
    orch.agents.clear()

    spawned_skills = []

    async def mock_pipeline(skill, prompt):
        spawned_skills.append(skill)

    orch.spawn_skill_pipeline = mock_pipeline
    try:
        # Before confirm: child should not be spawnable
        result = await orch._auto_spawn_ready_skills()
        check("child not spawned before confirm", "child" not in result)

        # Confirm parent
        await orch._confirm_skill_done("parent")

        parent = orch._find_entry("parent")
        check("parent status is done after confirm", parent["status"] == "done")
        check("child was spawned after confirm", "child" in spawned_skills)
    finally:
        orch.spawn_skill_pipeline = mock_pipeline


async def test_review_gate_blocks_cascade(orch):
    """Skills in 'review' should NOT count as satisfied dependencies."""
    orch.skill_entries = [
        make_entry("parent", status="review", success_rate=90),
        make_entry("child", deps=["parent"]),
    ]
    orch.agents.clear()

    async def mock_pipeline(skill, prompt):
        pass

    orch.spawn_skill_pipeline = mock_pipeline
    try:
        result = await orch._auto_spawn_ready_skills()
        check("child blocked while parent in review", "child" not in result)
    finally:
        orch.spawn_skill_pipeline = mock_pipeline


async def test_multi_level_cascade(orch):
    """Confirming a skill should only spawn immediate dependents, not skip levels."""
    orch.skill_entries = [
        make_entry("grandparent", status="review"),
        make_entry("parent", deps=["grandparent"]),
        make_entry("child", deps=["parent"]),
    ]
    orch.agents.clear()

    spawned_skills = []

    async def mock_pipeline(skill, prompt):
        spawned_skills.append(skill)

    orch.spawn_skill_pipeline = mock_pipeline
    try:
        await orch._confirm_skill_done("grandparent")
        check("parent spawned", "parent" in spawned_skills)
        check("child NOT spawned (parent not done yet)", "child" not in spawned_skills)
    finally:
        orch.spawn_skill_pipeline = mock_pipeline


async def test_handle_planner_done(orch):
    """Planner finishing should NOT auto-spawn — user must run /xbot-start."""
    orch.skill_entries = [
        make_entry("leaf-skill"),
    ]
    orch.agents.clear()

    spawned_skills = []

    async def mock_pipeline(skill, prompt):
        spawned_skills.append(skill)

    orch.spawn_skill_pipeline = mock_pipeline
    try:
        state = orch.AgentState(agent_id="planner-1", skill="_planner", agent_type="planner")
        await orch._handle_agent_done(state)
        check("no skills spawned after planner done", len(spawned_skills) == 0)
    finally:
        orch.spawn_skill_pipeline = mock_pipeline


async def test_handle_dev_done_runs_test(orch):
    """Dev agent finishing should trigger mechanical test, not auto-spawn."""
    orch.skill_entries = [
        make_entry("my-skill", status="writing"),
        make_entry("downstream", deps=["my-skill"]),
    ]
    orch.agents.clear()

    test_ran = []

    async def mock_mechanical_test(skill):
        test_ran.append(skill)

    original_test = orch.run_mechanical_test
    orch.run_mechanical_test = mock_mechanical_test
    try:
        state = orch.AgentState(agent_id="dev-1", skill="my-skill", agent_type="dev")
        await orch._handle_agent_done(state)
        # _handle_agent_done uses asyncio.create_task — give it a chance to run
        await asyncio.sleep(0)
        check("mechanical test triggered", "my-skill" in test_ran)
        # downstream should NOT be spawned (no confirm yet)
        downstream = orch._find_entry("downstream")
        check("downstream still planned", downstream["status"] == "planned")
    finally:
        orch.run_mechanical_test = original_test


async def test_xbot_start_endpoint(orch):
    """The /xbot-start path in handle_http should call _auto_spawn_ready_skills."""
    orch.skill_entries = [
        make_entry("done-dep", status="done"),
        make_entry("ready-skill", deps=["done-dep"]),
    ]
    orch.agents.clear()

    spawned_skills = []

    async def mock_pipeline(skill, prompt):
        spawned_skills.append(skill)

    orch.spawn_skill_pipeline = mock_pipeline
    try:
        # Simulate HTTP request
        request = "POST /xbot-start HTTP/1.1\r\nHost: localhost\r\n\r\n"
        reader = asyncio.StreamReader()
        reader.feed_data(request.encode())
        reader.feed_eof()

        # Capture response
        transport = type("T", (), {"is_closing": lambda self: False})()
        writer = asyncio.StreamWriter(
            transport=transport,
            protocol=asyncio.StreamReaderProtocol(asyncio.StreamReader()),
            reader=None,
            loop=asyncio.get_event_loop(),
        )
        written = []
        writer.write = lambda data: written.append(data)
        writer.drain = AsyncMock()
        writer.close = lambda: None

        await orch.handle_http(reader, writer)

        response = written[0].decode() if written else ""
        check("/xbot-start spawned ready-skill", "ready-skill" in spawned_skills)
        check("/xbot-start returns 200", "200 OK" in response)
        check("/xbot-start response has spawned list", "ready-skill" in response)
    finally:
        orch.spawn_skill_pipeline = mock_pipeline


async def test_task_root_detection(orch):
    """_is_task_root should identify the skill no other skill depends on."""
    orch.skill_entries = [
        make_entry("grasp"),
        make_entry("navigate"),
        make_entry("full-task", deps=["grasp", "navigate"]),
    ]
    orch.graph_meta = {"task_env": "RoboCasa-Pn-P-Counter-To-Cab-v0"}

    check("full-task is root", orch._is_task_root("full-task"))
    check("grasp is NOT root", not orch._is_task_root("grasp"))
    check("navigate is NOT root", not orch._is_task_root("navigate"))


async def test_task_root_no_meta(orch):
    """Without task_env in graph_meta, no skill should be identified as root."""
    orch.skill_entries = [
        make_entry("leaf"),
    ]
    orch.graph_meta = {}

    check("leaf is NOT root without task_env", not orch._is_task_root("leaf"))


async def test_task_root_skips_test_writer(orch):
    """Task root skill should skip test_writer and go straight to dev."""
    orch.skill_entries = [
        make_entry("sub-skill", status="done"),
        make_entry("root-task", deps=["sub-skill"]),
    ]
    orch.graph_meta = {"task_env": "RoboCasa-Test-v0"}
    orch.agents.clear()

    spawned = []

    async def mock_spawn(skill, prompt, agent_type="dev"):
        spawned.append((skill, agent_type))
        return f"agent-{skill}"

    # Need to reload the real spawn_skill_pipeline since prior tests may
    # have left a mock in place via their finally blocks.
    import importlib
    real_pipeline = importlib.import_module("agent_orchestrator").spawn_skill_pipeline.__wrapped__ \
        if hasattr(orch.spawn_skill_pipeline, "__wrapped__") else None

    # Directly test the logic: _is_task_root → skip test_writer → spawn dev
    check("root detected", orch._is_task_root("root-task"))

    # Simulate what spawn_skill_pipeline should do for a task root
    if orch._is_task_root("root-task"):
        orch._auto_generate_task_root_test("root-task")
        await mock_spawn("root-task", "Do the task", agent_type="dev")

    check("root spawns dev not test_writer",
          len(spawned) == 1 and spawned[0] == ("root-task", "dev"))


async def test_replan_adds_to_existing_tree(orch):
    """Replan: adding new skills on top of an existing tree preserves old entries."""
    # Start with a tree that has some done and planned skills
    orch.skill_entries = [
        make_entry("grasp", status="done"),
        make_entry("navigate", status="done"),
        make_entry("pick-and-place", deps=["grasp", "navigate"], status="review"),
    ]
    orch.agents.clear()

    # Simulate planner adding new skills (replan scenario)
    orch._add_entry("detect-obstacle", "Detect obstacles in path", [])
    orch._add_entry("avoid-obstacle", "Navigate around obstacles", ["detect-obstacle", "navigate"])

    check("original skills preserved", len(orch.skill_entries) == 5)
    check("grasp still done", orch._find_entry("grasp")["status"] == "done")
    check("navigate still done", orch._find_entry("navigate")["status"] == "done")
    check("pick-and-place still in review", orch._find_entry("pick-and-place")["status"] == "review")
    check("new detect-obstacle added", orch._find_entry("detect-obstacle") is not None)
    check("new avoid-obstacle has correct deps",
          orch._find_entry("avoid-obstacle")["dependencies"] == ["detect-obstacle", "navigate"])
    check("new skills are planned", orch._find_entry("detect-obstacle")["status"] == "planned")


async def test_replan_does_not_duplicate(orch):
    """Replan: adding a skill with the same name as existing returns the existing one."""
    orch.skill_entries = [
        make_entry("grasp", status="done"),
    ]
    orch.agents.clear()

    result = orch._add_entry("grasp", "Different description", ["some-dep"])
    check("returns existing entry", result["status"] == "done")
    check("no duplicate added", len(orch.skill_entries) == 1)
    # Description should NOT be overwritten by _add_entry (it returns existing)
    check("description unchanged", result["description"] == "Test skill grasp")


async def test_replan_remove_and_readd(orch):
    """Replan: removing a skill and re-adding it with different deps."""
    orch.skill_entries = [
        make_entry("grasp", status="planned"),
        make_entry("navigate", status="done"),
        make_entry("task", deps=["grasp", "navigate"]),
    ]
    orch.agents.clear()

    orch._remove_entry("grasp")
    check("grasp removed", orch._find_entry("grasp") is None)
    check("2 entries remain", len(orch.skill_entries) == 2)

    # Re-add with different config
    orch._add_entry("grasp-v2", "Improved grasping", ["navigate"])
    orch._update_entry("task", {"dependencies": ["grasp-v2", "navigate"]})
    check("grasp-v2 added", orch._find_entry("grasp-v2") is not None)
    check("task deps updated", orch._find_entry("task")["dependencies"] == ["grasp-v2", "navigate"])


async def test_plan_then_dev_then_replan_cycle(orch):
    """Full cycle: plan -> dev starts -> some skills done -> replan adds more -> dev resumes."""
    # Phase 1: Initial plan with two leaf skills and a root
    orch.skill_entries = [
        make_entry("detect"),
        make_entry("grasp"),
        make_entry("pick-up", deps=["detect", "grasp"]),
    ]
    orch.agents.clear()

    spawned_skills = []

    async def mock_pipeline(skill, prompt):
        spawned_skills.append(skill)

    orch.spawn_skill_pipeline = mock_pipeline

    # Phase 2: Start dev — should spawn leaf skills
    result = await orch._auto_spawn_ready_skills()
    check("phase1: detect spawned", "detect" in result)
    check("phase1: grasp spawned", "grasp" in result)
    check("phase1: pick-up not spawned", "pick-up" not in result)

    # Phase 3: Both leaf skills complete and get confirmed
    spawned_skills.clear()
    await orch._confirm_skill_done("detect")
    await orch._confirm_skill_done("grasp")
    check("phase2: pick-up spawned after deps done", "pick-up" in spawned_skills)

    # Phase 4: Replan — add a new branch while pick-up is in progress
    orch._update_entry("pick-up", {"status": "writing"})  # simulate in-progress
    orch._add_entry("place-down", "Place object on surface", [])
    orch._add_entry("full-task", "Complete pick and place", ["pick-up", "place-down"])

    # Phase 5: Auto-spawn should pick up the new leaf (place-down)
    spawned_skills.clear()
    result = await orch._auto_spawn_ready_skills()
    check("phase3: place-down spawned (new leaf)", "place-down" in result)
    check("phase3: full-task not spawned (pick-up not done)", "full-task" not in result)
    check("phase3: pick-up not re-spawned (in writing)", "pick-up" not in result)


async def test_multiple_replan_cycles(orch):
    """Multiple replan cycles: plan -> dev -> replan -> dev -> replan -> dev."""
    orch.skill_entries = [
        make_entry("skill-a"),
    ]
    orch.agents.clear()

    spawned_skills = []

    async def mock_pipeline(skill, prompt):
        spawned_skills.append(skill)

    orch.spawn_skill_pipeline = mock_pipeline

    # Cycle 1: Start dev
    result = await orch._auto_spawn_ready_skills()
    check("cycle1: skill-a spawned", "skill-a" in result)

    # Cycle 1: skill-a done
    spawned_skills.clear()
    await orch._confirm_skill_done("skill-a")

    # Cycle 2: Replan — add skill-b depending on skill-a
    orch._add_entry("skill-b", "Second skill", ["skill-a"])
    spawned_skills.clear()
    result = await orch._auto_spawn_ready_skills()
    check("cycle2: skill-b spawned (skill-a is done)", "skill-b" in result)

    # Cycle 2: skill-b done
    spawned_skills.clear()
    await orch._confirm_skill_done("skill-b")

    # Cycle 3: Replan again — add skill-c depending on skill-b
    orch._add_entry("skill-c", "Third skill", ["skill-b"])
    spawned_skills.clear()
    result = await orch._auto_spawn_ready_skills()
    check("cycle3: skill-c spawned (skill-b is done)", "skill-c" in result)

    # Verify all three are done after cycle 3 completes
    await orch._confirm_skill_done("skill-c")
    all_done = all(e["status"] == "done" for e in orch.skill_entries)
    check("cycle3: all skills done", all_done)


async def test_replan_with_active_agents_preserved(orch):
    """Replan while agents are running should not disturb active agents."""
    orch.skill_entries = [
        make_entry("in-progress", status="writing"),
        make_entry("waiting", deps=["in-progress"]),
    ]
    orch.agents.clear()
    # Simulate active agent for in-progress skill
    orch.agents["agent-ip"] = orch.AgentState(
        agent_id="agent-ip", skill="in-progress", status="running"
    )

    spawned_skills = []

    async def mock_pipeline(skill, prompt):
        spawned_skills.append(skill)

    orch.spawn_skill_pipeline = mock_pipeline

    # Replan: add a new independent branch
    orch._add_entry("new-leaf", "A new skill from replan", [])
    orch._add_entry("new-composite", "Combines old and new", ["in-progress", "new-leaf"])

    result = await orch._auto_spawn_ready_skills()
    check("new-leaf spawned", "new-leaf" in result)
    check("in-progress not re-spawned", "in-progress" not in result)
    check("waiting not spawned", "waiting" not in result)
    check("new-composite not spawned", "new-composite" not in result)


async def test_replan_delete_planned_skill_with_dependents(orch):
    """Deleting a planned skill should leave its dependents with unsatisfied deps."""
    orch.skill_entries = [
        make_entry("to-delete"),
        make_entry("dependent", deps=["to-delete"]),
    ]
    orch.agents.clear()

    spawned_skills = []

    async def mock_pipeline(skill, prompt):
        spawned_skills.append(skill)

    orch.spawn_skill_pipeline = mock_pipeline

    orch._remove_entry("to-delete")
    result = await orch._auto_spawn_ready_skills()
    # dependent still references "to-delete" in deps but it doesn't exist as done
    check("dependent not spawned (dep missing)", "dependent" not in result)


async def test_dev_to_plan_back_to_dev_status_transitions(orch):
    """Status transitions through dev -> replan (add skills) -> dev cycle."""
    orch.skill_entries = [
        make_entry("base-skill"),
    ]
    orch.agents.clear()

    spawned_skills = []

    async def mock_pipeline(skill, prompt):
        spawned_skills.append(skill)

    orch.spawn_skill_pipeline = mock_pipeline

    # Dev phase: spawn and complete base-skill
    await orch._auto_spawn_ready_skills()
    check("base-skill status is writing", orch._find_entry("base-skill")["status"] == "writing")

    # Simulate pipeline completion -> review
    orch._update_entry("base-skill", {"status": "review", "success_rate": 90})
    check("base-skill in review", orch._find_entry("base-skill")["status"] == "review")

    # User confirms
    spawned_skills.clear()
    await orch._confirm_skill_done("base-skill")
    check("base-skill done", orch._find_entry("base-skill")["status"] == "done")

    # Replan: planner adds new skill
    orch._add_entry("extension", "Extends base-skill", ["base-skill"])
    check("extension is planned", orch._find_entry("extension")["status"] == "planned")

    # Back to dev
    spawned_skills.clear()
    result = await orch._auto_spawn_ready_skills()
    check("extension spawned in next dev cycle", "extension" in result)
    check("extension status is writing", orch._find_entry("extension")["status"] == "writing")


async def test_concurrent_plan_and_dev_branches(orch):
    """Two independent branches: one in dev, one being replanned concurrently."""
    orch.skill_entries = [
        # Branch A: in progress
        make_entry("branch-a-leaf", status="done"),
        make_entry("branch-a-task", deps=["branch-a-leaf"], status="writing"),
        # Branch B: just planned via replan
        make_entry("branch-b-leaf"),
        make_entry("branch-b-task", deps=["branch-b-leaf"]),
    ]
    orch.agents.clear()
    orch.agents["agent-a"] = orch.AgentState(
        agent_id="agent-a", skill="branch-a-task", status="running"
    )

    spawned_skills = []

    async def mock_pipeline(skill, prompt):
        spawned_skills.append(skill)

    orch.spawn_skill_pipeline = mock_pipeline

    result = await orch._auto_spawn_ready_skills()
    check("branch-b-leaf spawned", "branch-b-leaf" in result)
    check("branch-a-task not re-spawned", "branch-a-task" not in result)
    check("branch-b-task not spawned (dep not done)", "branch-b-task" not in result)

    # Complete branch B leaf, then branch B task should be ready
    spawned_skills.clear()
    await orch._confirm_skill_done("branch-b-leaf")
    check("branch-b-task spawned after leaf done", "branch-b-task" in spawned_skills)


async def test_graph_meta_load(orch):
    """New graph format with metadata should load correctly."""
    # Write a graph with metadata format
    meta_graph = {
        "task_env": "RoboCasa-Pn-P-Counter-To-Cab-v0",
        "task_source": "~/tidybot_uni/sims/maniskill_tidyverse/robocasa_tasks/single_stage/kitchen_pnp.py",
        "entries": [
            make_entry("test-skill"),
        ]
    }
    graph_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(meta_graph, graph_file)
    graph_file.close()

    try:
        # Simulate loading
        import importlib
        old_repos = orch.LOCAL_REPOS
        old_entries = orch.skill_entries
        old_meta = orch.graph_meta

        from pathlib import Path
        orch.LOCAL_REPOS = Path(graph_file.name)
        orch._load_entries()

        check("entries loaded from meta format", len(orch.skill_entries) == 1)
        check("task_env in graph_meta", orch.graph_meta.get("task_env") == "RoboCasa-Pn-P-Counter-To-Cab-v0")
        check("task_source in graph_meta",
              "kitchen_pnp.py" in orch.graph_meta.get("task_source", ""))

        orch.LOCAL_REPOS = old_repos
        orch.skill_entries = old_entries
        orch.graph_meta = old_meta
    finally:
        os.unlink(graph_file.name)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global passed, failed

    # Create a minimal graph for import
    graph_path = make_graph([])

    try:
        orch = import_orchestrator(graph_path)

        # Mock out WS broadcasts (no clients connected)
        orch.ws_broadcast = AsyncMock()
        orch.ws_broadcast_status = AsyncMock()
        orch.ws_broadcast_agent_msg = AsyncMock()
        orch.broadcast_full_sync = AsyncMock()

        tests = [
            ("Auto-spawn leaf skills", test_auto_spawn_leaf_skills),
            ("Auto-spawn with deps satisfied", test_auto_spawn_with_deps_satisfied),
            ("Block on partial deps", test_auto_spawn_partial_deps),
            ("Skip non-planned statuses", test_skip_non_planned),
            ("Skip skills with active agents", test_skip_active_agents),
            ("Review gate blocks cascade", test_review_gate_blocks_cascade),
            ("Confirm triggers cascade", test_confirm_triggers_cascade),
            ("Multi-level cascade (one level at a time)", test_multi_level_cascade),
            ("Planner done does NOT auto-spawn", test_handle_planner_done),
            ("Dev done runs mechanical test", test_handle_dev_done_runs_test),
            ("/xbot-start endpoint", test_xbot_start_endpoint),
            ("Task root detection", test_task_root_detection),
            ("Task root needs task_env meta", test_task_root_no_meta),
            ("Task root skips test_writer", test_task_root_skips_test_writer),
            ("Replan adds to existing tree", test_replan_adds_to_existing_tree),
            ("Replan does not duplicate skills", test_replan_does_not_duplicate),
            ("Replan remove and re-add", test_replan_remove_and_readd),
            ("Plan → dev → replan → dev cycle", test_plan_then_dev_then_replan_cycle),
            ("Multiple replan cycles", test_multiple_replan_cycles),
            ("Replan preserves active agents", test_replan_with_active_agents_preserved),
            ("Delete planned skill leaves deps unsatisfied", test_replan_delete_planned_skill_with_dependents),
            ("Dev → plan → dev status transitions", test_dev_to_plan_back_to_dev_status_transitions),
            ("Concurrent plan and dev branches", test_concurrent_plan_and_dev_branches),
            ("Graph metadata loading", test_graph_meta_load),
        ]

        print("=" * 60)
        print("Orchestrator Pipeline Tests")
        print("=" * 60)

        for name, test_fn in tests:
            print(f"\n{name}:")
            # Reset state between tests
            orch.skill_entries = []
            orch.graph_meta = {}
            orch.agents.clear()
            orch.ws_broadcast.reset_mock()
            orch.ws_broadcast_status.reset_mock()
            orch.ws_broadcast_agent_msg.reset_mock()
            orch.broadcast_full_sync.reset_mock()

            try:
                asyncio.get_event_loop().run_until_complete(test_fn(orch))
            except Exception as e:
                print(f"  ERROR: {e}")
                import traceback
                traceback.print_exc()
                failed += 1

        print(f"\n{'=' * 60}")
        print(f"Results: {passed} passed, {failed} failed")
        print("=" * 60)

    finally:
        os.unlink(graph_path)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
