"""JSON-safe NumPy encoding shared by the service and its client."""

from __future__ import annotations

import base64
from typing import Any, Mapping

import numpy as np


def encode_array(value: np.ndarray | Any) -> dict[str, Any]:
    array = np.ascontiguousarray(value)
    return {
        "dtype": str(array.dtype),
        "shape": list(array.shape),
        "data": base64.b64encode(array.tobytes()).decode("ascii"),
    }


def decode_array(value: Mapping[str, Any]) -> np.ndarray:
    raw = base64.b64decode(str(value["data"]), validate=True)
    array = np.frombuffer(raw, dtype=np.dtype(str(value["dtype"])))
    return array.reshape(tuple(int(item) for item in value["shape"])).copy()


def encode_observation(observation: Mapping[str, np.ndarray]) -> dict[str, Any]:
    return {key: encode_array(value) for key, value in observation.items()}


def decode_observation(observation: Mapping[str, Any]) -> dict[str, np.ndarray]:
    return {key: decode_array(value) for key, value in observation.items()}
