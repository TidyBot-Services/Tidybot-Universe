"""Fixture pilots, native by default; --policy also accepts custom native policies.

Every task accepts --seed. open_cabinet seed 101 starts in planner collision:
the chassis is only 1.4 mm from a drawer handle (5 mm planner clearance).
Select another seed after checking its initial planner state.
"""
import argparse
import hashlib
import json
from pathlib import Path


def start_probe_report(out, result):
    rows=[]
    for path in sorted(out.glob('openarm365-dev-*/rpc-receipts.jsonl')):
        for line in path.read_text().splitlines():
            row=json.loads(line);request=row.get('request',{});response=row.get('response',{})
            value=response.get('value');value=value if isinstance(value,dict) else {}
            contacts=[]
            def exact(node):
                if isinstance(node,dict):
                    geometry=node.get('exact_geometry',{})
                    contacts.extend(c['bodies'] for c in geometry.get('contacts',[]) if 'bodies' in c)
                    for key,child in node.items():
                        if key!='exact_geometry':exact(child)
                elif isinstance(node,list):
                    for child in node:exact(child)
            exact(response)
            rows.append(dict(request_id=request.get('request_id'),group=request.get('group'),
                operation=request.get('operation'),args=request.get('args'),
                ok=response.get('ok') is True and value.get('ok',True) is not False and not value.get('rejected',False),
                reason=value.get('reason') or response.get('error') or response.get('reason'),
                exact_geometry_contact_bodies=contacts))
    report=dict(status=result.get('status'),reason=result.get('error'),rpcs=rows,
        move_rpc_count=sum(r['group']=='bimanual' and r['operation']=='move_joints' for r in rows))
    (out/'start_probe.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')


def main(argv=None):
    from curobo_v2.fixture_interaction import PROFILE,PILOT_TASKS,PILOT_SEEDS,DEFAULT_PILOT_TASKS
    from .dev_config import ROOT,SIM_PYTHON,new_execution_settings,make_lock,make_dev_config,validate_dev_config
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task',required=True,choices=PILOT_TASKS)
    parser.add_argument('--seed',type=int,help='screened seed; defaults to the first screened seed for the task')
    parser.add_argument('--out',type=Path,required=True)
    policy_choice=parser.add_mutually_exclusive_group()
    policy_choice.add_argument('--policy',type=Path,help='required for tasks without a built-in default policy')
    policy_choice.add_argument('--probe-start',action='store_true',help='probe joint 1 of both arms by +0.05 rad and back')
    parser.add_argument('--agent-mode',action='store_true')
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args(argv)
    if args.seed is None:args.seed=PILOT_SEEDS[args.task][0]
    if args.seed not in PILOT_SEEDS[args.task]:parser.error('--seed must be a screened seed for '+args.task)
    if not args.policy and not args.probe_start and args.task not in DEFAULT_PILOT_TASKS:
        parser.error('--policy is required for '+args.task+' (or select --probe-start)')
    args.out=args.out.resolve();args.out.mkdir(parents=True,exist_ok=False)
    source=dict(kind='dev-local',source_root=str(ROOT))
    settings=new_execution_settings(planner_source=source,rules_profile=PROFILE)
    lock=make_lock(args.out/'dev-lock.json',execution_settings=settings,planner_source=source)
    config=make_dev_config(args.task,args.seed,lock,execution_settings=settings,planner_source=source)
    if args.agent_mode:config['dev_mode']='agent'
    validate_dev_config(config)
    config_path=args.out/'config.json';config_path.write_text(json.dumps(config,sort_keys=True,indent=2)+'\n')
    policy=args.policy or ROOT/'openarm365_dev_v2/policies'/('fixture_start_probe.py' if args.probe_start else
        'fixture_open_cabinet.py' if args.task=='open_cabinet' else args.task+'.py')
    policy_path=args.out/'policy.py';policy_path.write_bytes(policy.read_bytes())
    if args.agent_mode:
        from .agent_policy import validate_source
        validate_source(policy_path.read_text(),config=config)
    else:
        from openarm365_dev.policy_validation import validate_generated_policy
        validate_generated_policy(policy_path.read_text(),config=config)
    if args.prepare_only:return 0
    from benchmarks.attention_harness.formal_runner_boundary import FormalRunRequest
    from .runner import OpenArm365DevRunnerV2
    request=FormalRunRequest(suite='robocasa365',task_id=args.task,seed=args.seed,
        policy_code_path=policy_path,policy_sha256=hashlib.sha256(policy_path.read_bytes()).hexdigest(),
        config_path=config_path,config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
        artifact_root=args.out,overall_deadline_seconds=config['attempt_wall_seconds'],
        attempt_deadline_seconds=config['attempt_wall_seconds'],sdk_call_limit=config['sdk_call_limit'],
        run_id='run:fixture-pilot-'+args.out.name,attempt_id='attempt:fixture-pilot:0')
    request.validate()
    runner=OpenArm365DevRunnerV2(service_source_root=None,sim_python=str(SIM_PYTHON),
        sim_source_root=None,task_source_root=None,asset_manifest=None,agent_mode=args.agent_mode)
    result=runner.execute(request)
    (args.out/'result.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
    if args.probe_start:start_probe_report(args.out,result)
    return 0 if result.get('status')=='completed' else 1


if __name__=='__main__':raise SystemExit(main())
