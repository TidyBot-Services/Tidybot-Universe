"""Run scripted OpenArm365 Dev v2 motion trials against a real simulator.

Use --dry-run to inspect the matrix without creating an experiment or worker.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Trial:
    name: str
    arm: str
    axis: str
    delta: float
    settle: int
    open_first: bool = True


def default_trials():
    return [Trial(f'L+z-{delta:g}m-s{settle}', 'left', 'z', delta, settle)
            for delta in (0.0001, 0.001, 0.005, 0.01) for settle in (20, 60, 100)] + [
        Trial('R+z-0.01m-s20', 'right', 'z', 0.01, 20),
        Trial('R+z-0.001m-s60', 'right', 'z', 0.001, 60)]


def parse_trial(raw):
    try:
        name, arm, axis, delta, settle, *rest = raw.split(':')
        if len(rest)>1 or arm not in ('left','right') or axis not in ('x','y','z'):
            raise ValueError
        value=float(delta);steps=int(settle)
        if not re.fullmatch(r'[A-Za-z0-9_.+-]+',name) or not math.isfinite(value) or value==0 or steps<=0:
            raise ValueError
        if rest and rest[0] not in ('open','closed'):
            raise ValueError
        return Trial(name,arm,axis,value,steps,not rest or rest[0]=='open')
    except ValueError as exc:
        raise argparse.ArgumentTypeError('trial must be NAME:ARM:AXIS:DELTA_M:SETTLE[:open|closed]') from exc


def select_trials(trials,only):
    names=[trial.name for trial in trials]
    if len(names)!=len(set(names)):
        raise ValueError('duplicate trial names')
    if not only:return trials
    requested=set(only.split(','))
    unknown=requested-set(names)
    if unknown:raise ValueError('unknown trial names: '+', '.join(sorted(unknown)))
    return [trial for trial in trials if trial.name in requested]


def policy_source(trial):
    delta={'x':(trial.delta,0,0),'y':(0,trial.delta,0),'z':(0,0,trial.delta)}[trial.axis]
    lines=[f'from robot_sdk import sensors, {trial.arm}_arm, {trial.arm}_gripper',
           'observation = sensors.get_observation()']
    if trial.open_first:lines.append(f'{trial.arm}_gripper.open()')
    lines.append(f'{trial.arm}_arm.move_delta({delta[0]}, {delta[1]}, {delta[2]})')
    return '\n'.join(lines)+'\n'


def last_jsonl(path):
    if not path.is_file():return None
    last=None
    with path.open() as stream:
        for line in stream:
            if line.strip():last=json.loads(line)
    return last


def read_jsonl(path):
    if not path.is_file():return []
    with path.open() as stream:return [json.loads(line) for line in stream if line.strip()]


def summarize(trial,feedback_path,episode):
    feedback=json.loads(feedback_path.read_text())
    moves=[row for row in feedback.get('action_trace',[]) if row.get('operation')=='move_delta']
    move=moves[-1] if moves else {}
    journals=read_jsonl(episode/'native-actions.jsonl')
    failure=next((row for row in reversed(journals) if row.get('event')=='tracking_error'),None)
    receipt=next((row for row in reversed(journals) if row.get('event')=='receipt' and
                  'maximum_endpoint_joint_error_rad' in row),None)
    last=last_jsonl(episode/'native-physics.jsonl')
    qvel=last.get('qvel') if last else None
    error=move.get('error')
    if isinstance(error,dict):error=error.get('message')
    return dict(trial=trial.name,arm=trial.arm,delta_m=trial.delta,axis=trial.axis,settle_steps=trial.settle,
        open_first=trial.open_first,result='completed' if move.get('status')=='completed' else 'failed',
        move_status=move.get('status'),move_error=error,
        maximum_error_mrad=(failure['error'] if failure else receipt['maximum_endpoint_joint_error_rad'] if receipt else None)*1000
            if failure or receipt else None,
        max_error_joint=failure.get('max_error_joint') if failure else None,
        endpoint_qvel_norm=math.sqrt(sum(float(v)**2 for v in qvel)) if qvel is not None else None,
        episode=str(episode))


def markdown_table(rows):
    lines=['| arm | 步长 (m) | settle | 结果 | 最大误差 (mrad) | 最大误差关节 | 末端 qvel (rad/s) |',
           '| --- | ---: | ---: | --- | ---: | --- | ---: |']
    for row in rows:
        number=lambda value: '—' if value is None else f'{value:.3f}'
        lines.append(f"| {row['arm']} | {row['delta_m']:g} | {row['settle_steps']} | {row['result']} | "
                     f"{number(row['maximum_error_mrad'])} | {row['max_error_joint'] or '—'} | "
                     f"{number(row['endpoint_qvel_norm'])} |")
    return '\n'.join(lines)+'\n'


class ScriptedClient:
    def __init__(self,reply):
        self.reply=reply;self.inputs=[];self.response_observer=None

    def chat(self,**kwargs):
        from benchmarks.attention_harness.parcc_client import ParccResponse
        self.inputs.append(kwargs)
        usage={'total_tokens':100,'prompt_tokens':40,'completion_tokens':60}
        body={'model':'scripted/replay','choices':[{'message':{'content':self.reply}}],'usage':usage}
        if self.response_observer:self.response_observer(200,{},json.dumps(body).encode())
        return ParccResponse('scripted/replay',self.reply,None,.01,1,usage)


def run_trial(trial,root,bound):
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    from benchmarks.attention_harness.dev_experiment import create,run,verify_manifest
    root.mkdir(parents=True,exist_ok=False)
    store=DevV2Store(root/'store.sqlite')
    request_key='motion-tracking-'+hashlib.sha256(trial.name.encode()).hexdigest()[:24]
    selection=dict(request_key=request_key,mode='interactive',
        tasks=['pick_place_counter_to_sink'],seeds=[101],policies=['autonomous'],
        target_depth=1,target_valid_executions=1,max_versions=1,help_budget=0,
        coverage='budget_limited',reproducible_reset=True)
    experiment=create(store,root/'artifacts',selection)
    manifest=verify_manifest(experiment)
    budget=root/'budget.json'
    budget.write_text(json.dumps(dict(goal_id=manifest['goal_id'],ledger_path=str(store.path.resolve()),
        experiment_id=experiment['experiment_id'],manifest_sha256=experiment['manifest']['sha256'],
        limits=manifest['goal_limits'],wall_limit=manifest['wall_limit'])))
    reply=json.dumps({'source':policy_source(trial),'reason':'scripted motion tracking diagnostic'})
    old_steps=os.environ.get('ATTN_DIAG_SETTLE_STEPS');old_bound=os.environ.get('ATTN_DIAG_TRACKING_BOUND')
    try:
        os.environ['ATTN_DIAG_SETTLE_STEPS']=str(trial.settle)
        os.environ['ATTN_DIAG_TRACKING_BOUND']=str(bound)
        run(store,experiment['experiment_id'],max_cells=1,budget_file=budget,client=ScriptedClient(reply))
    finally:
        for name,value in (('ATTN_DIAG_SETTLE_STEPS',old_steps),('ATTN_DIAG_TRACKING_BOUND',old_bound)):
            if value is None:os.environ.pop(name,None)
            else:os.environ[name]=value
    feedback=next(Path(experiment['artifact_dir']).glob('cells/*/v1/public-feedback.json'))
    episode=next(feedback.parent.glob('executions/openarm365-dev-*'))
    return summarize(trial,feedback,episode)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial',action='append',type=parse_trial,help='replace defaults with NAME:ARM:AXIS:DELTA_M:SETTLE[:open|closed]')
    parser.add_argument('--only',help='comma-separated trial names')
    parser.add_argument('--out',type=Path,default=Path('motion-tracking-results'))
    parser.add_argument('--bound',type=float,default=0.004)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args(argv)
    if not math.isfinite(args.bound) or args.bound<=0:parser.error('--bound must be positive and finite')
    try:trials=select_trials(args.trial if args.trial is not None else default_trials(),args.only)
    except ValueError as exc:parser.error(str(exc))
    if args.dry_run:
        for trial in trials:print(json.dumps(asdict(trial),ensure_ascii=False))
        return 0
    args.out.mkdir(parents=True,exist_ok=True)
    results=[]
    for trial in trials:
        try:row=run_trial(trial,args.out/trial.name,args.bound)
        except Exception as exc:
            row=dict(trial=trial.name,arm=trial.arm,delta_m=trial.delta,axis=trial.axis,
                settle_steps=trial.settle,open_first=trial.open_first,result='failed',move_status=None,
                move_error=f'{type(exc).__name__}: {exc}',maximum_error_mrad=None,
                max_error_joint=None,endpoint_qvel_norm=None,episode=None)
        results.append(row)
        print(json.dumps(row,ensure_ascii=False),flush=True)
        (args.out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
        (args.out/'results.md').write_text(markdown_table(results))
    return 0


if __name__=='__main__':sys.exit(main())
