"""One real SDK pick/place attempt. Failures retain final state and native verdict."""
from pathlib import Path
import argparse,importlib.util,io,json,time,traceback
import numpy as np
from PIL import Image
from .session import OwnedOpenArmService
from .kitchen import ROOT,PARAMETERS
from benchmarks.attention_harness.robocasa_native.openarm_backend import OpenArmActionBackend


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--parameters',type=Path,default=PARAMETERS);p.add_argument('--grasp-offset',type=float,default=.025);args=p.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    spec=importlib.util.spec_from_file_location('http_helpers',ROOT/'openarm_phase1/validate_client.py')
    helpers=importlib.util.module_from_spec(spec);spec.loader.exec_module(helpers)
    owned=None;sdk=None;result={'task':'counter_to_sink','robot':{'model':'OpenArm','execution_arm':'left'},'execution_status':'failed','native_success':False}
    try:
        with OwnedOpenArmService(args.out/'owned',python='/home/truares/miniconda3/envs/maniskill/bin/python',parameters_path=args.parameters,deadline=time.monotonic()+600) as owned:
            client=helpers.Client(owned.url,args.out);client.wait_steps(480)
            sdk=OpenArmActionBackend(owned.url,timeout_seconds=90.);sdk.set_episode_deadline(owned.deadline)
            def frame(name):
                raw=client.get('/observation');(args.out/(name+'.npz')).write_bytes(raw)
                with np.load(io.BytesIO(raw)) as data:Image.fromarray(np.uint8(np.clip(data['rgb'],0,1)*255)).save(args.out/(name+'.png'))
            def checkpoint(name):
                row={'stage':name,'state':client.get('/state'),'perception':client.post('/perceive',{})};(args.out/(name+'.json')).write_text(json.dumps(row,indent=2));frame(name)
            checkpoint('initial')
            try:
                sdk.set_gripper(-1.,settle_steps=80)
                target=np.array(sdk.find_objects(['obj'])[0]['position'])
                sdk.move_arm_to_position(*(target+[0,0,.15]),tolerance=.004,max_steps=100);checkpoint('pregrasp')
                # Short Cartesian requests avoid a long joint-space sweep through
                # the object during the final approach. Each is independently
                # planned, checked and physically executed by the SDK.
                for dz in np.linspace(.15,args.grasp_offset,10)[1:]:
                    sdk.move_arm_to_position(*(target+[0,0,float(dz)]),tolerance=.004,max_steps=100)
                checkpoint('grasp_pose')
                grasp=sdk.set_gripper(1.,settle_steps=80);checkpoint('closed')
                if not grasp.get('contact_hold_committed'):
                    raise RuntimeError('Gripper reached free-close limit without bilateral target contact')
                for dz in np.linspace(args.grasp_offset,.13,9)[1:]:
                    sdk.move_arm_to_position(*(target+[0,0,float(dz)]),tolerance=.004,max_steps=100)
                checkpoint('lifted')
                lifted=np.array(sdk.find_objects(['obj'])[0]['position'])
                if lifted[2] < target[2]+.07:
                    raise RuntimeError('Physical object did not follow the lift; grasp slipped')
                for name,point in [('transfer',[3.9,-1.6,1.10]),('sink',[3.92,-1.32,1.08])]:
                    sdk.move_arm_to_position(*point,tolerance=.004,max_steps=100);checkpoint(name)
                sdk.set_gripper(-1.,settle_steps=80);checkpoint('released')
                client.wait_steps(client.get('/state')['total_steps']+480)
                result['execution_status']='completed'
            except BaseException:
                result['error']=traceback.format_exc();(args.out/'failure.txt').write_text(result['error'])
            finally:
                try:
                    result['native_evaluation']=client.get('/task/success');result['native_success']=result['native_evaluation']['native_success'];checkpoint('final')
                    result['final_collisions']=client.get('/collisions')
                except Exception:result['final_query_error']=traceback.format_exc()
    except BaseException:result['startup_error']=traceback.format_exc()
    finally:
        if owned:result['reap']=owned.stop_receipt
        if sdk:result.update(action_receipts=sdk.action_receipts,planned_motion_receipts=sdk.planned_motion_receipts)
        (args.out/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    print(json.dumps({k:v for k,v in result.items() if k in ['execution_status','native_success','reap','error','startup_error']}),flush=True)
    return int(not result['native_success'])


if __name__=='__main__':raise SystemExit(main())
