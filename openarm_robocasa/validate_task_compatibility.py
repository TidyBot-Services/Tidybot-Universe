"""Load a second original task and exercise real SDK control; no task-pass fixture."""
from pathlib import Path
import argparse, importlib.util, json, time, traceback
import numpy as np
from .session import OwnedOpenArmService
from .kitchen import ROOT, PARAMETERS
from benchmarks.attention_harness.robocasa_native.openarm_backend import OpenArmActionBackend


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--task',choices=['counter_to_cab'],default='counter_to_cab')
    p.add_argument('--parameters',type=Path,default=PARAMETERS)
    args=p.parse_args(); args.out.mkdir(parents=True,exist_ok=False)
    spec=importlib.util.spec_from_file_location('accepted_client',ROOT/'openarm_phase1/validate_client.py')
    helpers=importlib.util.module_from_spec(spec);spec.loader.exec_module(helpers)
    result={'task':args.task,'scope':'scene/SDK/native evaluator compatibility; task capability separate','passed':False}
    owned=None; sdk=None
    try:
        with OwnedOpenArmService(args.out/'owned',python='/home/truares/miniconda3/envs/maniskill/bin/python',
                task=args.task,parameters_path=args.parameters,deadline=time.monotonic()+180) as owned:
            client=helpers.Client(owned.url,args.out);client.wait_steps(480)
            sdk=OpenArmActionBackend(owned.url,timeout_seconds=90);sdk.set_episode_deadline(owned.deadline)
            result['task_info']=client.get('/task/info');result['health']=owned.health
            raw=client.get('/observation');(args.out/'initial_observation.npz').write_bytes(raw)
            obs=sdk.observe();inactive=obs['right_joint_pos'].copy()
            sdk.set_gripper(-1.,settle_steps=80)
            here=obs['robot0_eef_pos'];target=here+[0,0,-.015]
            plan=sdk.plan_arm_to_position(*target.tolist());result['plan']=plan
            if not plan['reachable']:raise RuntimeError('Compatibility SDK target rejected before motion')
            sdk.move_arm_to_position(*target.tolist(),tolerance=.004,max_steps=100)
            final=sdk.observe();result['inactive_max_error_rad']=float(np.max(abs(final['right_joint_pos']-inactive)))
            if result['inactive_max_error_rad']>.01:raise RuntimeError('Inactive arm exceeded original hold bound')
            (args.out/'final_observation.npz').write_bytes(client.get('/observation'))
            result['native_evaluation']=client.get('/task/success')
            result['stop']=sdk.stop();result['passed']=result['stop']['active_action'] is None
    except BaseException:result['error']=traceback.format_exc()
    finally:
        if owned:result['reap']=owned.stop_receipt
        if sdk:result['actions']=sdk.action_receipts;result['planned_motion']=sdk.planned_motion_receipts
        result['passed']=result['passed'] and bool(result.get('reap')) and not result['reap']['process_group_remaining']
        (args.out/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:result.get(k) for k in ['task','passed','native_evaluation','error']}),flush=True)
    return int(not result['passed'])


if __name__=='__main__':raise SystemExit(main())
