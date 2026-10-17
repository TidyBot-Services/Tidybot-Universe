"""Per-sphere static-world contact groups for a measured carried body."""
from dataclasses import replace

import torch
from curobo._src.geom.collision.buffer_collision import CollisionBuffer
from curobo._src.geom.collision.collision_scene import SceneCollision, SceneCollisionCfg


class CarriedSceneCollision(SceneCollision):
    def configure(self, attached_indices, total_spheres, cache):
        indices = tuple(attached_indices)
        if (not indices or len(set(indices)) != len(indices)
                or any(type(i) is not int or i < 0 or i >= total_spheres for i in indices)):
            raise ValueError('Invalid attachment sphere mapping')
        self._attached_indices = tuple(sorted(indices))
        mask = torch.zeros(total_spheres, dtype=torch.bool, device=self.device_cfg.device)
        mask[list(indices)] = True
        self._masks = (~mask, mask)
        self._buffers = {}
        self._object_names = set()
        self._support_names = set()
        self._groups = [SceneCollision.from_config(SceneCollisionCfg(
            device_cfg=self.device_cfg, max_distance=self.checker.max_distance,
            cache=cache)) for _ in range(2)]
        self.load_collision_model(self.scene_model)

    def attachment(self, object_names, support_names):
        objects, supports = set(object_names), set(support_names)
        if (not objects or not supports or objects & supports
                or len(objects) != len(object_names) or len(supports) != len(support_names)):
            raise ValueError('Invalid carried object/support names')
        self._object_names, self._support_names = objects, supports

    def load_collision_model(self, scene_model, env_idx=0):
        if env_idx != 0 or isinstance(scene_model, list) or scene_model is None:
            raise ValueError('Measured contact groups require one explicit world')
        if scene_model.mesh or scene_model.voxel:
            raise ValueError('Contact groups require the complete cuboid world')
        names = [c.name for c in scene_model.cuboid or []]
        if (len(set(names)) != len(names) or self._object_names & set(names)
                or not self._support_names <= set(names)):
            raise ValueError('Contact pair geometry absent or duplicated in static world')
        if hasattr(self, '_groups'):
            for index, group in enumerate(self._groups):
                excluded = self._support_names if index == 1 else set()
                group.load_collision_model(replace(scene_model, cuboid=[
                    c for c in scene_model.cuboid or [] if c.name not in excluded]))
        super().load_collision_model(scene_model, env_idx)

    def _query(self, method, state, collision_buffer, **kwargs):
        if not hasattr(self, '_groups') or not self._support_names:
            raise ValueError('Attachment groups must be configured before collision queries')
        spheres = state.robot_spheres
        if spheres.shape[-2] != len(self._masks[0]):
            raise ValueError('Attachment sphere mapping changed')
        results = []
        for i, (group, mask) in enumerate(zip(self._groups, self._masks)):
            key = (id(collision_buffer), i)
            buffer = self._buffers.get(key)
            if buffer is None:
                buffer = self._buffers[key] = CollisionBuffer.from_shape(spheres.shape, self.device_cfg)
            buffer.resize(spheres.shape, self.device_cfg)
            query = torch.cat((spheres[..., :3], torch.where(
                mask, spheres[..., 3], -100.).unsqueeze(-1)), dim=-1)
            results.append(getattr(group.checker, method)(
                scene=group.data, query_sphere=query, collision_buffer=buffer, **kwargs))
        return results[0] + results[1]

    def get_sphere_distance(self, state, collision_buffer, weight, activation_distance,
                            env_query_idx=None, return_loss=False):
        return self._query('get_sphere_distance', state, collision_buffer, weight=weight,
                           activation_distance=activation_distance,
                           env_query_idx=env_query_idx, return_loss=return_loss)

    def get_sphere_collision(self, state, collision_buffer, weight, activation_distance,
                             env_query_idx=None, return_loss=False):
        return self._query('get_sphere_collision', state, collision_buffer, weight=weight,
                           activation_distance=activation_distance,
                           env_query_idx=env_query_idx, return_loss=return_loss)

    def get_swept_sphere_distance(self, state, collision_buffer, weight, activation_distance,
                                  trajectory_dt, enable_speed_metric=False,
                                  env_query_idx=None, return_loss=False):
        return self._query('get_swept_sphere_distance', state, collision_buffer,
                           weight=weight, activation_distance=activation_distance,
                           trajectory_dt=trajectory_dt, enable_speed_metric=enable_speed_metric,
                           env_query_idx=env_query_idx, return_loss=return_loss)

    def get_swept_sphere_collision(self, state, collision_buffer, weight, activation_distance,
                                   trajectory_dt, enable_speed_metric=False,
                                   env_query_idx=None, return_loss=False):
        return self._query('get_swept_sphere_collision', state, collision_buffer,
                           weight=weight, activation_distance=activation_distance,
                           trajectory_dt=trajectory_dt, enable_speed_metric=enable_speed_metric,
                           env_query_idx=env_query_idx, return_loss=return_loss)


def configure_scene(scene, kinematics, attachment, cache, world):
    """Bind only the measured link's live sphere slots to both collision groups."""
    params = kinematics.config.kinematics_config
    try:
        indices = params.get_sphere_index_from_link_name(attachment.link).cpu().tolist()
    except (KeyError, AttributeError) as exc:
        raise ValueError(f'Carried link {attachment.link!r} absent from official kinematics') from exc

    def extent(values):
        return 'empty' if not values else f'{min(values)}..{max(values)}'

    count = attachment.sphere_count
    declared_radii = torch.as_tensor(attachment.spheres)[:, 3]
    expected = indices[:count]
    actual = []
    layout_matches = False
    spheres = params.link_spheres
    if (spheres is not None and spheres.ndim == 3
            and spheres.shape[-2] == params.total_spheres
            and all(type(i) is int and 0 <= i < params.total_spheres for i in indices)):
        radii = spheres[:, indices, 3]
        if torch.isfinite(radii).all():
            active = radii > 0
            actual = [index for index, live in zip(indices, active[0].tolist()) if live]
            expected_active = torch.zeros(len(indices), dtype=torch.bool, device=active.device)
            expected_active[:count] = True
            layout_matches = bool(torch.all(active == expected_active))
    valid_declared = (0 < count <= len(declared_radii)
                      and torch.isfinite(declared_radii).all()
                      and bool((declared_radii[:count] > 0).all())
                      and bool((declared_radii[count:] <= 0).all()))
    if (not valid_declared or len(indices) < count or len(set(indices)) != len(indices)
            or not layout_matches or actual != expected or len(actual) != count):
        raise ValueError(
            f'Carried sphere topology differs for link {attachment.link!r}: '
            f'expected active={count}, actual active={len(actual)}, '
            f'expected indices={extent(expected)}, actual indices={extent(actual)}, '
            f'link slots={len(indices)} ({extent(indices)}), total slots={params.total_spheres}, '
            f'declared capacity={len(declared_radii)}')
    if hasattr(scene, '_attached_indices'):
        if tuple(sorted(actual)) != scene._attached_indices or params.total_spheres != len(scene._masks[0]):
            raise ValueError(
                f'Planner/checker carried sphere mapping differs for link {attachment.link!r}: '
                f'expected active={len(scene._attached_indices)} '
                f'({extent(scene._attached_indices)}), actual active={len(actual)} '
                f'({extent(actual)}), expected total={len(scene._masks[0])}, '
                f'actual total={params.total_spheres}')
    else:
        scene.configure(actual, params.total_spheres, cache)
    names = {c.name for c in world.cuboid or []}
    if not attachment.supports <= names or attachment.names & names:
        raise ValueError('Carried/static world accounting differs')
    scene.attachment(sorted(attachment.names), sorted(attachment.supports))
    scene.load_collision_model(world)
    return len(actual)


from curobo.motion_planner import MotionPlanner


class CarriedMotionPlanner(MotionPlanner):
    """Construct the official solvers around one carried-aware scene before warmup."""

    def __init__(self, config, attachment, world):
        self._carried_attachment = attachment
        self._carried_world = world
        super().__init__(config)

    def destroy(self):
        """Release whichever official solvers completed construction."""
        if getattr(self, '_destroyed', False):
            return
        self._destroyed = True
        for name in ('ik_solver', 'trajopt_solver'):
            solver = getattr(self, name, None)
            if solver is not None:
                solver.destroy()
        graph = getattr(self, 'graph_planner', None)
        if graph is not None and hasattr(graph, 'reset_cuda_graph'):
            graph.reset_cuda_graph()

    def _initialize_components(self):
        from curobo._src.solver.solver_ik import IKSolver
        from curobo._src.solver.solver_trajopt import TrajOptSolver
        from curobo._src.graph_planner.graph_planner_prm import PRMGraphPlanner
        cfg = self.config
        if cfg.scene_collision_cfg is None:
            raise ValueError('Carried planning requires a scene collision config')
        self.scene_collision_checker = CarriedSceneCollision.from_config(cfg.scene_collision_cfg)
        self.ik_solver = IKSolver(cfg.ik_solver_config, self.scene_collision_checker)
        configure_scene(self.scene_collision_checker, self.ik_solver.kinematics,
                        self._carried_attachment, cfg.scene_collision_cfg.cache,
                        self._carried_world)
        self.trajopt_solver = TrajOptSolver(cfg.trajopt_solver_config,
                                           self.scene_collision_checker)
        self.graph_planner = None
        if cfg.graph_planner_config is not None:
            self.graph_planner = PRMGraphPlanner(cfg.graph_planner_config,
                                                self.scene_collision_checker)
