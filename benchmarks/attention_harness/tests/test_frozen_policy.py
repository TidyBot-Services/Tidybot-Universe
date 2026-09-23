from __future__ import annotations

from benchmarks.attention_harness.frozen_policy import FROZEN_POLICIES, get_frozen_policy


def test_each_task_has_a_valid_frozen_public_policy() -> None:
    assert set(FROZEN_POLICIES) == {"cube_lift", "cube_stack"}
    for task_id in sorted(FROZEN_POLICIES):
        policy = get_frozen_policy(task_id)
        source = policy.source()
        assert "from robot_sdk import" in source
        assert "cube_pos" not in source
        assert len(policy.sha256()) == 64


def test_unknown_task_has_no_frozen_policy() -> None:
    try:
        get_frozen_policy("unknown")
    except ValueError as exc:
        assert "no frozen public policy" in str(exc)
    else:
        raise AssertionError("unknown task unexpectedly had a frozen policy")
