"""Negative-control policy for the Dev → Harness → Eval connection smoke.

It intentionally issues no robot actions. Native task failure is the expected
outcome; this policy must never be used as evidence of task-solving ability.
"""


def run(sdk, context):
    del sdk, context
