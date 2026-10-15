"""Conservative, complete shape snapshots from the actual kitchen scene."""
from __future__ import annotations

import hashlib
import json

import numpy as np


class WorldError(RuntimeError):
    """A collision world could not be represented without dropping geometry."""


def box_from_vertices(vertices, scale):
    points = np.asarray(vertices, dtype=float) * np.asarray(scale, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or not len(points) or not np.isfinite(points).all():
        raise WorldError("Collision mesh contains invalid vertices")
    low, high = points.min(axis=0), points.max(axis=0)
    # A flat triangle mesh still has a nonzero collision skin.
    return (low + high) / 2, np.maximum((high - low) / 2, 1e-5)


def transformed_box(name: str, center, half_size, world_from_shape, planner_from_world):
    from scipy.spatial.transform import Rotation
    center, half_size = np.asarray(center, float), np.asarray(half_size, float)
    transform = np.asarray(planner_from_world, float) @ np.asarray(world_from_shape, float)
    if (center.shape != (3,) or half_size.shape != (3,) or transform.shape != (4, 4)
            or not np.isfinite(np.r_[center, half_size, transform.ravel()]).all()
            or np.any(half_size <= 0) or not np.allclose(transform[3], [0, 0, 0, 1], atol=1e-7)
            or not np.allclose(transform[:3, :3].T @ transform[:3, :3], np.eye(3), atol=1e-6)
            or np.linalg.det(transform[:3, :3]) < .999999):
        raise WorldError("Invalid collision shape transform or dimensions")
    quaternion = Rotation.from_matrix(transform[:3, :3]).as_quat()[[3, 0, 1, 2]]
    return {"name": name, "center": (transform[:3, :3] @ center + transform[:3, 3]).tolist(),
            "half_size": half_size.tolist(), "quat_wxyz": quaternion.tolist()}


def shape_box(shape, *, plane_extent=100.):
    import sapien.physx as px
    zero = np.zeros(3)
    if isinstance(shape, px.PhysxCollisionShapeBox):
        return zero, np.asarray(shape.half_size)
    if isinstance(shape, (px.PhysxCollisionShapeConvexMesh, px.PhysxCollisionShapeTriangleMesh)):
        return box_from_vertices(shape.vertices, shape.scale)
    if isinstance(shape, px.PhysxCollisionShapeSphere):
        return zero, np.full(3, shape.radius)
    if isinstance(shape, px.PhysxCollisionShapeCapsule):
        return zero, np.array([shape.half_length + shape.radius, shape.radius, shape.radius])
    if isinstance(shape, px.PhysxCollisionShapeCylinder):
        return zero, np.array([shape.half_length, shape.radius, shape.radius])
    if isinstance(shape, px.PhysxCollisionShapePlane):
        # PhysX planes have local +X normal. Represent the solid negative
        # half-space over the explicitly bounded planning domain, including floor.
        return np.array([-plane_extent / 2, 0, 0]), np.array([plane_extent / 2, plane_extent, plane_extent])
    raise WorldError("Unsupported actual kitchen collision shape: " + type(shape).__name__)


def snapshot(scene, robot, *, planner_from_world=None, plane_extent=100.):
    """Include every nonrobot actor and articulated link, without name filtering."""
    import sapien
    transform = np.eye(4) if planner_from_world is None else np.asarray(planner_from_world, float)
    robot_id = robot.root.entity.per_scene_id
    entities = {entity.per_scene_id: entity for entity in scene.get_all_actors()}
    articulated_ids = set()
    for articulation in scene.get_all_articulations():
        if articulation.root.entity.per_scene_id == robot_id:
            continue
        for link in articulation.links:
            entities[link.entity.per_scene_id] = link.entity
            articulated_ids.add(link.entity.per_scene_id)
    cuboids, inventory = [], []
    for identifier, entity in sorted(entities.items()):
        body = entity.find_component_by_type(sapien.physx.PhysxRigidBaseComponent)
        if body is None:
            continue
        world_from_entity = entity.pose.to_transformation_matrix()
        for index, shape in enumerate(body.collision_shapes):
            center, half_size = shape_box(shape, plane_extent=plane_extent)
            world_from_shape = world_from_entity @ shape.local_pose.to_transformation_matrix()
            box = transformed_box(f"entity:{identifier}:shape:{index}:{entity.name}", center, half_size,
                                  world_from_shape, transform)
            cuboids.append(box)
            inventory.append({"name": box["name"], "entity_id": identifier, "entity_name": entity.name,
                              "shape_type": type(shape).__name__, "articulated": identifier in articulated_ids,
                              "source": "actual PhysX collision geometry", "conservative_box": True})
    if not cuboids:
        raise WorldError("Actual kitchen world has no collision geometry")
    identity = {"cuboids": cuboids, "planner_from_world": transform.tolist(), "plane_extent_m": plane_extent}
    world_sha = hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return {**identity, "world_sha256": world_sha, "inventory": inventory,
            "complete_shape_count": len(cuboids), "robot_link_count": len(robot.links),
            "inactive_robot_links": "represented by the complete locked robot model and current named joints"}
