"""Robosuite GT object provider for the explicitly labelled v2 track."""

from __future__ import annotations

import math
from typing import Any

from .adapter import RobosuiteSimGTBackend


class RobosuiteGTPerception:
    def __init__(self, adapter: RobosuiteSimGTBackend, *, fixed_camera_names: list[str] | None = None) -> None:
        self.adapter = adapter
        self.fixed_camera_names = fixed_camera_names

    def find_objects(
        self, target_names: list[str] | None = None,
        camera_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if self.fixed_camera_names is not None:
            if camera_names is not None and camera_names != self.fixed_camera_names:
                raise ValueError("policy requested cameras outside the frozen validation case")
            camera_names = self.fixed_camera_names
        response = self.adapter.perceive_gt(target_names=target_names, camera_names=camera_names)
        if response.get("frame") != "robosuite_world":
            raise ValueError("GT perception returned a different coordinate frame")
        if response.get("cameras") != [self.adapter.camera_name]:
            raise ValueError("GT perception did not attest the active camera")
        if camera_names is not None and camera_names != response["cameras"]:
            raise ValueError("GT perception did not attest requested cameras")
        rows = response.get("objects")
        if not isinstance(rows, list):
            raise ValueError("GT perception did not return objects")
        result = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                raise ValueError("GT perception returned an invalid object")
            position = row.get("position")
            if not isinstance(position, list) or len(position) != 3 or any(
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                for value in position
            ):
                raise ValueError("GT perception returned an invalid position")
            result.append({
                "name": row["name"], "position": position, "confidence": 1.0,
                "source": "sim_gt", "frame": "robosuite_world",
            })
        return result
