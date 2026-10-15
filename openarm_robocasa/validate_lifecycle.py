"""Real HTTP lifecycle matrix. No mocked physics and no task-success claim."""
from pathlib import Path
import argparse,importlib.util,json,threading,time,traceback
import numpy as np
from .session import OwnedOpenArmService
from .kitchen import ROOT
from benchmarks.attention_harness.robocasa_native.openarm_backend import OpenArmActionBackend
from benchmarks.attention_harness.core.control import EmergencyInterrupt


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=False)
    spec=importlib.util.spec_from_file_location('accepted_client',ROOT/'openarm_phase1/validate_client.py')
    helpers=importlib.util.module_from_spec(spec);spec.loader.exec_module(helpers)
    checks=helpers.Checks();rows=[]
    for case in ['left','right','sdk_cancel','sdk_timeout','owner_cancel','owner_timeout','physics_fault']:
        out=args.out/case;out.mkdir();owned=None;row={'case':case}
        cancel=threading.Event();arm='right' if case=='right' else 'left'
        try:
            owned=OwnedOpenArmService(out/'owned',python='/home/truares/miniconda3/envs/maniskill/bin/python',
                arm=arm,deadline=time.monotonic()+120,
                cancel_event=cancel if case=='owner_cancel' else None,
                fault_after_steps=500 if case=='physics_fault' else None)
            with owned:
                client=helpers.Client(owned.url,out);state=client.get('/state')
                sdk=OpenArmActionBackend(owned.url,execution_arm=arm,timeout_seconds=30.)
                row['health']=owned.health;row['initial_state']=state
                q=np.array(state[arm]['qpos']);q[0]+=.03 if arm=='left' else -.03
                if case in {'left','right'}:
                    observation=sdk.observe()
                    checks.check(case+' coherent RGBD',observation['base_camera_image'].shape==(480,640,3)
                        and np.isfinite(observation['base_camera_depth']).all())
                    held=np.array(state['left' if arm=='right' else 'right']['qpos'])
                    receipt=client.post('/actions',{arm:q.tolist(),'epoch':state['epoch']},expected=202)
                    row['motion']=client.wait_action(receipt['id'])
                    checks.check(case+' joint motion',row['motion']['status']=='completed')
                    row['gripper']=sdk.set_gripper(-1.,settle_steps=80)
                    checks.check(case+' gripper',row['gripper']['status']=='completed')
                    inactive='left' if arm=='right' else 'right'
                    checks.check(case+' inactive held',np.max(abs(np.array(client.get('/state')[inactive]['qpos'])-held))<=.01)
                    row['reset']=client.post('/reset',{})
                    checks.check(case+' reset locks identity',row['reset']['robot']==state['robot']
                        and row['reset']['epoch']>state['epoch'] and row['reset']['active_action'] is None)
                    client.post('/actions',{arm:q.tolist(),'epoch':state['epoch']},expected=409)
                    checks.check(case+' stale action rejected',True)
                    plan=sdk.plan_arm_to_position(30.,0.,0.)
                    checks.check(case+' unreachable is read only',plan['reachable'] is False
                        and plan['action_dispatched'] is False and client.get('/state')['active_action'] is None)
                elif case in {'sdk_cancel','sdk_timeout'}:
                    receipt=client.post('/actions',{arm:q.tolist(),'epoch':state['epoch']},expected=202)
                    if case=='sdk_cancel':sdk.set_interrupt_check(lambda:True)
                    else:sdk.set_episode_deadline(time.monotonic()+.15)
                    try:sdk._wait_action(receipt)
                    except (EmergencyInterrupt,TimeoutError) as exc:row['expected_error']=type(exc).__name__
                    checks.check(case+' stop committed',bool(sdk.cancellation_receipts)
                        and sdk.cancellation_receipts[-1]['active_action'] is None
                        and client.get('/actions/'+receipt['id'])['status']=='stopped')
                    row['stop']=sdk.cancellation_receipts
                elif case in {'owner_cancel','owner_timeout'}:
                    client.post('/actions',{arm:q.tolist(),'epoch':state['epoch']},expected=202)
                    if case=='owner_cancel':cancel.set()
                    else:owned.deadline=time.monotonic()+.15
                    limit=time.monotonic()+15
                    while owned.stop_receipt is None and time.monotonic()<limit:time.sleep(.05)
                    checks.check(case+' watchdog stop',owned.stop_receipt is not None
                        and owned.stop_receipt['reason']==('operator_cancel' if case=='owner_cancel' else 'episode_deadline')
                        and owned.stop_receipt['stop_barrier']['active_action'] is None)
                else:
                    receipt=client.post('/actions',{arm:q.tolist(),'epoch':state['epoch']},expected=202)
                    limit=time.monotonic()+15
                    while owned.child.poll() is None and time.monotonic()<limit:time.sleep(.05)
                    checks.check('physics fault exits',owned.child.poll()==1)
                    events=[json.loads(line) for line in (out/'owned/service/events.jsonl').read_text().splitlines()]
                    checks.check('physics fault cancels real action',any(item['event']=='fatal' for item in events)
                        and any(item['event']=='action_failed' and item['action']['id']==receipt['id'] for item in events))
            row['passed']=True
        except BaseException:row.update(passed=False,error=traceback.format_exc())
        finally:
            if owned:
                owned.close();row['reap']=owned.stop_receipt
                checks.check(case+' process group reaped',bool(row['reap']) and not row['reap']['process_group_remaining'])
            rows.append(row);(out/'result.json').write_text(json.dumps(row,indent=2))
            print(json.dumps({'case':case,'passed':row.get('passed'),'error':row.get('error','')[-500:]}),flush=True)
    checks.save(args.out/'checks.json');(args.out/'result.json').write_text(json.dumps(rows,indent=2))
    return int(not all(row.get('passed') for row in rows))


if __name__=='__main__':raise SystemExit(main())
