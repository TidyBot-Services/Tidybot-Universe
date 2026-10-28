#!/usr/bin/env python3
"""Private pipe worker, invoked with locked Python -B -I; no socket/server."""
import json
import os
from pathlib import Path
import sys


def load_bootstrap(bootstrap):
    """Use the frozen modules with the owner's pinned trust policy, as V2Service does."""
    raw = json.loads(Path(bootstrap['lock_path']).read_text())
    sys.path.insert(0, raw['project_root'])
    from curobo_v2 import identity as trust
    registry = Path(bootstrap['trust_registry'])
    if (not registry.is_absolute()
            or trust.digest(registry) != bootstrap['trust_registry_sha256']):
        raise trust.IdentityError('Owner deployment registry digest mismatch')
    trust.DEPLOYMENT_REGISTRY = registry
    trust._registry_digest = bootstrap['trust_registry_sha256']
    lock, digest = trust.load_lock(bootstrap['lock_path'], tier='dev', boundary='dev_loop')
    if digest != bootstrap['lock_sha256']:
        raise trust.IdentityError('planner pin changed')
    return lock, digest


def refuse_keys_env():
    def audit(event, args):
        if (event == 'open' and args and isinstance(args[0], (str, bytes, os.PathLike))
                and os.path.basename(os.fsdecode(args[0])) == 'keys.env'):
            raise PermissionError('T1b never reads keys.env')
    sys.addaudithook(audit)


def main():
    # Read bootstrap before importing any planner module. The frozen worker uses
    # the owner's registry, not a registry from inside the immutable snapshot.
    bootstrap = json.loads(sys.stdin.readline())
    lock, digest = load_bootstrap(bootstrap)
    from curobo_v2.identity import authorize_service_import, install_import_guard
    config = str(Path(lock['project_root'])/'config/openarm_fixed.yml')
    authorize_service_import(lock, config)
    import curobo._src.runtime as runtime
    runtime.kernel_backend = 'cuda_core'
    runtime.cuda_core_backend = True
    runtime.cache_dir = str(Path(bootstrap['out'])/'runtime_cache')
    from curobo._src.curobolib.backends import get_backend_name
    if get_backend_name() != 'cuda_core':
        raise ValueError('locked cuda_core backend required')
    install_import_guard()
    # Match the service worker's explicit configuration, never inherited defaults.
    for key in list(os.environ):
        if key.startswith('CUROBO_'):
            del os.environ[key]
    os.environ.update(CUROBO_ROBOT_CFG=config, CUROBO_ASSETS_DIR=lock['assets_root'],
                      CUROBO_USE_CUDA_GRAPH='1', CUROBO_DEVICE='cuda:0')
    from curobo_v2.sweep import configure_evidence
    configure_evidence(Path(bootstrap['out']))
    from curobo_v2.service.curobo_service.planner import PlannerAdapter
    from curobo_v2.service.curobo_service.world import WorldStore
    from curobo_v2.execution_settings import minimum_arm_margin
    adapter = PlannerAdapter('cuda:0', WorldStore(max_envs=1))
    core = adapter._planner
    def send(value):
        output.write(json.dumps(value, allow_nan=False)+'\n'); output.flush()
    send(dict(ready=True, names=core.joint_names, limits=core.limits.tolist(),
        tools=core.tool_frames, locks=list(core._locks),
        binding=core.settings['native_chassis'], lock_sha256=digest))
    for line in sys.stdin:
        request = json.loads(line)
        if request['operation'] == 'close':
            return
        body = request['body']
        adapter.push_world(body['env_id'], request['world'])
        if request['operation'] == 'fk':
            send(adapter.fk(body['env_id'], body['current_q'], body['joint_names'],
                body['locked_joints'], body['active_joints'], execution_settings=body['execution_settings']))
            continue
        adapter.warmup(**body, tool_frames=request.get('tool_frames'))
        if request['operation'] == 'joint':
            response = adapter.plan_joints(**body, target_q=request['target_q'])
        else:
            response = adapter.plan_ik_path(**body, targets=request['targets'],
                ik_candidates=request['ik_candidates'], max_joint_jump=request['max_joint_jump'],
                **({'grasp_search': request['grasp_search']} if request.get('grasp_search') else {}))
        trajectory = response.get('trajectory')
        supplemental = None
        if trajectory and request.get('distractor_world'):
            from curobo_v2.sweep import certify_trajectory
            # Match the core's locked inactive joints and finger state.
            from curobo_v2.execution_settings import from_config
            core.execution_settings = from_config(body['execution_settings'])
            selected, _ = core._select(core.named(body['current_q']), body['active_joints'],
                tool_frames=request.get('tool_frames'), locked_joints=body['locked_joints'])
            locks = next(model[3] for model in core._models.values() if model[0] is selected)
            limits = core._config['kinematics']['cspace']
            certificate, motion = certify_trajectory(response,
                urdf=core._config['kinematics']['urdf_path'], world=request['distractor_world'],
                locks=locks, attachment=body['attachment'], position_limits=core.limits,
                maximum_speed={n: float(core._joints[n].find('limit').get('velocity')) for n in core.joint_names},
                max_acceleration=float(limits['max_acceleration']), max_jerk=float(limits['max_jerk']),
                execution_settings=core.execution_settings)
            core.execution_settings = None
            supplemental = dict(geometry=certificate, motion=motion)
            if certificate['result'] != 'PASS' or motion['result'] != 'PASS':
                response['status'] = 'failed'
                response['reason'] = 'carried_distractor_sweep_rejected'

        margins = []
        if trajectory:
            margins = [minimum_arm_margin(q, core.joint_names, core.limits, body['active_joints'])['minimum_joint_margin_rad']
                       for q in trajectory]
        # Failed IK margins are valuable evidence; never replace them by zero.
        failed_margins = [m['minimum_joint_margin_rad'] for m in response.get('candidate_joint_margins', []) if m]
        validation = response.get('validation') or {}
        goal_margin = validation.get('goal_joint_margin') or {}
        if goal_margin.get('minimum_joint_margin_rad') is not None:
            failed_margins.append(goal_margin['minimum_joint_margin_rad'])
        accounting = validation.pop('geometry_accounting', None)
        if accounting:
            validation['geometry_accounting_counts'] = {
                k: len(v) if isinstance(v, list) else v for k, v in accounting.items()}
        certificate = validation.get('continuous_geometry') or {}
        send(dict(success=response['status'] == 'success' and bool(trajectory),
            failure_reason=response.get('reason'),
            endpoint_q=trajectory[-1] if trajectory else None,
            min_joint_margin_rad=min(margins or failed_margins, default=None),
            min_clearance_m=None, certified_clearance_m=certificate.get('clearance_m') if certificate.get('result') == 'PASS' else None,
            validation=validation, distractor_sweep=supplemental, solver_diagnostics=response.get('solver_diagnostics'),
            candidate_joint_margins=response.get('candidate_joint_margins'),
            candidate_reasons=response.get('ik_candidate_reasons'),
            heading_search={k: v for k, v in response.items() if 'heading' in k},
            sample_count=len(trajectory or [])))


if __name__ == '__main__':
    refuse_keys_env()
    # Keep native libraries and their stdout out of the pipe protocol.
    output = os.fdopen(os.dup(sys.stdout.fileno()), 'w', buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    main()
