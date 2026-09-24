"""RoboCasa simulator-GT perception provider for the v2 experiment track."""

from __future__ import annotations

from typing import Any

import numpy as np

from .client import RobocasaServiceError, RobocasaSimClient


class RobocasaGTPerception:
    """Transform `/perceive` world positions into the arm-base control frame.

    `/perceive` uses simulator segmentation and actor-name mapping. Its output
    is deliberately labelled sim_gt, even though depth contributes to the
    position estimate. Evaluator endpoints are never called here.
    """

    def __init__(self, client: RobocasaSimClient, *, fixed_camera_names: list[str] | None = None) -> None:
        self._client = client
        self._fixed_camera_names = fixed_camera_names

    def find_objects(
        self,
        target_names: list[str] | None = None,
        camera_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if self._fixed_camera_names is not None:
            if camera_names is not None and camera_names != self._fixed_camera_names:
                raise RobocasaServiceError("policy requested cameras outside frozen validation case")
            camera_names = self._fixed_camera_names
        response = self._client._call(
            "POST",
            "/perceive",
            {"target_names": target_names, "camera_names": camera_names},
            timeout=120.0,
        )
        if response.get("error"):
            raise RobocasaServiceError(str(response["error"]))
        if self._fixed_camera_names is not None and response.get("cameras") != self._fixed_camera_names:
            raise RobocasaServiceError("/perceive did not attest the frozen camera views")
        objects = response.get("objects")
        if not isinstance(objects, list):
            raise RobocasaServiceError("/perceive did not return objects")
        base = _vec(response.get("arm_base"), 3, "arm_base")
        quat = _vec(response.get("arm_base_quat"), 4, "arm_base_quat")
        rotation = _wxyz_rotation(quat)
        result = []
        for row in objects:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                raise RobocasaServiceError("/perceive returned an invalid object")
            world = _vec([row.get("x"), row.get("y"), row.get("z")], 3, "object position")
            position = rotation.T @ (world - base)
            result.append(
                {
                    "name": row["name"],
                    "position": position.tolist(),
                    "confidence": 1.0,
                    "source": "sim_gt",
                    "frame": "arm_base",
                }
            )
        return result


def _vec(value: Any, count: int, label: str) -> np.ndarray:
    try:
        array = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise RobocasaServiceError(f"{label} must contain finite numbers") from exc
    if array.shape != (count,) or not np.isfinite(array).all():
        raise RobocasaServiceError(f"{label} must contain {count} finite numbers")
    return array


def _wxyz_rotation(quat: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(quat)
    if norm < 1e-8:
        raise RobocasaServiceError("arm_base_quat is degenerate")
    w, x, y, z = quat / norm
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )
