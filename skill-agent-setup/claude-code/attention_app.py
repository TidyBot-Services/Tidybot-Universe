"""AttentionBench-aware Skill DAG entrypoint with named service discovery."""

from __future__ import annotations

import asyncio
from pathlib import Path

import agent_orchestrator as orch
from attention_orchestrator import install as install_attention
from dev_memory_bridge import prepare_dev_memory
from service_evidence import assess_dependency


def install() -> None:
    install_attention()
    prior_prompt = orch._get_system_prompt

    def prompt_with_services(agent_type: str, skill_name: str = "",
                             agent_server_url: str = "") -> str:
        prompt = prior_prompt(agent_type, skill_name, agent_server_url)
        if agent_type != "dev" or not skill_name:
            return prompt
        entry = orch._find_entry(skill_name)
        if entry and entry.get("dev_memory") is not None:
            url = orch.graph_meta.get("memory_service_url")
            if not isinstance(url, str) or not url.strip():
                raise ValueError("Dev Memory requires graph memory_service_url")
            previous = entry.get("dev_memory_exposure")
            if previous is not None:
                dev = entry["dev_memory"]
                if (not isinstance(previous, dict) or not isinstance(dev, dict)
                        or previous.get("development_id") != dev.get("development_id")):
                    raise ValueError("Dev Memory exposure changed within one graph node")
                prompt += (
                    "\n\nA prior Dev Memory grant exists for this session: "
                    f"{previous['grant_id']}. Do not re-expose guidance under this "
                    "development ID; create a new approved development session if needed.\n"
                )
            else:
                guidance, pointer = prepare_dev_memory(
                    entry=entry, graph_id=orch.GRAPH_DIR.name,
                    repo_root=Path(__file__).resolve().parents[2],
                    memory_service_url=url,
                )
                orch._update_entry(skill_name, {"dev_memory_exposure": pointer})
                prompt += guidance
        dependencies = entry.get("service_dependencies", []) if entry else []
        if not dependencies:
            return prompt
        catalog_name = orch.graph_meta.get("service_catalog")
        if not isinstance(catalog_name, str) or not catalog_name.strip():
            return prompt + (
                "\n\n## External Services\n"
                "Service dependencies are declared, but no service_catalog is configured. "
                "Do not guess URLs or silently deploy services.\n"
            )
        catalog = Path(catalog_name)
        if not catalog.is_absolute():
            catalog = orch.GRAPH_DIR / catalog
        deploy_url = orch.graph_meta.get("deploy_agent_url")
        wishlist_name = orch.graph_meta.get("service_wishlist")
        wishlist = Path(wishlist_name) if isinstance(wishlist_name, str) else None
        if wishlist is not None and not wishlist.is_absolute():
            wishlist = orch.GRAPH_DIR / wishlist
        lines = ["\n\n## External Services (catalog + Deploy Agent inventory)"]
        evidence = []
        for dependency in dependencies[:12]:
            if isinstance(dependency, str):
                name, runtime_name = dependency, None
                reason = f"Graph node {skill_name} requires capability {name}"
            elif isinstance(dependency, dict):
                name = dependency.get("capability")
                runtime_name = dependency.get("runtime_name")
                reason = dependency.get("reason", "")
            else:
                lines.append("- Invalid service dependency; request graph correction.")
                continue
            try:
                result = assess_dependency(
                    capability=name, runtime_name=runtime_name,
                    catalog_path=catalog, wishlist_path=wishlist,
                    deploy_agent_url=deploy_url if isinstance(deploy_url, str) else None,
                    requested_by=f"{orch.GRAPH_DIR.name}:{skill_name}", reason=reason,
                )
                evidence.append(result)
                lines.append(
                    f"- {name}: {result['state']}; endpoint={result['endpoint'] or 'unverified'}; "
                    f"client={result['client_sdk'] or 'unspecified'}; "
                    f"wishlist={result['wishlist_status'] or ('proposal' if result['proposed_wishlist_item'] else 'none')}."
                )
            except (OSError, ValueError) as exc:
                lines.append(f"- {name}: unavailable ({type(exc).__name__}); do not guess endpoint.")
        orch._update_entry(skill_name, {"service_resolution_evidence": evidence})
        lines.append(
            "Use a named client only when the endpoint is verified. If missing, "
            "record a Wishlist request; deployment is a separate operator-approved action."
        )
        return prompt + "\n".join(lines)

    orch._get_system_prompt = prompt_with_services


if __name__ == "__main__":
    install()
    asyncio.run(orch.main())
