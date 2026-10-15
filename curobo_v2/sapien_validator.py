"""Original kitchen PhysX veto, restricted to validation and FK.

Extracted from openarm_robocasa/planning.py. No IK, RRT, path search or
trajectory generation is available through this validator.
"""
import numpy as np
from .client import PlanningRejected


class KitchenCollisionValidator:
    def __init__(self, kitchen, *, bootstrap_side=None):
        self.kitchen = kitchen
        self.rig = kitchen.rig
        self.side = bootstrap_side or kitchen.selection.execution_arm
        if self.side not in {"left", "right"}:
            raise ValueError("invalid OpenArm planning side")
        self.indices = np.array([self.rig.idx[f"openarm_{self.side}_joint{i}"] for i in range(1, 8)])
        self.tcp_name = f"openarm_{self.side}_hand_tcp"
        self._collision_rig = None
        from openarm_robocasa.kitchen import accepted_module
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
            raise PlanningRejected("unsupported kitchen collision shape: " + type(shape).__name__)
        clone.local_pose = shape.local_pose
        clone.set_collision_groups(shape.get_collision_groups())
        clone.contact_offset = shape.contact_offset
        clone.rest_offset = shape.rest_offset
        return clone

    def collision_scene(self):
        import sapien
        from openarm_robocasa.kitchen import accepted_module
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
        from openarm_robocasa.contact_policy import bilateral_target_contacts
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

    def close(self):
        if self._collision_rig is not None:
            self._collision_rig.close()

    def validate_trajectory(self,trajectory,*,max_joint_step=.005,allow_target_contact=False,cancel_check):
        path=np.asarray(trajectory,float)
        if (path.ndim!=2 or len(path)<2 or path.shape[1]!=self.rig.robot.dof
                or not np.isfinite(path).all()or max_joint_step!=.005):
            raise PlanningRejected('Native trajectory requires complete finite joints and frozen .005rad sampling')
        if cancel_check():raise InterruptedError("Native V2 validation canceled before dispatch")
        self.collision_scene();count=0
        for first,last in zip(path[:-1],path[1:]):
            segments=max(1,int(np.ceil(np.max(abs(last-first))/max_joint_step)))
            for index in range(segments):
                if cancel_check():raise InterruptedError("Native V2 validation canceled before dispatch")
                begin=first+(last-first)*index/segments;end=first+(last-first)*(index+1)/segments
                receipt=self.check_path(end,start=begin,allow_target_contact=allow_target_contact,_reuse_scene=True)
                count+=receipt['samples']
                if not receipt['collision_free']:
                    return dict(receipt,trajectory_segment=count,max_joint_step_rad=max_joint_step)
        return dict(collision_free=True,samples=count,max_joint_step_rad=max_joint_step,
                    noncontact_clearance_m=.005,inactive_arm_included=True,
                    source='original kitchen full-shape PhysX veto along the complete V2 trajectory')
