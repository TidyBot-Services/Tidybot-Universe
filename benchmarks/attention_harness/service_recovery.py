"""Validate both suites' dedicated Service process-group cleanup receipts."""

from __future__ import annotations

from typing import Any


def service_stop_confirmed(stop: Any, suite: str) -> bool:
    if not isinstance(stop, dict):
        return False
    if suite == "robosuite":
        return stop.get("leader_reaped") is True and stop.get("process_group_gone") is True
    if suite == "robocasa":
        services = stop.get("services")
        if not isinstance(services, dict) or not services:
            return False
        expected = {"simulator", "agent"}
        if set(services) != expected and not (
                stop.get("reason") == "operator_cancel" and set(services).issubset(expected)):
            return False
        return all(
            isinstance(services.get(name), dict)
            and services[name].get("leader_reaped") is True
            and services[name].get("process_group_gone") is True
            for name in services
        )
    return False
