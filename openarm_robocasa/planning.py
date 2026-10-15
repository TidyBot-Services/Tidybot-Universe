"""URDF-specific bounded IK and a separate PhysX collision planning scene."""
from pathlib import Path
import time
import numpy as np


class PlanningError(RuntimeError):
    pass


def joint_path(start, goal, limits, edge_check, *, deadline_seconds=5., seed=101):
    """Deterministic bounded bidirectional RRT, then collision-checked shortcuts."""
    from curobo_v2.deployment import retired_entry
    retired_entry('retired openarm_robocasa/planning.py:joint_path')
    start, goal, limits = map(np.asarray, (start, goal, limits))
    deadline = time.monotonic() + deadline_seconds
    if edge_check(start, goal):
        return [start, goal]
    random = np.random.default_rng(seed)
    trees = [([start.copy()], [-1], True), ([goal.copy()], [-1], False)]
    def extend(tree, target):
        points, parents, _ = tree
        nearest = int(np.argmin([np.linalg.norm(p - target) for p in points]))
        delta = target - points[nearest]
        point = points[nearest] + delta * min(1., .35 / max(np.max(abs(delta)), 1e-12))
        if not edge_check(points[nearest], point):
            return None, False
        points.append(point); parents.append(nearest)
        return len(points) - 1, np.max(abs(point - target)) < 1e-8
    def ancestry(tree, index):
        points, parents, _ = tree; result = []
        while index >= 0:
            result.append(points[index]); index = parents[index]
        return list(reversed(result))
    for iteration in range(600):
        if time.monotonic() >= deadline:
            raise PlanningError("OpenArm collision planning deadline; no action dispatched")
        first, second = trees
        sample = second[0][0] if iteration % 5 == 0 else random.uniform(limits[:, 0], limits[:, 1])
        index, _ = extend(first, sample)
        if index is not None:
            for _ in range(40):
                if time.monotonic() >= deadline: break
                other, reached = extend(second, first[0][index])
                if other is None: break
                if reached:
                    a, b = ancestry(first, index), ancestry(second, other)
                    result = a + list(reversed(b))[1:]
                    if not first[2]: result.reverse()
                    reduced = [result[0]]; anchor = 0
                    while anchor < len(result) - 1:
                        next_index = anchor + 1
                        for end in range(len(result) - 1, anchor + 1, -1):
                            if time.monotonic() >= deadline: break
                            if edge_check(result[anchor], result[end]):
                                next_index = end; break
                        reduced.append(result[next_index]); anchor = next_index
                    return reduced
        trees.reverse()
    raise PlanningError("OpenArm collision planner exhausted bounded search; no action dispatched")


class OpenArmPlanner:
    def __init__(self, kitchen, *, bootstrap_side=None):
        self.kitchen = kitchen
        self.rig = kitchen.rig
        self.side = bootstrap_side or kitchen.selection.execution_arm
        if self.side not in {"left", "right"}:
            raise ValueError("invalid OpenArm planning side")
        self.indices = np.array([self.rig.idx[f"openarm_{self.side}_joint{i}"] for i in range(1, 8)])
        self.tcp_name = f"openarm_{self.side}_hand_tcp"
        self._collision_rig = None
        from .kitchen import accepted_module
        sim = accepted_module().sim
        child_joints = {j.find("child").get("link"): j for j in self.rig.xml.findall("joint")}
        chain = []; child = self.tcp_name
        while child != "world":
            joint = child_joints[child]
            index = self.rig.idx.get(joint.get("name"))
            axis_node = joint.find("axis")
            axis = None if axis_node is None else np.fromstring(axis_node.get("xyz"), sep=" ")
            chain.append((sim.origin(joint.find("origin")), joint.get("type"), axis, index))
            child = joint.find("parent").get("link")
        self.chain = list(reversed(chain))

    def fk(self, q):
        from scipy.spatial.transform import Rotation
        local = np.eye(4)
        for origin, kind, axis, index in self.chain:
            motion = np.eye(4)
            if kind == "prismatic":
                motion[:3, 3] = axis * q[index]
            elif kind != "fixed":
                motion[:3, :3] = Rotation.from_rotvec(axis * q[index]).as_matrix()
            local = local @ origin @ motion
        return self.rig.robot.root_pose.to_transformation_matrix() @ local

    def inverse(self, position, orientation=None):
        """Only selected arm changes; every candidate uses official limits."""
        from curobo_v2.deployment import retired_entry
        retired_entry('retired openarm_robocasa/planning.py:inverse')
        from scipy.optimize import least_squares
        from scipy.spatial.transform import Rotation
        target = np.asarray(position, float)
        if target.shape != (3,) or not np.isfinite(target).all():
            raise ValueError("target must be finite world xyz")
        q = np.array(self.rig.robot.qpos, float)
        lower, upper = self.rig.qlimits[self.indices].T
        current = q[self.indices].copy()

        def residual(arm):
            candidate = q.copy(); candidate[self.indices] = arm
            pose = self.fk(candidate)
            error = pose[:3, 3] - target
            if orientation is not None:
                rot_error = Rotation.from_matrix(np.asarray(orientation) @ pose[:3, :3].T).as_rotvec()
                error = np.r_[error, .15 * rot_error]
            return error

        random = np.random.default_rng(101)
        seeds = [current, (lower + upper) / 2, *random.uniform(lower, upper, (12, 7))]
        candidates = []
        self.inverse_diagnostics = []
        for seed in seeds:
            fit = least_squares(residual, np.clip(seed, lower + 1e-8, upper - 1e-8),
                                bounds=(lower, upper), max_nfev=120,
                                ftol=1e-9, xtol=1e-9, gtol=1e-9)
            candidate = q.copy(); candidate[self.indices] = fit.x
            pose = self.fk(candidate)
            error = np.linalg.norm(pose[:3, 3] - target)
            angular = 0 if orientation is None else np.linalg.norm(
                Rotation.from_matrix(np.asarray(orientation) @ pose[:3, :3].T).as_rotvec())
            self.inverse_diagnostics.append({"position_error_m": float(error), "rotation_error_rad": float(angular),
                                             "nfev": fit.nfev, "arm_qpos": fit.x.tolist()})
            if error <= .004 and angular <= .03:
                candidates.append((float(np.linalg.norm(fit.x - current)), candidate, float(error), float(angular)))
        if not candidates:
            best = min(self.inverse_diagnostics, key=lambda d: d["position_error_m"] + .15 * d["rotation_error_rad"])
            raise PlanningError("OpenArm selected TCP target is unreachable within 4mm / 0.03rad; best=" + str(best))
        return sorted(candidates, key=lambda row: row[0])

    def _clone_shape(self, shape):
        import sapien.physx as px
        material = shape.physical_material
        if isinstance(shape, px.PhysxCollisionShapeBox):
            clone = px.PhysxCollisionShapeBox(shape.half_size, material)
        elif isinstance(shape, px.PhysxCollisionShapeConvexMesh):
            clone = px.PhysxCollisionShapeConvexMesh(shape.vertices, shape.scale, material)
        elif isinstance(shape, px.PhysxCollisionShapeTriangleMesh):
            clone = px.PhysxCollisionShapeTriangleMesh(shape.vertices, shape.triangles, shape.scale, material)
        elif isinstance(shape, (px.PhysxCollisionShapeCapsule, px.PhysxCollisionShapeCylinder)):
            clone = type(shape)(shape.radius, shape.half_length, material)
        elif isinstance(shape, px.PhysxCollisionShapeSphere):
            clone = px.PhysxCollisionShapeSphere(shape.radius, material)
        elif isinstance(shape, px.PhysxCollisionShapePlane):
            clone = px.PhysxCollisionShapePlane(material)
        else:
            raise PlanningError("unsupported kitchen collision shape: " + type(shape).__name__)
        clone.local_pose = shape.local_pose
        clone.set_collision_groups(shape.get_collision_groups())
        clone.contact_offset = shape.contact_offset
        clone.rest_offset = shape.rest_offset
        return clone

    def collision_scene(self):
        import sapien
        from .kitchen import accepted_module
        if self._collision_rig is not None:
            self._collision_rig.close()
        self._collision_rig = accepted_module().sim.Rig(self.rig.urdf)
        rig = self._collision_rig
        rig.robot.set_root_pose(self.rig.robot.root_pose)
        entities = list(self.kitchen.scene.get_all_actors())
        for art in self.kitchen.scene.get_all_articulations():
            if art.root.entity.per_scene_id != self.rig.robot.root.entity.per_scene_id:
                entities.extend(link.entity for link in art.links)
        self.obstacle_names = {}
        self.target_obstacle_name = None
        self.target_clone = None
        from .contact_policy import bilateral_target_contacts
        carrying = bool(bilateral_target_contacts(self.kitchen.scene, side=self.side,
                                                  target_id=self.kitchen.target_entity.per_scene_id))
        self.carried_support = set()
        target_id = self.kitchen.target_entity.per_scene_id
        if carrying:
            for contact in self.kitchen.scene.get_contacts():
                a, b = [body.entity for body in contact.bodies]
                for target, other in [(a, b), (b, a)]:
                    if target.per_scene_id == target_id and not other.name.startswith("openarm_"):
                        self.carried_support.add(other.per_scene_id)
        self.carried_tcp_initial = self.fk(np.array(self.rig.robot.qpos))
        self.carried_object_initial = self.kitchen.target_entity.pose.to_transformation_matrix()
        self.obstacle_ids = {}
        for index, entity in enumerate(entities):
            body = entity.find_component_by_type(sapien.physx.PhysxRigidBaseComponent)
            if body is None or not body.collision_shapes:
                continue
            obstacle = sapien.Entity()
            obstacle.name = f"obstacle:{index}:{entity.name}"
            is_carried = carrying and entity.per_scene_id == target_id
            component = sapien.physx.PhysxRigidDynamicComponent() if is_carried else sapien.physx.PhysxRigidStaticComponent()
            for shape in body.collision_shapes:
                component.attach(self._clone_shape(shape))
            obstacle.add_component(component)
            if is_carried:
                component.disable_gravity = True
                component.mass = body.mass; component.inertia = body.inertia
                component.cmass_local_pose = body.cmass_local_pose
                self.target_clone = obstacle
            obstacle.pose = entity.pose
            rig.scene.add_entity(obstacle)
            self.obstacle_names[obstacle.name] = entity.name
            self.obstacle_ids[obstacle.name] = entity.per_scene_id
            if entity.per_scene_id == self.kitchen.target_entity.per_scene_id:
                self.target_obstacle_name = obstacle.name
        return rig

    def check_path(self, target, *, allow_target_contact=False, _reuse_scene=False, start=None):
        """No real robot qpos is changed by planning."""
        rig = self._collision_rig if _reuse_scene else self.collision_scene()
        start = np.array(self.rig.robot.qpos) if start is None else np.asarray(start)
        segments = max(1, int(np.ceil(np.max(abs(target - start)) / 1.)))
        samples = []
        for segment in range(segments):
            begin = start + (target - start) * segment / segments
            end = start + (target - start) * (segment + 1) / segments
            delta = end - begin
            count = max(2, int(np.ceil(np.max(abs(delta)) / .02)) + 1)
            samples.extend(begin + np.clip(delta, -alpha * np.max(abs(delta)), alpha * np.max(abs(delta)))
                           for alpha in np.linspace(0, 1, count))
        contacts = []
        for index, q in enumerate(samples):
            rig.robot.set_qpos(q); rig.robot.set_qvel(np.zeros(rig.robot.dof))
            carried_pose = None
            if self.target_clone is not None:
                import sapien
                carried_pose = self.fk(q) @ np.linalg.inv(self.carried_tcp_initial) @ self.carried_object_initial
                self.target_clone.pose = sapien.Pose(carried_pose)
                body = self.target_clone.find_component_by_type(sapien.physx.PhysxRigidDynamicComponent)
                body.linear_velocity = np.zeros(3); body.angular_velocity = np.zeros(3)
            rig.goal = q.copy(); rig.command = q.copy()
            rig.step()
            for contact in rig.contacts():
                a, b = contact["bodies"]
                if not (a.startswith("openarm_") or b.startswith("openarm_")):
                    if self.target_clone is not None and self.target_clone.name in {a, b}:
                        other = b if a == self.target_clone.name else a
                        # Real reset/grasp support may be left by an upward lift.
                        departing = (self.obstacle_ids.get(other) in self.carried_support and
                            carried_pose[2, 3] >= self.carried_object_initial[2, 3] - .0001)
                        if not departing and any(p["separation_m"] < .005 for p in contact["points"]):
                            contacts.append({"sample": index, "carried_object": True, **contact})
                    continue
                if not any(p["separation_m"] < .005 for p in contact["points"]):
                    continue
                # Ground mounting contact is a fixed-base support, never arm contact.
                robot_name = a if a.startswith("openarm_") else b
                other = self.obstacle_names.get(b if robot_name == a else a, "")
                if robot_name == "openarm_body_link0" and "floor" in other:
                    continue
                other_name = b if robot_name == a else a
                if allow_target_contact and robot_name in {
                        f"openarm_{self.side}_left_finger", f"openarm_{self.side}_right_finger"} and other_name == self.target_obstacle_name:
                    continue
                contacts.append({"sample": index, **contact})
            if contacts:
                break
        return {"collision_free": not contacts, "samples": len(samples), "noncontact_clearance_m": .005,
                "obstacle_count": len(self.obstacle_names), "contacts": contacts,
                "carried_object_included": self.target_clone is not None,
                "inactive_arm_included": True, "source": "separate PhysX scene with current kitchen collision shapes"}

    def plan(self, position, orientation=None):
        from curobo_v2.deployment import retired_entry
        retired_entry('retired openarm_robocasa/planning.py:plan')
        rejected = []
        candidates = self.inverse(position, orientation)
        for index, (_, target, error, angular) in enumerate(candidates):
            check = self.check_path(target, allow_target_contact=True, _reuse_scene=index > 0)
            if check["collision_free"]:
                return {"target_qpos": target.tolist(), "position_error_m": error,
                        "rotation_error_rad": angular, "collision_check": check,
                        "robot": self.kitchen.selection.artifact(), "joint_waypoints": [target.tolist()]}
            rejected.append(check)
        start = np.array(self.rig.robot.qpos)
        search_deadline = time.monotonic() + 5.
        for _, target, error, angular in candidates:
            if not self.check_path(target, start=target, allow_target_contact=True, _reuse_scene=True)["collision_free"]:
                continue
            def edge(a, b):
                qa, qb = start.copy(), start.copy()
                qa[self.indices], qb[self.indices] = a, b
                return self.check_path(qb, start=qa, allow_target_contact=True, _reuse_scene=True)["collision_free"]
            try:
                path = joint_path(start[self.indices], target[self.indices], self.rig.qlimits[self.indices],
                                  edge, deadline_seconds=max(.01, search_deadline - time.monotonic()))
                waypoints = []
                for arm in path[1:]:
                    q = start.copy(); q[self.indices] = arm; waypoints.append(q.tolist())
                return {"target_qpos": target.tolist(), "position_error_m": error, "rotation_error_rad": angular,
                        "joint_waypoints": waypoints, "robot": self.kitchen.selection.artifact(),
                        "collision_check": {"collision_free": True, "inactive_arm_included": True,
                            "carried_object_included": self.target_clone is not None,
                            "obstacle_count": len(self.obstacle_names), "planner": "bounded bidirectional RRT",
                            "edge_checks": "accepted joint slew at <=0.02rad samples", "waypoints": len(waypoints)}}
            except PlanningError:
                if time.monotonic() >= search_deadline: break
        raise PlanningError("OpenArm IK candidates intersect kitchen or either arm: " + str(rejected[:1]))

    def close(self):
        if self._collision_rig is not None:
            self._collision_rig.close()
