"""Synchronous client for the standalone Robosuite simulator service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np

from .codec import decode_array, decode_observation, encode_array


class ServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClientStep:
    observation: dict[str, np.ndarray]
    reward: float
    done: bool
    info: dict[str, Any]


@dataclass(frozen=True)
class ReferenceResult:
    observation: dict[str, np.ndarray]
    trace: list[dict[str, Any]]


class RobosuiteSimClient:
    def __init__(self, base_url: str, *, timeout: float = 70.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def capabilities(self) -> dict[str, Any]:
        return self._request("GET", "/v1/capabilities")

    def reset(self, **request: Any) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, dict[str, Any]]:
        response = self._request("POST", "/v1/reset", request)
        return self._decode_session(response)

    def attach(self) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, dict[str, Any]]:
        """Attach to an already-reset episode without changing its state."""
        return self._decode_session(self._request("GET", "/v1/session"))

    @staticmethod
    def _decode_session(response: dict[str, Any]) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, dict[str, Any]]:
        return (
            decode_observation(response["observation"]),
            decode_array(response["action_low"]),
            decode_array(response["action_high"]),
            dict(response["metadata"]),
        )

    def step(self, action: np.ndarray) -> ClientStep:
        response = self._request("POST", "/v1/step", {"action": encode_array(action)})
        return ClientStep(
            decode_observation(response["observation"]),
            float(response["reward"]),
            bool(response["done"]),
            dict(response["info"]),
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
            raise ServiceError(f"service returned HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise ServiceError(f"cannot reach Robosuite service at {self.base_url}: {exc}") from exc
        if "error" in value:
            raise ServiceError(str(value["error"]))
        return value
