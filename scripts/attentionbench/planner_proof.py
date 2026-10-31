#!/usr/bin/env python3
"""Local build evidence only: direct d06 adapter planning, no trust decision.

Run with the freshly built environment's Python using -B -I. This script does
not call identity.load_lock, register a deployment, or start V2Service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(library, lock_path, requests, output):
    lock = json.loads(Path(lock_path).read_bytes())
    project = Path(lock['project_root']).resolve()
    library = Path(library).resolve()
    if library != Path(lock['library_root']).resolve():
        raise ValueError('Official cuRobo source differs from the d06 lock')
    sys.path.insert(0, str(project))
    sys.path.insert(0, str(library))
    import curobo
    if Path(curobo.__file__).resolve() != library / 'curobo/__init__.py':
        raise ValueError('Official cuRobo import resolved outside the built checkout')
    import yaml
    from curobo_v2 import identity
    spec = json.loads(Path(requests).read_bytes())
    config = (project / spec['robot_config']).resolve()
    if not config.is_relative_to(project):
        raise ValueError('Proof robot configuration escapes the d06 project')
    # Authorize a *process-local import context* from the built lock. This
    # validates runtime bytes but does not approve the lock for service use.
    identity.authorize_service_import(lock, str(config))
    from curobo_v2.service.curobo_service.planner import PlannerAdapter
    from curobo_v2.service.curobo_service.world import WorldStore

    doc = yaml.safe_load(config.read_text())
    names = doc['service']['joint_names']
    cspace = doc['robot_cfg']['kinematics']['cspace']
    values = dict(zip(cspace['joint_names'], cspace['default_joint_position']))
    q = [float(values[name]) for name in names]
    adapter = PlannerAdapter('cuda:0', WorldStore(8))
    adapter.push_world(spec['env_id'], **spec['world'])
    results = []
    for item in spec['plans']:
        target = q.copy()
        target[item['joint_index']] += item['delta_rad']
        request = {'env_id': spec['env_id'], 'current_q': q, 'target_qpos': target,
                   'joint_names': names}
        result = adapter.plan_joints(spec['env_id'], q, target, joint_names=names)
        if result.get('status') != 'success':
            raise ValueError('plan request did not succeed: ' + str(result.get('reason')))
        results.append({'request': request, 'result': result})
    raw = json.dumps(results, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'passed': True, 'request_set_sha256': sha(requests),
                                  'result_sha256': hashlib.sha256(raw).hexdigest()}, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('library', 'lock', 'requests', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    run(args.library, args.lock, args.requests, args.output)


if __name__ == '__main__':
    main()
