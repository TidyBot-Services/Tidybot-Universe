"""Read-only service discovery for Skill/Attention agents.

The catalog describes *known* services; the Deploy Agent reports *running*
instances. Missing services are requests for a human/service author, never
an implicit deployment or an untrusted internet search.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import urlopen


@dataclass(frozen=True)
class ServiceResolution:
    name: str
    state: str  # ready | catalog_only | requested | unavailable
    endpoint: str | None
    client: str | None
    detail: str


def _catalog_entries(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("capabilities"), dict):
        values = [{**value, "name": name} for name, value in data["capabilities"].items()
                  if isinstance(value, dict)]
    elif isinstance(data, dict) and isinstance(data.get("services"), list):
        values = data["services"]
    elif isinstance(data, list):
        values = data
    elif isinstance(data, dict):
        values = [{"name": key, **value} for key, value in data.items()
                  if isinstance(value, dict)]
    else:
        raise ValueError("service catalog must be a list or object")
    entries: dict[str, dict[str, Any]] = {}
    for item in values:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError("service catalog entry needs a name")
        name = item["name"].strip()
        if not name or name in entries:
            raise ValueError("duplicate or empty service name")
        entries[name] = item
    return entries


def _running_services(deploy_agent_url: str, *, timeout: float = 3.0) -> dict[str, dict[str, Any]]:
    parsed = urlsplit(deploy_agent_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
        raise ValueError("invalid Deploy Agent URL")
    with urlopen(deploy_agent_url.rstrip("/") + "/services", timeout=timeout) as response:
        payload = json.load(response)
    values = payload.get("services") if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        raise ValueError("Deploy Agent returned invalid service inventory")
    return {item["name"]: item for item in values
            if isinstance(item, dict) and isinstance(item.get("name"), str)}


def resolve_service(
    name: str, *, catalog_path: Path, deploy_agent_url: str | None = None,
    runtime_name: str | None = None,
    running_snapshot: dict[str, dict[str, Any]] | None = None,
    timeout: float = 3.0,
) -> ServiceResolution:
    """Resolve a named dependency without deploying or calling the service."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("service name is required")
    catalog = _catalog_entries(catalog_path)
    item = catalog.get(name)
    if item is None:
        return ServiceResolution(name, "requested", None, None,
                                 "not in catalog; add to Wishlist for service author")
    client = item.get("client") or item.get("client_sdk")
    if client is not None and (not isinstance(client, str) or not client.strip()):
        raise ValueError("invalid service client reference")
    if deploy_agent_url is None:
        return ServiceResolution(name, "catalog_only", None, client,
                                 "catalog entry found; running state unverified")
    try:
        running = running_snapshot if running_snapshot is not None else _running_services(deploy_agent_url, timeout=timeout)
    except (OSError, ValueError, TimeoutError) as exc:
        return ServiceResolution(name, "unavailable", None, client,
                                 f"Deploy Agent inventory unavailable: {type(exc).__name__}")
    instance = running.get(runtime_name or name)
    if instance is None or instance.get("status") != "healthy":
        return ServiceResolution(name, "catalog_only", None, client,
                                 "known service is not reported healthy")
    endpoint = instance.get("host")
    if not isinstance(endpoint, str):
        raise ValueError("healthy Deploy Agent entry has no endpoint")
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
        raise ValueError("invalid running-service endpoint")
    return ServiceResolution(name, "ready", endpoint, client,
                             "catalogued and reported healthy by Deploy Agent")
