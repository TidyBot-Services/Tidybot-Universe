"""Synchronous client for the standalone Robosuite simulator service."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np

from .codec import decode_array, decode_observation, encode_array


class ServiceError(RuntimeError):
    def __init__(self, message, *, payload=None, status=None):
        super().__init__(message)
        self.payload = payload
        self.status = status


@dataclass(frozen=True)
class ClientStep:
    observation: dict[str, np.ndarray]
    reward: float
    done: bool
    info: dict[str, Any]
    receipt: dict[str, Any] | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class ReferenceResult:
    observation: dict[str, np.ndarray]
    trace: list[dict[str, Any]]


class RobosuiteSimClient:
    def __init__(self, base_url: str, *, timeout: float = 70.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session_id = None

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def capabilities(self) -> dict[str, Any]:
        return self._request("GET", "/v1/capabilities")

    def reset(self, **request: Any) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, dict[str, Any]]:
        response = self._request("POST", "/v1/reset", request)
        return self._decode_session(response)

    def reset_attested(self, **request: Any) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, dict[str, Any], dict[str, str]]:
        response = self._request("POST", "/v1/reset", request)
        applied = response.get("applied_variation")
        if not isinstance(applied, dict) or set(applied) != {"scene_id", "object_set_id"}:
            raise ServiceError("reset did not attest the realized variation")
        return (*self._decode_session(response), applied)

    def perceive_gt(self, *, target_names: list[str] | None = None, camera_names: list[str] | None = None) -> dict[str, Any]:
        return self._request(
            "POST", "/v2/perceive_gt",
            {"target_names": target_names, "camera_names": camera_names},
        )

    def attach(self) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, dict[str, Any]]:
        """Attach to an already-reset episode without changing its state."""
        return self._decode_session(self._request("GET", "/v1/session"))

    def _decode_session(self, response: dict[str, Any]) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, dict[str, Any]]:
        self.session_id = response["metadata"].get("session_id")
        return (
            decode_observation(response["observation"]),
            decode_array(response["action_low"]),
            decode_array(response["action_high"]),
            dict(response["metadata"]),
        )

    def step(self, action: np.ndarray, *, request_id: str | None = None) -> ClientStep:
        request = {"action": encode_array(action), "session_id": self.session_id,
                   "request_id": request_id or uuid.uuid4().hex}
        try:
            response = self._request("POST", "/v1/step", request)
        except ServiceError as exc:
            exc.request_id = request["request_id"]
            exc.session_id = request["session_id"]
            raise
        return ClientStep(
            decode_observation(response["observation"]),
            float(response["reward"]),
            bool(response["done"]),
            dict(response["info"]),
            response.get("receipt"), response.get("request_id"),
        )

    def observe(self) -> dict[str, np.ndarray]:
        return decode_observation(self._request("GET", "/v1/observation")["observation"])

    def native_success(self) -> bool:
        return bool(self._request("GET", "/v1/success")["native_success"])

    def metadata(self) -> dict[str, Any]:
        return self._request("GET", "/v1/metadata")

    def run_reference(self, timeout_seconds: float) -> ReferenceResult:
        response = self._request(
            "POST", "/v1/reference", {"timeout_seconds": timeout_seconds}
        )
        return ReferenceResult(
            decode_observation(response["observation"]),
            list(response["trace"]),
        )

    def close_environment(self) -> None:
        self._request("POST", "/v1/close", {})

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                value = json.loads(response.read())
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(detail)
            except ValueError:
                payload = {"error": detail}
            raise ServiceError(f"service returned HTTP {exc.code}: {detail}",
                               payload=payload, status=exc.code) from exc
        except (URLError, TimeoutError, ConnectionError) as exc:
            raise ServiceError(f"cannot reach Robosuite service at {self.base_url}: {exc}") from exc
        if "error" in value:
            raise ServiceError(str(value["error"]))
        return value
