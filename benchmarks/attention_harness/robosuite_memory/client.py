"""V2-only Robosuite client, independent of the frozen v1 package import path."""

from __future__ import annotations

from typing import Any

from robosuite_sim.client import RobosuiteSimClient, ServiceError


class RobosuiteSimGTClient(RobosuiteSimClient):
    def reset_attested(self, **request: Any):
        response = self._request("POST", "/v1/reset", request)
        applied = response.get("applied_variation")
        if not isinstance(applied, dict) or set(applied) != {"scene_id", "object_set_id"}:
            raise ServiceError("reset did not attest the realized variation")
        return (*self._decode_session(response), applied)

    def perceive_gt(
        self, *, target_names: list[str] | None = None,
        camera_names: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST", "/v2/perceive_gt",
            {"target_names": target_names, "camera_names": camera_names},
        )
