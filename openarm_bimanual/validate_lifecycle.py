"""Real HTTP lifecycle matrix. No mocked physics and no task-success claim."""
from pathlib import Path
import argparse,importlib.util,json,threading,time,traceback
import numpy as np
from .session import OwnedOpenArmService
from .kitchen import ROOT
from .backend import MobileOpenArmActionBackend as OpenArmActionBackend
from benchmarks.attention_harness.core.control import EmergencyInterrupt


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();args.out=args.out.resolve();args.out.mkdir(parents=True,exist_ok=False)
    spec=importlib.util.spec_from_file_location('accepted_client',ROOT/'openarm_phase1/validate_client.py')
    helpers=importlib.util.module_from_spec(spec);spec.loader.exec_module(helpers)
    checks=helpers.Checks();rows=[]
    for case in ['left','right','both','named_cancel','sdk_cancel','sdk_timeout','owner_cancel','owner_timeout','physics_fault']:
        out=args.out/case;out.mkdir();owned=None;row={'case':case}
        cancel=threading.Event();arm='right' if case=='right' else 'left';mode=case if case in {'left','right'} else 'both'
        try:
            owned=OwnedOpenArmService(out/'owned',python='/home/truares/miniconda3/envs/maniskill/bin/python',
                arm=mode,deadline=time.monotonic()+120,
                cancel_event=cancel if case=='owner_cancel' else None,
                fault_after_steps=500 if case=='physics_fault' else None,display=False)
            with owned:
                client=helpers.Client(owned.url,out);state=client.get('/state')
                sdk=OpenArmActionBackend(owned.url,execution_arm=mode,timeout_seconds=30.)
                row['health']=owned.health;row['initial_state']=state
                q=np.array(state[arm]['qpos']);q[0]+=.03 if arm=='left' else -.03
                if case in {'left','right','both'}:
                    observation=sdk.observe()
                    checks.check(case+' coherent RGBD',observation['base_camera_image'].shape==(480,640,3)
                        and np.isfinite(observation['base_camera_depth']).all())
                    held=np.array(state['left' if arm=='right' else 'right']['qpos'])
                    receipt=client.post('/actions',{arm:q.tolist(),'epoch':state['epoch']},expected=202)
                    row['motion']=client.wait_action(receipt['id'])
                    checks.check(case+' joint motion',row['motion']['status']=='completed')
                    row['gripper']=sdk.set_limb_gripper(arm,-1.,settle_steps=80)
                    checks.check(case+' gripper',row['gripper']['status']=='completed')
                    inactive='left' if arm=='right' else 'right'
                    checks.check(case+' inactive held',np.max(abs(np.array(client.get('/state')[inactive]['qpos'])-held))<=.01)
                    row['base_translation']=sdk.move_base_delta(.02,0,0)
                    moved=client.get('/state');base_before=np.array(state['base']['world_pose'])
                    checks.check(case+' base physically translated',np.linalg.norm(np.array(moved['base']['world_pose'])[:2,3]-base_before[:2,3])>.015)
                    checks.check(case+' root stayed fixed',np.allclose(np.array(row['initial_state']['mount_world_xyz_wxyz']),np.array(moved['mount_world_xyz_wxyz'])))
                    row['base_turn']=sdk.move_base_delta(0,0,.04)
                    checks.check(case+' base physically turned',row['base_turn']['status']=='completed' and abs(client.get('/state')['base']['qpos'][2])>.03)
                    checks.check(case+' base holds inactive arm',np.max(abs(np.array(client.get('/state')[inactive]['qpos'])-held))<=.01)
                    client.post('/base/actions',{'delta':[.31,0,0],'epoch':state['epoch']},expected=400)
                    client.post('/base/actions',{'delta':[0,0,.51],'epoch':state['epoch']},expected=400)
                    checks.check(case+' base safety preserved',True)
                    stop=client.post('/stop',{});stopped=client.get('/state')
                    checks.check(case+' stop confirmed',stop['active_action'] is None and stopped['epoch']==stop['epoch'])
                    row['measured_stop']=sdk._confirm_rest(stop)
                    checks.check(case+' both arms and chassis measured rest',row['measured_stop']['physical_rest_confirmed'])
                    row['reset']=client.post('/reset',{})
                    checks.check(case+' base reset',np.max(abs(np.array(row['reset']['base']['qpos'])))<.002)
                    client.post('/base/actions',{'delta':[.01,0,0],'epoch':state['epoch']},expected=409)
                    checks.check(case+' stale base action rejected',True)
                    checks.check(case+' reset locks identity',row['reset']['robot']==state['robot']
                        and row['reset']['epoch']>state['epoch'] and row['reset']['active_action'] is None)
                    client.post('/actions',{arm:q.tolist(),'epoch':state['epoch']},expected=409)
                    checks.check(case+' stale action rejected',True)
                    plan=client.post('/plan',{'arm':arm,'position':[30.,0.,0.],'epoch':row['reset']['epoch']})
                    checks.check(case+' unreachable is read only',not plan['reachable'] and not plan['action_dispatched']
                        and client.get('/state')['active_action'] is None)
                    before=client.get('/state')
                    row['collision_rejection']=client.post('/base/actions',{'delta':[.15,0,0],'epoch':before['epoch']},expected=422)
                    checks.check(case+' base collision rejected before dispatch',client.get('/state')['active_action'] is None)
                    if case=='both':
                        current=client.get('/state');right=np.array(current['right']['qpos']);right[0]-=.03
                        rec=client.post('/actions',{'right':right.tolist(),'epoch':current['epoch']},expected=202)
                        row['right_motion']=client.wait_action(rec['id'])
                        checks.check('both independent right participates',row['right_motion']['status']=='completed')
                        row['right_gripper']=sdk.set_limb_gripper('right',-1.)
                        checks.check('both independent grippers',row['right_gripper']['status']=='completed')
                    else:
                        other='left' if arm=='right' else 'right'
                        client.post('/actions',{other:state[other]['qpos'],'epoch':row['reset']['epoch']},expected=400)
                        checks.check(case+' inactive command locked',True)
                elif case=='named_cancel':
                    receipt=client.post('/base/actions',{'delta':[.02,0,0],'epoch':state['epoch']},expected=202)
                    row['cancel']=sdk.cancel(receipt['id'])
                    checks.check('named cancellation actual stop',row['cancel']['physical_rest_confirmed'] and all(row['cancel']['stopped_components'].values()))
                    client.post('/actions',{'left':q.tolist(),'epoch':state['epoch']},expected=409)
                    checks.check('cancel invalidates old epoch',True)
                elif case in {'sdk_cancel','sdk_timeout'}:
                    receipt=client.post('/base/actions',{'delta':[.02,0,0],'epoch':state['epoch']},expected=202)
                    if case=='sdk_cancel':sdk.set_interrupt_check(lambda:True)
                    else:sdk.set_episode_deadline(time.monotonic()+.15)
                    try:sdk._wait_action(receipt)
                    except (EmergencyInterrupt,TimeoutError) as exc:row['expected_error']=type(exc).__name__
                    checks.check(case+' stop committed',bool(sdk.cancellation_receipts)
                        and sdk.cancellation_receipts[-1]['active_action'] is None
                        and client.get('/actions/'+receipt['id'])['status']=='stopped')
                    row['stop']=sdk.cancellation_receipts
                elif case in {'owner_cancel','owner_timeout'}:
                    client.post('/base/actions',{'delta':[.02,0,0],'epoch':state['epoch']},expected=202)
                    if case=='owner_cancel':cancel.set()
                    else:owned.deadline=time.monotonic()+.15
                    limit=time.monotonic()+15
                    while owned.stop_receipt is None and time.monotonic()<limit:time.sleep(.05)
                    checks.check(case+' watchdog stop',owned.stop_receipt is not None
                        and owned.stop_receipt['reason']==('operator_cancel' if case=='owner_cancel' else 'episode_deadline')
                        and owned.stop_receipt['stop_barrier']['active_action'] is None)
                else:
                    receipt=client.post('/base/actions',{'delta':[.02,0,0],'epoch':state['epoch']},expected=202)
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
                checks.check(case+' measured arms and base rest before reap',bool(row['reap']['stop_barrier']) and row['reap']['stop_barrier']['physical_rest_confirmed'] is True)
            rows.append(row);(out/'result.json').write_text(json.dumps(row,indent=2))
            print(json.dumps({'case':case,'passed':row.get('passed'),'error':row.get('error','')[-500:]}),flush=True)
    checks.save(args.out/'checks.json');(args.out/'result.json').write_text(json.dumps(rows,indent=2))
    return int(not all(row.get('passed') for row in rows))


if __name__=='__main__':raise SystemExit(main())
