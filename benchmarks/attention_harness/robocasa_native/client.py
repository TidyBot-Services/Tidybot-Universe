"""Non-oracle HTTP client for the existing RoboCasa/ManiSkill service."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .tasks import get_robocasa_task


JsonTransport = Callable[[str, str, dict[str, Any] | None, float], dict[str, Any]]


class RobocasaServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class RobocasaObservation:
    task_id: str
    environment_id: str
    language: str
    objects: tuple[dict[str, Any], ...]
    cameras: tuple[str, ...]

    def artifact(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "environment_id": self.environment_id,
            "language": self.language,
            "objects": [dict(item) for item in self.objects],
            "cameras": list(self.cameras),
        }

    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.artifact(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class RobocasaSimClient:
    """Expose only reset, public perception, task info, and Boolean success."""

    def __init__(
        self,
        task_id: str,
        *,
        base_url: str = "http://127.0.0.1:5500",
        transport: JsonTransport | None = None,
    ) -> None:
        self.spec = get_robocasa_task(task_id)
        self.base_url = base_url.rstrip("/")
        self._transport = transport or self._http_json

    def assert_task(self) -> dict[str, Any]:
        info = self._call("GET", "/task/info")
        if info.get("task") != self.spec.environment_id:
            raise RobocasaServiceError(
                f"service task {info.get('task')!r} does not match "
                f"{self.spec.environment_id!r}"
            )
        return {
            "task": self.spec.environment_id,
            "lang": str(info.get("lang", "")),
        }

    def reset(self, seed: int) -> RobocasaObservation:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise TypeError("seed must be an integer")
        result = self._call("POST", "/reset", {"seed": seed}, timeout=120.0)
        if result.get("status") != "ok":
            raise RobocasaServiceError(f"reset failed: {result}")
        return self.observe()

    def observe(self) -> RobocasaObservation:
        info = self.assert_task()
        perception = self._call("POST", "/perceive", {}, timeout=120.0)
        objects = perception.get("objects", [])
        cameras = perception.get("cameras", [])
        if not isinstance(objects, list) or not isinstance(cameras, list):
            raise RobocasaServiceError("invalid public perception response")
        sanitized = []
        for item in objects:
            if not isinstance(item, dict):
                raise RobocasaServiceError("perception object must be a JSON object")
            sanitized.append(dict(item))
        return RobocasaObservation(
            task_id=self.spec.task_id,
            environment_id=self.spec.environment_id,
            language=info["lang"],
            objects=tuple(sanitized),
            cameras=tuple(str(value) for value in cameras),
        )

    def native_success(self) -> bool:
        # The existing service response also contains privileged evaluator
        # debug data. Deliberately discard it at this boundary.
        result = self._call("GET", "/task/success")
        success = result.get("success")
        if not isinstance(success, bool):
            raise RobocasaServiceError("native evaluator did not return a Boolean")
        return success

    def _call(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        result = self._transport(method, self.base_url + path, payload, timeout)
        if not isinstance(result, dict):
            raise RobocasaServiceError(f"{path} did not return a JSON object")
        return result

    @staticmethod
    def _http_json(
        method: str,
        url: str,
        payload: dict[str, Any] | None,
        timeout: float,
    ) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
        except (OSError, urllib.error.URLError) as exc:
            raise RobocasaServiceError(f"{method} {url} failed: {exc}") from exc
        try:
            value = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RobocasaServiceError(f"{method} {url} returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise RobocasaServiceError(f"{method} {url} returned non-object JSON")
        return value
