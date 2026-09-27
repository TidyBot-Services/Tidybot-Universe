"""Evidence contract between Skill/Dev, service Wishlist and Deploy Agent.

The public catalog identifies capabilities, not running instances.  This
module only reads catalog/wishlist/inventory and prepares a request; it never
edits the independent Wishlist repo or invokes POST /deploy.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from service_discovery import _catalog_entries, _running_services, resolve_service


_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,95}\Z")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()


def assess_dependency(
    *, capability: str, runtime_name: str | None,
    catalog_path: Path, wishlist_path: Path | None,
    deploy_agent_url: str | None, requested_by: str, reason: str,
) -> dict[str, Any]:
    if not isinstance(capability, str) or not _ID.fullmatch(capability):
        raise ValueError("service capability must be a stable catalog ID")
    if runtime_name is not None and (not isinstance(runtime_name, str) or not _ID.fullmatch(runtime_name)):
        raise ValueError("runtime_name must be an explicit Deploy Agent service ID")
    if not isinstance(requested_by, str) or not requested_by.strip():
        raise ValueError("requested_by is required")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("missing-service request needs a reason")
    catalog = _catalog_entries(catalog_path)
    inventory = None
    inventory_error = None
    if deploy_agent_url:
        try:
            inventory = _running_services(deploy_agent_url)
        except (OSError, ValueError, TimeoutError) as exc:
            inventory_error = type(exc).__name__
    resolution = resolve_service(
        capability, catalog_path=catalog_path,
        deploy_agent_url=deploy_agent_url if inventory is not None else None,
        runtime_name=runtime_name, running_snapshot=inventory,
    )
    state = "inventory_unavailable" if inventory_error else resolution.state
    wishlist_status = None
    wishlist_sha = None
    if wishlist_path is not None:
        payload = json.loads(wishlist_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError("wishlist must have an items array")
        wishlist_sha = _sha(wishlist_path)
        matches = [item for item in payload["items"]
                   if isinstance(item, dict) and item.get("id") == capability]
        if len(matches) > 1:
            raise ValueError("duplicate Wishlist capability ID")
        if matches:
            wishlist_status = matches[0].get("status")
    proposed = None
    if capability not in catalog and wishlist_status is None:
        proposed = {
            "id": capability, "name": capability.replace("-", " ").title(),
            "description": reason, "category": "service",
            "requested_by": requested_by, "reason": reason,
            "votes": 1, "status": "pending", "assigned": None,
            "completed_at": None,
        }
    return {
        "schema_version": "tidybot.service-dependency-evidence.v1",
        "capability": capability,
        "runtime_name": runtime_name or capability,
        "state": state,
        "endpoint": resolution.endpoint if state == "ready" else None,
        "client_sdk": resolution.client,
        "catalog_uri": str(catalog_path.resolve()),
        "catalog_sha256": _sha(catalog_path),
        "catalog_entry_sha256": _canonical_sha(catalog[capability]) if capability in catalog else None,
        "wishlist_uri": str(wishlist_path.resolve()) if wishlist_path else None,
        "wishlist_sha256": wishlist_sha,
        "wishlist_status": wishlist_status,
        "proposed_wishlist_item": proposed,
        "deploy_agent_url": deploy_agent_url,
        "deploy_inventory_sha256": _canonical_sha(inventory) if inventory is not None else None,
        "inventory_error": inventory_error,
        "checked_at": time.time(),
        "deployment_authority": "operator_approval_required",
    }

def prepare_deploy_plan(
    *, dependency_evidence: dict[str, Any], manifest: dict[str, Any],
    approved_by: str, approved_manifest_sha256: str,
) -> dict[str, Any]:
    """Build a POST /deploy body only from an independently approved manifest.

    The current services_wishlist catalog has no image/port/gpu contract.
    A catalog host is never promoted into a deployment instruction.
    """
    if dependency_evidence.get("schema_version") != "tidybot.service-dependency-evidence.v1":
        raise ValueError("verified dependency evidence is required")
    if dependency_evidence.get("state") == "ready":
        raise ValueError("service already reported healthy; deployment is unnecessary")
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise PermissionError("operator approval is required")
    if (not dependency_evidence.get("deploy_agent_url")
            or not dependency_evidence.get("deploy_inventory_sha256")):
        raise RuntimeError("approved deployment needs a verified Deploy Agent inventory snapshot")
    if not isinstance(manifest, dict) or not isinstance(approved_manifest_sha256, str):
        raise ValueError("approved deployment manifest is required")
    if _canonical_sha(manifest) != approved_manifest_sha256:
        raise ValueError("deployment manifest digest does not match approval")
    expected = dependency_evidence["runtime_name"]
    if manifest.get("name") != expected:
        raise ValueError("deployment name does not match the resolved runtime binding")
    image = manifest.get("image")
    port = manifest.get("port")
    if not isinstance(image, str) or not re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", image):
        raise ValueError("deployment requires an immutable image digest")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("deployment requires a valid container port")
    allowed = {"name", "image", "port", "gpu", "vram_gb", "env",
               "volumes", "health", "ready_timeout", "command"}
    if set(manifest) - allowed:
        raise ValueError("deployment manifest has unsupported fields")
    return {
        "schema_version": "tidybot.approved-deploy-plan.v1",
        "endpoint": "POST /deploy",
        "deploy_agent_url": dependency_evidence["deploy_agent_url"],
        "body": dict(manifest),
        "manifest_sha256": approved_manifest_sha256,
        "approved_by": approved_by,
        "catalog_sha256": dependency_evidence["catalog_sha256"],
        "inventory_before_sha256": dependency_evidence["deploy_inventory_sha256"],
        "execution_state": "not_submitted",
    }
