"""Compatibility imports for the pre-shared-SDK AttentionHarness API."""

from tidybot_sdk import ActionResult, RobotBackend, copy_observation

# Kept temporarily so external development scripts from the first native
# harness revision continue to import. New code uses the unambiguous names in
# ``tidybot_sdk`` directly.
ServiceStep = ActionResult
RobotService = RobotBackend

__all__ = [
    "RobotBackend",
    "RobotService",
    "ServiceStep",
    "copy_observation",
]
