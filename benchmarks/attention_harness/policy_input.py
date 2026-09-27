"""Bounded, agent-visible inputs passed between Attention attempts."""

from typing import Any


def public_attention_input(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    allowed = {"demo_prior", "advisor_guidance", "inspected_trace", "memory_ids_to_use", "memory_guidance", "approval_granted"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("attention input contains unsupported fields")
    if any(not isinstance(value.get(key), str) or len(value[key]) > 4000
           for key in ("demo_prior", "advisor_guidance", "inspected_trace") if key in value):
        raise ValueError("attention input text must be bounded public strings")
    ids = value.get("memory_ids_to_use", [])
    if not isinstance(ids, list) or len(ids) > 8 or any(not isinstance(item, str) or not item for item in ids):
        raise ValueError("attention memory selection must be a bounded ID list")
    if "approval_granted" in value and not isinstance(value["approval_granted"], bool):
        raise ValueError("approval_granted must be an explicit boolean")
    guidance = value.get("memory_guidance", {})
    if (not isinstance(guidance, dict) or len(guidance) > 8
            or any(not isinstance(key, str) or not isinstance(item, str)
                   or len(item) > 4000 for key, item in guidance.items())
            or set(guidance) - set(ids)):
        raise ValueError("memory guidance must match selected bounded IDs")
    return dict(value)
