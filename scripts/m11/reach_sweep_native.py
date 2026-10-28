"""Operator-only native reset and FK on independent MjData; no env.step calls."""
from collections import OrderedDict
from copy import copy, deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
from scipy.spatial.transform import Rotation


class DataView:
    """Names used by the executor's robosuite wrapper, over copied native data."""
    def __init__(self, data):
        self._data = data

    def __getattr__(self, name):
        return getattr(self._data, {'body_xpos': 'xpos', 'body_xmat': 'xmat',
                                   'body_xquat': 'xquat'}.get(name, name))


class NativeSweep:
    def __init__(self, probe, probe_path, out, source):
        self.probe, self.probe_path, self.out, self.source = probe, probe_path, out, source.resolve()
        self.original = self.process = self.log = None
        self.cache = OrderedDict()

    def __enter__(self):
        try:
            self.start()
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def start(self):
        from curobo_v2.execution_settings import from_config
        from curobo_v2.identity import load_lock, verify_files
        from curobo_v2 import identity
        from openarm365_dev_v2.environment import create_env, joint_address
        from openarm365_dev_v2.scene_contract import capture_state, fingerprint
        from openarm365.environment import validate_physical_model
        from benchmarks.attention_harness.diagnostics.object_proxy_measure import writable_mjcf_assets
        from scripts.m11_cabinet_probe import geometry, write_json
        self.settings = from_config(self.probe['execution_settings'])
        if self.settings.profile != 'r4-20261027':
            raise ValueError('r4 required')
        pin = self.settings.planner_deployment
        lock, sha = load_lock(pin['lock_path'], tier='dev', boundary='dev_loop')
        verify_files(lock)
        if sha != pin['sha256']:
            raise ValueError('probe planner pin differs')
        # Reuse the existing immutable probe deployment; never materialize a new
        # deployment, rewrite a lock, or import an unpinned planner implementation.
        config = json.loads(self.probe_path.with_name('config.json').read_text())
        native_ref = config['dev_lock']
        native_raw = Path(native_ref['path']).read_bytes()
        if hashlib.sha256(native_raw).hexdigest() != native_ref['sha256']:
            raise ValueError('probe native identity changed')
        native_lock = json.loads(native_raw)
        # Planner files are pinned by the CUDA deployment; native environment,
        # base and attachment code are pinned separately by the Dev identity.
        matched = {}
        runtime_root = Path(__file__).resolve().parents[2]
        for path in sorted((self.source/'curobo_v2').rglob('*.py')):
            relative = path.relative_to(self.source)
            expected = lock['files'].get(str(Path(lock['project_root'])/relative))
            for root in {self.source, runtime_root}:
                actual = hashlib.sha256((root/relative).read_bytes()).hexdigest()
                if actual != expected:
                    raise ValueError('planner-source differs from probe deployment: '+str(relative))
            matched[str(relative)] = expected
        from openarm365_dev_v2.dev_config import verify_lock
        verify_lock(native_ref, expected_source_root=str(self.source))
        if runtime_root != self.source:
            for name, expected in native_lock['files'].items():
                path = Path(name)
                if path.is_relative_to(self.source) and path.suffix == '.py':
                    relative = path.relative_to(self.source)
                    if hashlib.sha256((runtime_root/relative).read_bytes()).hexdigest() != expected:
                        raise ValueError('executing native source differs: '+str(relative))
        if (config['task_id'] != self.probe['task_id'] or config['seed'] != self.probe['seed']
                or config['execution_settings'] != self.probe['execution_settings']
                or config['planner_mode'] != 'ik_path'):
            raise ValueError('probe config mismatch / unsupported planner mode')
        self.config = config
        for key, folder in [('XDG_CACHE_HOME', 'cache'), ('NUMBA_CACHE_DIR', 'numba-cache'),
                            ('CUDA_CACHE_PATH', 'cuda-cache')]:
            os.environ[key] = str(self.out/folder)
        with writable_mjcf_assets(self.out):
            self.original = create_env(config['official_env'], robots='OpenArm365', seed=config['seed'],
                obj_instance_split=config['obj_instance_split'], layout_and_style_ids=[(11, 34)],
                camera_widths=128, camera_heights=128, randomize_cameras=False,
                generative_textures=None, control_freq=config['control_freq'], horizon=config['horizon'],
                scene_reset_seed=config['seed'])
            self.original.reset()
            validate_physical_model(self.original)
            state = capture_state(self.original)
            if fingerprint(state, True) != self.probe['resets'][0]['normalized_sha256']:
                raise ValueError('canonical reset differs from probe')
            fresh = geometry(self.original)
            for key in ('mount_pose_world',):
                if not np.allclose(fresh[key], self.probe['resets'][0]['scene'][key], atol=1e-7, rtol=0):
                    raise ValueError('reset geometry differs: '+key)
        self.reset_data = copy(self.original.sim.data._data)
        self.env = SimpleNamespace(sim=SimpleNamespace(model=self.original.sim.model,
            data=DataView(copy(self.reset_data))), robots=self.original.robots,
            obj_body_id=self.original.obj_body_id)
        self.base_indexes = self.env.robots[0]._ref_base_joint_pos_indexes
        self.base_initial = self.reset_data.qpos[self.base_indexes].copy()
        self.log = (self.out/'planner.log').open('w')
        worker = Path(__file__).with_name('reach_sweep_worker.py')
        environment = {k: v for k, v in os.environ.items() if not k.startswith(('PYTHON', 'CUROBO_'))
                       and k not in ('LD_PRELOAD', 'LD_LIBRARY_PATH')
                       and not any(x in k.upper() for x in ('TOKEN', 'SECRET', 'KEY', 'PASSWORD'))}
        environment.update(XDG_CACHE_HOME=str(self.out/'cache'), MPLCONFIGDIR=str(self.out/'mpl-cache'),
                           CUDA_CACHE_PATH=str(self.out/'cuda-cache'))
        trust_fields = dict(trust_registry=str(identity.DEPLOYMENT_REGISTRY),
                           trust_registry_sha256=identity.digest(identity.DEPLOYMENT_REGISTRY))
        self.process = subprocess.Popen([lock['python'], '-B', '-I', str(worker)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True,
            env=environment, cwd=lock['project_root'])
        self.send(dict(lock_path=pin['lock_path'], lock_sha256=sha, out=str(self.out.resolve()), **trust_fields))
        self.info = self.receive()
        self.names = self.info['names']; self.binding = self.info['binding']
        self.addresses = [joint_address(self.env.sim, n) for n in self.names]
        self.lock_addresses = {n: joint_address(self.env.sim, n) for n in self.info['locks']}
        self.begin({})
        from openarm365_dev.environment import mount_pose, tcp_pose
        result = self.request('fk', self.names)
        local = np.linalg.inv(mount_pose(self.env))
        for side in ('left', 'right'):
            got = result['poses'][f'openarm_{side}_hand_tcp']; actual = local@tcp_pose(self.env, side)
            rotation = Rotation.from_quat(np.array(got['quaternion_wxyz'])[[1, 2, 3, 0]]).as_matrix()
            if (np.linalg.norm(actual[:3, 3]-got['position']) > 1e-5 or
                    Rotation.from_matrix(rotation@actual[:3, :3].T).magnitude() > 1e-5):
                raise ValueError('native/planner TCP alignment mismatch')
        self.receipt = dict(planner_lock_sha256=sha, planner_source=str(self.source), **trust_fields,
            matched_sources=matched, execution_settings=self.settings.to_dict(), resets=1,
            physics_motion_steps=0, transport='private stdio, no network listener',
            active_gripper_assumption_m=.043, carried_gripper_assumption='open aperture retained; closure untested',
            normalized_sha256=fingerprint(state, True), alignment='both physical TCPs within 1e-5')
        write_json(self.out/'runtime.json', self.receipt)

    def send(self, value):
        self.process.stdin.write(json.dumps(value, allow_nan=False)+'\n'); self.process.stdin.flush()

    def receive(self):
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError('offline planner exited; see '+str(self.out/'planner.log'))
        return json.loads(line)

    def __exit__(self, *unused):
        if self.process:
            try:
                if self.process.poll() is None:
                    self.send({'operation': 'close'})
                    self.process.wait(timeout=5)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                self.process.kill(); self.process.wait()
            self.process.stdin.close(); self.process.stdout.close()
        if self.log:
            self.log.close()
        if self.original:
            self.original.close()

    def forward(self):
        import mujoco
        mujoco.mj_forward(self.env.sim.model._model, self.env.sim.data._data)

    def begin(self, row):
        self.env.sim.data = DataView(copy(self.reset_data))
        self.held = self.relative_object = None
        self.retention = {'attach_request_id': 'hypothetical-offline-grasp', 'supports': None}
        self.history = ()

    def world(self, *, complete=False, heading=False):
        from openarm365_dev.world import snapshot, movable_object_roots
        if complete or self.config['contact_exempt_objects'] == 'none':
            return snapshot(self.env, chassis_binding=self.binding)
        roots = movable_object_roots(self.env)
        attached = self.held['target_id'] if self.held else roots['obj'] if heading else None
        return snapshot(self.env, chassis_binding=self.binding, exempt_body_ids=set(roots.values()), attached_body_id=attached)

    def request(self, operation, active, **extra):
        from openarm365_dev.attachment import planner_context, retain_declared_supports
        world = self.world(heading=bool(extra.get('grasp_search')))
        context = planner_context(self.env, self.held, world, self.info['tools'])
        if context:
            context = retain_declared_supports(context, self.retention, world['inventory'])
        body = dict(env_id='robocasa365', current_q=self.env.sim.data.qpos[self.addresses].tolist(),
            joint_names=self.names, active_joints=active,
            locked_joints={n: float(self.env.sim.data.qpos[a]) for n, a in self.lock_addresses.items()},
            execution_settings=self.settings.to_dict())
        if operation != 'fk':
            body['attachment'] = context
        if extra.get('grasp_search'):
            extra['grasp_search']['attachment'] = planner_context(self.env,
                dict(holder=self.side, target_id=self.env.obj_body_id['obj']), world, self.info['tools'])
        # The executor exempts other movable objects from arm IK. Preserve that
        # exact primary world, and separately certify carried trajectories against
        # all distractors so a nominal cabinet pass cannot intersect distr_cab.
        extra_world = None
        if self.held:
            from openarm365_dev.world import snapshot
            extra_world = snapshot(self.env, chassis_binding=self.binding,
                exempt_body_ids={self.held['target_id']}, attached_body_id=self.held['target_id'])['cuboids']
        self.send(dict(operation=operation, body=body, world=world['cuboids'],
                       distractor_world=extra_world, **extra))
        return self.receive()

    def cached(self, key, operation):
        key = self.history + (json.dumps(key, sort_keys=True),)
        if key in self.cache:
            result, qpos, held, relative, retention = self.cache.pop(key)
            self.env.sim.data.qpos[:] = qpos
            self.held, self.relative_object, self.retention = deepcopy((held, relative, retention))
            self.forward()
        else:
            result = operation()
            qpos = self.env.sim.data.qpos.copy()
            held, relative, retention = deepcopy((self.held, self.relative_object, self.retention))
        self.cache[key] = (result, qpos, held, relative, retention)
        while len(self.cache) > 2048:
            self.cache.popitem(last=False)
        self.history = key
        return deepcopy(result)

    def set_endpoint(self, result):
        if result['success']:
            self.env.sim.data.qpos[self.addresses] = result['endpoint_q']
            self.forward()
            self.move_held()
        return result

    def tuck(self, row):
        def operation():
            target = self.env.sim.data.qpos[self.addresses].copy()
            for i, value in enumerate(row['tuck_q'], 1):
                target[self.names.index(f"openarm_{row['tuck_arm']}_joint{i}")] = value
            # SDK bimanual plans all 14 joints; retain the other seven targets.
            return self.set_endpoint(self.request('joint', self.names, target_q=target.tolist()))
        return self.cached(['tuck', row['tuck_arm'], row['tuck_q']], operation)

    def base_path(self, row):
        from openarm365_dev.base_motion import check_exact_base_path, BasePathRejected, base_joint_delta
        def operation():
            audits = []
            for delta in row['base_moves'] or [[0., 0., 0.]]:
                try:
                    audit = check_exact_base_path(self.env, self.world(complete=True), self.binding,
                        delta, self.base_initial, held_body_id=self.held['target_id'] if self.held else None,
                        execution_settings=self.settings)
                except BasePathRejected as exc:
                    audits.append(exc.collision_check)
                    return dict(success=False, failure_reason='BasePathRejected', detail=str(exc),
                        min_joint_margin_rad=None, min_clearance_m=min(a['minimum_clearance_m'] for a in audits if a['minimum_clearance_m'] is not None),
                        segments=audits)
                audits.append(audit)
                before = self.env.sim.data.qpos[self.base_indexes].copy()
                self.env.sim.data.qpos[self.base_indexes] = before+base_joint_delta(delta, before, self.base_initial)
                self.forward(); self.move_held()
            return dict(success=True, failure_reason=None, min_joint_margin_rad=None,
                min_clearance_m=min((a['minimum_clearance_m'] for a in audits if a['minimum_clearance_m'] is not None), default=None), segments=audits)
        return self.cached(['base', row['base_moves']], operation)

    def attach(self, row):
        from openarm365_dev.environment import tcp_pose
        from openarm365_dev.attachment import object_pose
        self.held = dict(holder=row['arm'], target_id=self.env.obj_body_id['obj'])
        self.relative_object = np.linalg.inv(tcp_pose(self.env, row['arm']))@object_pose(self.env, self.held['target_id'])
        # Capture supports with the first attached request, exactly as executor.
        self.history += ('attach:'+row['arm'],)

    def move_held(self):
        if self.held is None:
            return
        from openarm365_dev.environment import tcp_pose
        pose = tcp_pose(self.env, self.held['holder'])@self.relative_object
        model = self.env.sim.model
        address = int(model.jnt_qposadr[int(model.body_jntadr[self.held['target_id']])])
        self.env.sim.data.qpos[address:address+3] = pose[:3, 3]
        self.env.sim.data.qpos[address+3:address+7] = Rotation.from_matrix(pose[:3, :3]).as_quat()[[3, 0, 1, 2]]
        self.forward()

    def waypoint(self, stage, row):
        self.side = row['arm']
        def operation():
            if stage == 'pregrasp':
                self.open_hand(row['arm'])
            return self.trace_waypoint(stage, row)
        return self.cached([stage, row['arm'], row['waypoints'], row['grasp_quat_xyzw']], operation)

    def open_hand(self, side):
        """Hypothetical open endpoint, matching the policy's gripper.open call.

        No closure/contact pose is invented. The open aperture remains locked
        for conditional carried checks; actual closure needs native execution.
        """
        from openarm365_dev_v2.environment import PHYSICAL_TO_NATIVE
        endpoints = self.config['gripper_command_endpoints_m']
        if endpoints != [.001, .043]:
            raise ValueError('unexpected gripper command endpoints')
        indexes = self.env.robots[0]._ref_gripper_joint_pos_indexes[PHYSICAL_TO_NATIVE[side]]
        self.env.sim.data.qpos[indexes] = endpoints[1]
        self.forward()

    def trace_waypoint(self, stage, row):
        from openarm365_dev.environment import tcp_pose, mount_pose
        side = row['arm']; tool = f'openarm_{side}_hand_tcp'
        active = [f'openarm_{side}_joint{i}' for i in range(1, 8)]
        target = np.array(row['waypoints'][stage])
        samples = []
        def plan(position, rotation, search=False):
            local = np.linalg.inv(mount_pose(self.env))
            def target_pose(p):
                return {tool: dict(position=(local@np.r_[p, 1])[:3].tolist(),
                    quaternion_wxyz=Rotation.from_matrix(local[:3, :3]@rotation).as_quat()[[3, 0, 1, 2]].tolist())}
            extra = dict(targets=target_pose(position), tool_frames=[tool],
                ik_candidates=self.config['ik_candidates'], max_joint_jump=self.config['ik_max_joint_jump'])
            if search:
                extra['grasp_search'] = dict(lift_targets=target_pose(row['waypoints']['lift']),
                    place_targets=target_pose(row['waypoints']['place']),
                    world_vertical=(local[:3, :3]@np.array([0., 0., 1.])).tolist())
            result = self.request('pose', active, **extra)
            self.set_endpoint(result)
            samples.append(dict(target_world_m=position.tolist(), **result))
            return result['success']
        # Match SDK translation calls and preserve the current orientation.
        for _ in range(160):
            current = tcp_pose(self.env, side)
            delta = target-current[:3, 3]; distance = np.linalg.norm(delta)
            if distance < .001:
                break
            step = .015 if stage == 'grasp' else .029
            final = stage == 'grasp' and distance <= step
            if final and delta[2] >= 0:
                return aggregate(samples, 'grasp_search_requires_final_descent')
            if not plan(current[:3, 3]+delta*min(1., step/distance), current[:3, :3], search=final):
                return aggregate(samples)
            if final:
                break
        else:
            return aggregate(samples, 'waypoint_failed_to_converge')
        if stage == 'pregrasp':
            goal = Rotation.from_quat(row['grasp_quat_xyzw']).as_matrix()
            for _ in range(60):
                current = tcp_pose(self.env, side)
                rv = Rotation.from_matrix(goal@current[:3, :3].T).as_rotvec()
                angle = np.linalg.norm(rv)
                if angle <= .01:
                    break
                previous = current.copy()
                if np.linalg.norm(target-current[:3, 3]) > .05:
                    return aggregate(samples, 'orientation_drift')
                if not plan(target, Rotation.from_rotvec(rv*min(1., .13/angle)).as_matrix()@current[:3, :3]):
                    return aggregate(samples)
                now = tcp_pose(self.env, side)
                if Rotation.from_matrix(now[:3, :3]@previous[:3, :3].T).magnitude() < 1e-5:
                    # Generator returns here too; report the remaining error.
                    break
            else:
                return aggregate(samples, 'orientation_failed_to_converge')
        result = aggregate(samples)
        result['reached_world_m'] = tcp_pose(self.env, side)[:3, 3].tolist()
        result['reached_quat_xyzw'] = Rotation.from_matrix(tcp_pose(self.env, side)[:3, :3]).as_quat().tolist()
        result['requested_orientation_error_rad'] = float((Rotation.from_quat(row['grasp_quat_xyzw'])*Rotation.from_quat(result['reached_quat_xyzw']).inv()).magnitude())
        return result


def aggregate(samples, failure=None):
    margins = [s['min_joint_margin_rad'] for s in samples if s.get('min_joint_margin_rad') is not None]
    return dict(success=failure is None and all(s['success'] for s in samples),
        failure_reason=failure or next((s['failure_reason'] for s in samples if not s['success']), None),
        min_joint_margin_rad=min(margins, default=None), min_clearance_m=None,
        certified_clearance_m=min((s['certified_clearance_m'] for s in samples if s.get('certified_clearance_m') is not None), default=None),
        samples=samples)
