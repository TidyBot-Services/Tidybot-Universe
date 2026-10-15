"""Alternative real physical transfer diagnostic; SDK motions only."""
from pathlib import Path
import argparse,importlib.util,io,json,time,traceback
import numpy as np
from PIL import Image
from .session import OwnedOpenArmService
from .kitchen import ROOT
from benchmarks.attention_harness.robocasa_native.openarm_backend import OpenArmActionBackend


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);parser.add_argument('--parameters',type=Path)
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=False)
    spec=importlib.util.spec_from_file_location('accepted_client',ROOT/'openarm_phase1/validate_client.py')
    h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
    owned=None;sdk=None;result={'strategy':'single-arm finger push via SDK','execution_status':'failed','native_success':False}
    try:
        with OwnedOpenArmService(args.out/'owned',python='/home/truares/miniconda3/envs/maniskill/bin/python',parameters_path=args.parameters,deadline=time.monotonic()+300) as owned:
            client=h.Client(owned.url,args.out);client.wait_steps(480)
            sdk=OpenArmActionBackend(owned.url,timeout_seconds=90);sdk.set_episode_deadline(owned.deadline)
            def checkpoint(name):
                row={'state':client.get('/state'),'objects':sdk.find_objects()}
                (args.out/(name+'.json')).write_text(json.dumps(row,indent=2))
                raw=client.get('/observation');(args.out/(name+'.npz')).write_bytes(raw)
                with np.load(io.BytesIO(raw)) as obs:Image.fromarray(np.uint8(np.clip(obs['rgb'],0,1)*255)).save(args.out/(name+'.png'))
            checkpoint('initial');target=np.array(sdk.find_objects(['obj'])[0]['position'])
            try:
                sdk.set_gripper(1.,settle_steps=80)
                behind=target+[0,-.038,.02]
                sdk.move_arm_to_position(*(target+[0,0,.15]),tolerance=.004,max_steps=100)
                sdk.move_arm_to_position(*(behind+[0,0,.07]),tolerance=.004,max_steps=100)
                for dz in np.linspace(.07,0,5)[1:]:sdk.move_arm_to_position(*(behind+[0,0,float(dz)]),tolerance=.004,max_steps=100)
                checkpoint('behind')
                end=np.array([3.92,-1.32,behind[2]])
                for i,point in enumerate(np.linspace(behind,end,24)[1:]):
                    sdk.move_arm_to_position(*point,tolerance=.004,max_steps=100)
                    checkpoint('push'+str(i))
                sdk.move_arm_delta(0,0,.13,[0,0,0]);sdk.set_gripper(-1.,settle_steps=80)
                client.wait_steps(client.get('/state')['total_steps']+480)
                result['execution_status']='completed'
            except BaseException:result['error']=traceback.format_exc()
            finally:
                result['native_evaluation']=client.get('/task/success');result['native_success']=result['native_evaluation']['native_success'];checkpoint('final')
    except BaseException:result['startup_error']=traceback.format_exc()
    finally:
        if owned:result['reap']=owned.stop_receipt
        if sdk:result.update(action_receipts=sdk.action_receipts,planned_motion_receipts=sdk.planned_motion_receipts)
        (args.out/'result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:result[k] for k in ['execution_status','native_success']}),flush=True)
    return int(not result['native_success'])


if __name__=='__main__':raise SystemExit(main())
