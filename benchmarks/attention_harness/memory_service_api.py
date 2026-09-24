"""Authenticated loopback HTTP surface for the shared Memory Service.

This is an optional transport; the Memory Agent and runner can use the same
service boundary in-process. No daemon is started merely by importing it.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from .core.store import StateConflictError
from .memory_service import MemoryService


class CandidateInput(BaseModel):
    memory_id: str
    request_id: str
    guidance: str
    repair: str
    applicability: dict[str, str]
    created_at: float
    artifact_kind: str = "text_hint"
    human_attention_seconds: float = 0.0


class RetrieveInput(BaseModel):
    context: dict[str, Any]
    now: float


class PairInput(BaseModel):
    memory_id: str
    control_attempt_id: str
    treatment_attempt_id: str
    control_safety: dict[str, Any]
    treatment_safety: dict[str, Any]


def create_app(store_path: Path, *, api_key: str) -> FastAPI:
    if not api_key or len(api_key) < 16:
        raise ValueError("Memory Service requires an API key of at least 16 characters")
    service = MemoryService(store_path)
    app = FastAPI(title="TidyBot Attention Memory Service", version="2")

    def authorized(x_memory_service_key: str | None = Header(default=None)) -> None:
        if not x_memory_service_key or not hmac.compare_digest(x_memory_service_key, api_key):
            raise HTTPException(status_code=401, detail="invalid Memory Service key")

    def guarded(call, *args, **kwargs):
        try:
            return call(*args, **kwargs)
        except StateConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/health")
    def health():
        return {"status": "ok", "schema_version": "attentionbench.memory-service.v2"}

    @app.get("/sources/{request_id}", dependencies=[Depends(authorized)])
    def source(request_id: str):
        return guarded(service.source_for_agent, request_id)

    @app.post("/candidates", dependencies=[Depends(authorized)])
    def candidate(payload: CandidateInput):
        return guarded(service.create_candidate, **payload.model_dump()).artifact()

    @app.post("/retrieve", dependencies=[Depends(authorized)])
    def retrieve(payload: RetrieveInput):
        return [item.artifact() for item in guarded(
            service.retrieve, payload.context, now=payload.now,
        )]

    @app.post("/pairs", dependencies=[Depends(authorized)])
    def pair(payload: PairInput):
        def save_safety(value: dict[str, Any]) -> Path:
            encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            if len(encoded) > 65536:
                raise ValueError("safety monitor artifact exceeds 64 KiB")
            digest = hashlib.sha256(encoded).hexdigest()
            directory = store_path.parent / "memory-safety-evidence"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{digest}.json"
            if path.exists():
                if path.read_bytes() != encoded:
                    raise StateConflictError("safety artifact digest collision")
            else:
                path.write_bytes(encoded)
            return path

        return guarded(
            service.record_pair,
            memory_id=payload.memory_id,
            control_attempt_id=payload.control_attempt_id,
            treatment_attempt_id=payload.treatment_attempt_id,
            control_safety=guarded(save_safety, payload.control_safety),
            treatment_safety=guarded(save_safety, payload.treatment_safety),
        )

    @app.get("/memories/{memory_id}", dependencies=[Depends(authorized)])
    def memory(memory_id: str):
        return guarded(service.get_memory, memory_id).artifact()

    @app.get("/memories/{memory_id}/provenance", dependencies=[Depends(authorized)])
    def provenance(memory_id: str):
        return guarded(service.provenance, memory_id)

    @app.get("/memories/{memory_id}/impact", dependencies=[Depends(authorized)])
    def impact(memory_id: str):
        return guarded(service.impact_report, memory_id)

    @app.get("/memories/{memory_id}/pairs", dependencies=[Depends(authorized)])
    def pairs(memory_id: str):
        return guarded(service.list_pairs, memory_id)

    @app.put("/memories/{memory_id}/plan", dependencies=[Depends(authorized)])
    def plan(memory_id: str, payload: dict[str, Any]):
        return guarded(service.record_plan, memory_id, payload)

    @app.get("/memories/{memory_id}/plan", dependencies=[Depends(authorized)])
    def get_plan(memory_id: str):
        return guarded(service.get_plan, memory_id)

    @app.post("/memories/{memory_id}/export", dependencies=[Depends(authorized)])
    def export(memory_id: str):
        return {"directory": str(guarded(service.export_package, memory_id))}

    @app.get("/memories/{memory_id}/artifact", dependencies=[Depends(authorized)])
    def artifact(memory_id: str):
        return guarded(service.verify_package, memory_id)

    @app.post("/memories/{memory_id}/promote", dependencies=[Depends(authorized)])
    def promote(memory_id: str):
        return guarded(service.promote, memory_id).artifact()

    return app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-path", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--api-key-env", default="ATTENTION_MEMORY_API_KEY")
    args = parser.parse_args()
    key = os.environ.get(args.api_key_env, "")
    app = create_app(args.store_path, api_key=key)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
