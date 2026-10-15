"""Contact solver diagnosis; reset-only robot pose, not a task-success run."""
from pathlib import Path
import argparse,importlib.util,json,time,traceback,hashlib
import numpy as np
from .session import OwnedOpenArmService
from .kitchen import ROOT,PARAMETERS
from benchmarks.attention_harness.robocasa_native.openarm_backend import OpenArmActionBackend


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--position-iterations',type=int,default=32);p.add_argument('--velocity-iterations',type=int,default=8);p.add_argument('--damping',type=float,default=50);p.add_argument('--follower-damping',type=float,default=0);p.add_argument('--tendon-damping',type=float,default=0);p.add_argument('--inset',type=float,default=.0004);args=p.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    cfg=json.loads(PARAMETERS.read_bytes()); recorded=json.loads((ROOT/'outputs/openarm-robocasa/task-probe-3/grasp_pose.json').read_bytes())['state']
    cfg['initial']['left']=recorded['left']['qpos'];cfg['initial']['left_gripper']=.04399999976158142
    cfg.update(solver_position_iterations=args.position_iterations,solver_velocity_iterations=args.velocity_iterations,
        contact_hold_finger_damping=args.damping,contact_hold_follower_damping=args.follower_damping,
        mimic_tendon_damping=args.tendon_damping,contact_hold_inset_m=args.inset,
        scope='Contact stability diagnosis; robot pose set only at reset; not task capability acceptance')
    params=ROOT/'openarm_robocasa/configs'/(args.out.name+'.json');params.write_text(json.dumps(cfg,indent=2)+'\n');params.with_suffix('.sha256').write_text(hashlib.sha256(params.read_bytes()).hexdigest()+'\n')
    spec=importlib.util.spec_from_file_location('http_helpers',ROOT/'openarm_phase1/validate_client.py');h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
    result={'type':'contact solver diagnosis','task_acceptance':False};owned=None;sdk=None
    try:
        with OwnedOpenArmService(args.out/'owned',python='/home/truares/miniconda3/envs/maniskill/bin/python',parameters_path=params,deadline=time.monotonic()+120) as owned:
            client=h.Client(owned.url,args.out);client.wait_steps(480);sdk=OpenArmActionBackend(owned.url,timeout_seconds=60)
            result['initial']=client.get('/state');result['task_info']=client.get('/task/info')
            try:result['close']=sdk.set_gripper(1.,settle_steps=80)
            except Exception:result['error']=traceback.format_exc()
            result['final']=client.get('/state');result['native']=client.get('/task/success');result['model']=client.get('/model')
    except Exception:result['startup_error']=traceback.format_exc()
    finally:
        if sdk:result['actions']=sdk.action_receipts
        if owned:result['reap']=owned.stop_receipt
        (args.out/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    print(json.dumps({'closed':result.get('close',{}).get('status'),'error':result.get('error',result.get('startup_error',''))[-800:], 'finger':result.get('final',{}).get('left_gripper')}),flush=True)


if __name__=='__main__':main()
