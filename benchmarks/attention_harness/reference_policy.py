"""Request the service-owned diagnostic policy without receiving oracle state."""

from __future__ import annotations

from .robosuite_adapter import RobosuiteRobotBackend


class EpisodeTimeout(TimeoutError):
    pass


def run_reference_policy(
    adapter: RobosuiteRobotBackend, timeout_seconds: float = 60.0
) -> None:
    try:
        adapter.run_reference_policy(timeout_seconds)
    except TimeoutError as exc:
        raise EpisodeTimeout(str(exc)) from exc
