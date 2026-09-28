"""Public progress checks must be grounded in SDK before/after observations."""

from benchmarks.attention_harness.public_progress import public_lift_progress_event


def _events(final_z: float):
    return [
        {"event_id": "sdk-0", "operation": "find_objects", "status": "completed",
         "result": [{"name": "cube", "position": [0.0, 0.0, 0.80]}]},
        {"event_id": "sdk-1", "operation": "close", "status": "completed"},
        {"event_id": "sdk-2", "operation": "move_to_position", "status": "completed"},
        {"event_id": "sdk-3", "operation": "find_objects", "status": "completed",
         "result": [{"name": "cube", "position": [0.0, 0.0, final_z]}]},
    ]


def test_requires_public_post_lift_observation_and_real_guidance():
    assert public_lift_progress_event(_events(0.80), attention_input={}) is None
    assert public_lift_progress_event(_events(0.80)[:-1],
                                      attention_input={"advisor_guidance": "try again"}) is None
    assert public_lift_progress_event(_events(0.87),
                                      attention_input={"advisor_guidance": "try again"}) is None
    failed = public_lift_progress_event(_events(0.80),
                                        attention_input={"advisor_guidance": "try again"})
    assert failed["status"] == "failed"
    assert failed["result"] == {"before_z_m": 0.8, "after_z_m": 0.8, "rise_m": 0.0}
    assert failed["arguments"]["before_sdk_event"] == "sdk-0"
    assert failed["arguments"]["after_sdk_event"] == "sdk-3"
