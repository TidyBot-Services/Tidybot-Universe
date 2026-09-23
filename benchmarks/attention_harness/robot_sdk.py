"""Compatibility wrapper for the shared TidyBot SDK core.

AttentionHarness no longer owns an SDK implementation. Keep this import path
for the first native-harness revision while callers migrate to ``tidybot_sdk``.
"""

from tidybot_sdk import Arm, Gripper, Sensors, TidyBotSDK

NativeRobotSDK = TidyBotSDK

__all__ = ["Arm", "Gripper", "NativeRobotSDK", "Sensors", "TidyBotSDK"]
