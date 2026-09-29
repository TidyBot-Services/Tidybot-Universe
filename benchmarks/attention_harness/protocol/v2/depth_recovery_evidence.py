"""Recompute depth recovery equivalence from hashed raw artifacts."""
import hashlib
from pathlib import Path

import numpy as np


def verify_recovery(details):
    issues = []
    repeat, equivalence = details.get("repeat_observation", {}), details.get("equivalence", {})
    if repeat.get("status") != "valid" or repeat.get("observation_only") is not True:
        issues.append("not_valid_observation_only_recovery")
    for name in ("simulation_time", "qpos", "intrinsics", "pose"):
        before, after = repeat.get(name + "_before"), repeat.get(name + "_after")
        if before is None or after is None or not np.array_equal(before, after):
            issues.append(name + "_changed_or_missing")
    if repeat.get("simulation_time_unchanged") is not True or repeat.get("qpos_unchanged") is not True:
        issues.append("physics_unchanged_unattested")

    def load(path, digest):
        target = Path(path or "/missing")
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError("artifact hash mismatch")
        return np.load(target, allow_pickle=False)

    try:
        first = load(details.get("capture_path"), details.get("capture_sha256"))
        second = load(repeat.get("capture_path"), repeat.get("capture_sha256"))
        if hashlib.sha256(np.ascontiguousarray(first).tobytes()).hexdigest() != details.get("frame_sha256"):
            issues.append("first_frame_hash")
        if hashlib.sha256(np.ascontiguousarray(second).tobytes()).hexdigest() != repeat.get("frame_sha256"):
            issues.append("repeat_frame_hash")
        if np.isfinite(first).all() and (first >= 0).all() and (first <= 1).all():
            issues.append("first_frame_not_invalid")
        if not (np.isfinite(second).all() and (second >= 0).all() and (second <= 1).all()):
            issues.append("repeat_frame_invalid")
        with load(equivalence.get("path"), equivalence.get("sha256")) as evidence:
            original = {k[10:]: evidence[k] for k in evidence.files if k.startswith("original__")}
            public = {k[8:]: evidence[k] for k in evidence.files if k.startswith("public__")}
            camera = details.get("camera_name")
            depth_key = camera + "_depth"
            if set(public) != set(original) | {camera + "_intrinsics", camera + "_pose_mat"}:
                issues.append("public_key_equivalence")
            for key, value in original.items():
                expected_dtype = np.dtype("float32") if key.endswith("_depth") else value.dtype
                if key not in public or public[key].dtype != expected_dtype or public[key].shape != value.shape:
                    issues.append("public_dtype_or_shape:" + key)
                if key.startswith("robot0_") and not np.array_equal(value, public.get(key)):
                    issues.append("proprioception_changed:" + key)
            if not np.array_equal(first, np.flipud(original[depth_key]), equal_nan=True):
                issues.append("original_frame_binding")
            if not np.array_equal(second, evidence["normalized_depth"]):
                issues.append("repeat_frame_binding")
            near, far = float(evidence["near"]), float(evidence["far"])
            if not (0 < near < far):
                issues.append("invalid_depth_planes")
            metric = (near / (1 - second.astype(np.float64) * (1 - near / far))).astype(np.float32)
            depth = public[depth_key]
            if not np.array_equal(metric, depth) or not np.isfinite(depth).all() or not (depth > 0).all():
                issues.append("metric_depth_equivalence")
            for suffix, name, shape in (("_intrinsics", "intrinsics", (3, 3)), ("_pose_mat", "pose", (4, 4))):
                value = public[camera + suffix]
                if value.dtype != np.float64 or value.shape != shape or not np.array_equal(value, repeat[name + "_before"]):
                    issues.append("calibration_equivalence:" + name)
    except Exception as exc:
        issues.append("recovery_evidence_unreadable:" + str(exc))
    return issues
