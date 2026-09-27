"""Cooperative simulator stop signal shared by runners and action backends."""

from __future__ import annotations

from typing import Callable


class EmergencyInterrupt(RuntimeError):
    pass


def raise_if_interrupted(check: Callable[[], bool] | None) -> None:
    if check is not None and check():
        raise EmergencyInterrupt("operator requested emergency interrupt")
