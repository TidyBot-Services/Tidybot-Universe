"""Gripper-only native lifecycle negatives; no authenticated motion-chain claim."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
from dataclasses import asdict
import numpy as np
from .grasp import GraspCoordinator,SCHEMA
from .grasp_feedback import loaded_limits,sample_feedback,apply_controller
from .validate_grasp import calibration


def run(out):
    from .kitchen import OpenArmKitchen
    from .contact_policy import unexpected_contacts
    k=OpenArmKitchen('counter_to_sink',101,'both');r=k.rig;limits,provenance=loaded_limits(r)
    (out/'loaded-limits.json').write_text(json.dumps(provenance,indent=2)+'\n')
    (out/'scene-identity.json').write_text(json.dumps(dict(task='counter_to_sink',seed=101,objects=k.object_state(),variation=k.variation,formal_eligible=False),indent=2)+'\n')
    original_arm_goal=r.goal[r.arm].copy();original_base_goal=r.goal[r.base].copy()
    step=0;report=[];wire=(out/'requests-responses.jsonl').open('w');trace=(out/'steps.jsonl').open('w')
    events=[]
    try:
        for _ in range(480):k.step();step+=1
        for name,side in [('empty','left'),('empty','right'),('cancel','left'),('stop','right'),('feedback_invalid','left'),('runtime_identity_failed','right')]:
            stop_reasons=[]
            def stop(reason):
                stop_reasons.append(reason)
                q=np.clip(np.array(r.robot.qpos),r.qlimits[:,0],r.qlimits[:,1])
                for m in r.mimics:q[r.idx[m.joint]]=m.multiplier*q[r.idx[m.mimic]]+m.offset
                # Commit original bounded drive hold; never write simulation state.
                r.goal=q.copy();r.command=q.copy();r.apply_drive()
            c=GraspCoordinator(limits,epoch=len(report),target_id=int(k.target_entity.per_scene_id),stop_callback=stop,emit=events.append)
            body=dict(schema=SCHEMA,epoch=c.epoch,arm=side,request_id=f'{name}-{side}',mode='acquire' if name=='empty' else 'position',
                finger_displacement_m=.006 if name=='empty' else limits[side].upper_m,drive_force_limit_n=20.)
            if name=='empty':body.update(target_id=c.target_id,desired_contact_force_n=2.)
            a=c.command(body,step=step);aid=a['id']
            wire.write(json.dumps(dict(case=name,side=side,transport='in-process gripper component',request_raw=json.dumps(body),response_raw=json.dumps(a)))+'\n')
            count=720 if name=='empty' else 30
            for i in range(count):
                apply_controller(r,c);k.step();step+=1
                x=sample_feedback(r,target=k.target_entity,epoch=c.epoch,step=step,dt=1/240)
                raw={s:asdict(f) for s,f in x.items()};injected=None
                if i==count-1 and name in {'feedback_invalid','runtime_identity_failed'}:
                    if name=='feedback_invalid':x[side].valid=False
                    else:x[side].identity_valid=False
                    injected=name
                c.observe(x)
                contacts=unexpected_contacts(r.scene,sides=['left','right'],target_id=c.target_id,target_contact_sides=['left','right'])
                if contacts:c.abort('collision_stop',step=step)
                # Stop may capture arm/base measured positions; no desired motion.
                # During active stepping, all commands must remain the frozen initial targets.
                if not c.suspended and (not np.array_equal(r.goal[r.arm],original_arm_goal) or not np.array_equal(r.goal[r.base],original_base_goal)):
                    raise RuntimeError('Negative probe changed arm/base motion targets')
                trace.write(json.dumps(dict(case=name,side=side,step=step,actual_feedback=raw,injected_boundary_failure=injected,
                    qpos=r.robot.qpos.tolist(),qvel=r.robot.qvel.tolist(),goal=r.goal.tolist(),command=r.command.tolist(),
                    controller=c.snapshot(),unexpected=contacts,formal_eligible=False))+'\n')
                if c.suspended:break
            if name in {'cancel','stop'}:c.abort(name,step=step,status='cancelled' if name=='cancel' else 'stopped')
            for _ in range(240):k.step();step+=1
            v=np.array(r.robot.qvel)
            rest=bool(max(abs(v[r.arm]))<=.05 and max(abs(v[r.finger]))<=.005 and max(abs(v[r.base]))<=.005)
            expect='grasp_not_confirmed' if name=='empty' else name
            result=dict(case=name,side=side,action=c.actions[aid],stop_reasons=stop_reasons,
                no_grasp=not any(f.holding or f.grasped for f in c.fingers.values()),ownership=c.ownership,
                physical_rest_confirmed=rest,rest_qvel=v.tolist(),epoch=c.epoch,
                injection_scope='host feedback boundary only; not an actual V2 identity tamper' if name=='runtime_identity_failed' else 'feedback-valid bit injection' if name=='feedback_invalid' else 'native gripper component request',
                passed=c.suspended and expect in stop_reasons and rest and c.ownership is None)
            report.append(result);wire.write(json.dumps(dict(case=name,side=side,response_raw=json.dumps(c.actions[aid])))+'\n')
            # Stop held the measured pose. Preserve it as the next idle reference;
            # no arm/base displacement is dispatched during these gripper tests.
            original_arm_goal=r.goal[r.arm].copy();original_base_goal=r.goal[r.base].copy()
        (out/'result.json').write_text(json.dumps(dict(cases=report,passed=all(x['passed'] for x in report),formal_eligible=False,
            real_moving_handoff='UNVERIFIED',native_evaluation=k.native_evaluate()),indent=2)+'\n')
        print(json.dumps(dict(passed=all(x['passed'] for x in report),cases=len(report))))
    finally:
        (out/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
        (out/'partial-result.json').write_text(json.dumps(report,indent=2)+'\n')
        trace.close();wire.close();k.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--task-source',type=Path,required=True);args=p.parse_args()
    args.out.mkdir(parents=True,exist_ok=False);sys.path.insert(0,str(args.task_source));sources={}
    for file in Path(__file__).parent.glob('*.py'):
        dest=args.out/'source_snapshot'/file.name;dest.parent.mkdir(exist_ok=True);shutil.copy2(file,dest);sources[file.name]=hashlib.sha256(file.read_bytes()).hexdigest()
    (args.out/'source-identity.json').write_text(json.dumps(sources,indent=2)+'\n')
    calibration(args.out);run(args.out)

if __name__=='__main__':main()
