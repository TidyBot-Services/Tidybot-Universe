"""Read feedback + gripper-only acceptance in the ORIGINAL dual-arm native scene.

No arm/base targets, planner entry, object pose writes, or joint-state writes.
This script cannot perform or certify moving handoffs. Every result is engineering
only, formal_eligible=false. Native task reset remains owned by OpenArmKitchen.
"""
import argparse
import importlib.metadata
import hashlib
import shutil
import json
from pathlib import Path
import sys
from dataclasses import asdict
import numpy as np
from .grasp import GraspCoordinator, SCHEMA
from .grasp_feedback import loaded_limits, normal_force, sample_feedback, apply_controller


def calibration(out):
    import sapien
    scene=sapien.Scene([sapien.physx.PhysxCpuSystem()]);scene.set_timestep(1/240)
    builder=scene.create_actor_builder();builder.add_box_collision(half_size=[1,1,.05]);floor=builder.build_static('force_calibration_floor')
    builder=scene.create_actor_builder();builder.add_box_collision(half_size=[.05]*3,density=1000);box=builder.build('force_calibration_mass');box.pose=sapien.Pose([0,0,.2])
    component=box.find_component_by_type(sapien.physx.PhysxRigidDynamicComponent)
    rows=[]
    try:
        for step in range(480):
            scene.step();points=[]
            for contact in scene.get_contacts():
                if {b.entity.name for b in contact.bodies}=={box.name,floor.name}:
                    points += [dict(normal=p.normal.tolist(),impulse_Ns=p.impulse.tolist(),separation_m=float(p.separation)) for p in contact.points]
            rows.append(dict(step=step+1,force_estimate_n=normal_force(points,1/240),points=points))
        expected=9.81*component.mass; measured=float(np.mean([r['force_estimate_n'] for r in rows[-240:]]))
        report=dict(sapien_version=importlib.metadata.version('sapien'),python=sys.executable,dt_s=1/240,
            formula='sum(abs(dot(impulse_Ns, unit_normal))) / dt_s per finger',unit='N',
            raw_impulse_unit='N*s',filter='EMA alpha=.25 for control; raw positive bilateral contact always required',
            calibration='isolated 1kg-class native box in static gravity, 240-step mean after 240 settling steps',
            mass_kg=component.mass,expected_n=expected,observed_n=measured,relative_error=abs(measured-expected)/expected,
            passed=abs(measured-expected)/expected<.01,
            measured_drive_force_available=False,
            qf='applied generalized force: passive compensation in accepted rig; NOT measured drive force',
            incoming_joint_forces='constraint reactions [links,6]; not isolated motor output, not used as drive feedback',
            force_limit='configured loaded joint cap, not observed force',
            api_members={name:[n for n in dir(cls) if 'force' in n or 'drive' in n or n=='qf'] for name,cls in [('joint',sapien.physx.PhysxArticulationJoint),('articulation',sapien.physx.PhysxArticulation)]},
            references=['https://sapien.ucsd.edu/docs/2.2/tutorial/basic/contact.html','https://sapien-sim.github.io/docs/api/sapien.pysapien.physx.html'],formal_eligible=False)
        (out/'force-calibration-steps.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        (out/'feedback-audit.json').write_text(json.dumps(report,indent=2)+'\n')
        if not report['passed']:raise RuntimeError('Contact-force calibration failed')
        return report
    finally:scene.clear()


def static(out):
    from .kitchen import OpenArmKitchen
    from .contact_policy import unexpected_contacts
    # Scene/task/object/model are the frozen originals. No alternate objects.
    kitchen=OpenArmKitchen('counter_to_sink',101,'both');rig=kitchen.rig
    limits,provenance=loaded_limits(rig)
    (out/'loaded-model-limits.json').write_text(json.dumps(provenance,indent=2)+'\n')
    (out/'scene-identity.json').write_text(json.dumps(dict(task='counter_to_sink',seed=101,objects=kitchen.object_state(),variation=kitchen.variation,parameters_sha256=kitchen.parameters_sha256,
        model_sha256=hashlib.sha256(Path(rig.urdf).read_bytes()).hexdigest(),mimics=[dict(joint=m.joint,mimic=m.mimic,multiplier=m.multiplier,offset=m.offset) for m in rig.mimics],extra_disabled_pairs=rig.extra_pairs),indent=2)+'\n')
    step=0;stops=[];events=[];results=[]
    def stop(reason):
        stops.append(reason)
        q=np.clip(np.array(rig.robot.qpos),rig.qlimits[:,0],rig.qlimits[:,1])
        for m in rig.mimics:q[rig.idx[m.joint]]=m.multiplier*q[rig.idx[m.mimic]]+m.offset
        rig.goal=q.copy();rig.command=q.copy();rig.apply_drive()
    c=GraspCoordinator(limits,epoch=0,target_id=int(kitchen.target_entity.per_scene_id),stop_callback=stop,emit=events.append)
    trajectory=(out/'gripper-steps.jsonl').open('w');wire=(out/'requests-responses.jsonl').open('w')
    initial_arm_goal=rig.goal[rig.arm].copy();initial_base_goal=rig.goal[rig.base].copy()
    try:
        for _ in range(480):kitchen.step();step+=1
        for side,goal in [('left',.006),('right',.006),('left',limits['left'].upper_m),('right',limits['right'].upper_m)]:
            reference=np.array(rig.robot.qpos)
            body=dict(schema=SCHEMA,epoch=c.epoch,arm=side,request_id=f'static-{side}-{goal}',mode='position',
                finger_displacement_m=goal,drive_force_limit_n=20.)
            receipt=c.command(body,step=step);aid=receipt['id']
            wire.write(json.dumps(dict(transport='in-process component; not authenticated HTTP',request_raw=json.dumps(body),response_raw=json.dumps(receipt)))+'\n')
            for _ in range(720+240):
                apply_controller(rig,c);kitchen.step();step+=1
                samples=sample_feedback(rig,target=kitchen.target_entity,epoch=c.epoch,step=step,dt=1/240)
                c.observe(samples)
                contacts=unexpected_contacts(rig.scene,sides=['left','right'],target_id=c.target_id,target_contact_sides=['left','right'])
                q=np.array(rig.robot.qpos);v=np.array(rig.robot.qvel)
                idle_s='right' if side=='left' else 'left';ix=[rig.idx[f'openarm_{idle_s}_finger_joint{i}'] for i in (1,2)]
                if np.max(abs(q[rig.arm]-reference[rig.arm]))>.01 or np.max(abs(q[ix]-reference[ix]))>.0005 or contacts:
                    c.abort('static_idle_or_collision_guard',step=step)
                if not np.array_equal(rig.goal[rig.arm],initial_arm_goal) or not np.array_equal(rig.goal[rig.base],initial_base_goal):raise RuntimeError('Static validation changed arm or base targets')
                trajectory.write(json.dumps(dict(step=step,epoch=c.epoch,action_id=aid,qpos=q.tolist(),qvel=v.tolist(),
                    goal=rig.goal.tolist(),drive_target=rig.command.tolist(),feedback={s:asdict(x) for s,x in samples.items()},
                    controller=c.snapshot(),object_world=kitchen.target_entity.pose.to_transformation_matrix().tolist(),
                    tcp_world={s:rig.robot.find_link_by_name(f'openarm_{s}_hand_tcp').entity_pose.to_transformation_matrix().tolist() for s in ['left','right']},
                    qf_applied_passive_compensation=rig.robot.qf.tolist(),measured_drive_force_n=None,unexpected_contacts=contacts,formal_eligible=False))+'\n')
                if c.suspended:break
            result=dict(side=side,goal_m=goal,action=c.actions[aid],hold_steps=240,
                position_error_m=float(max(abs(q[[rig.idx[f'openarm_{side}_finger_joint{i}'] for i in (1,2)]]-goal))),
                max_final_finger_speed_m_s=float(max(abs(v[rig.finger]))),arm_error_rad=float(max(abs(q[rig.arm]-rig.goal[rig.arm]))),
                mimic_error_m=max(abs(samples[s].q_m[0]-samples[s].q_m[1]) for s in ['left','right']))
            result['passed']=bool(result['action']['status']=='completed' and not c.suspended and result['position_error_m']<=.001 and result['max_final_finger_speed_m_s']<=.005 and result['arm_error_rad']<=.03 and result['mimic_error_m']<=.001)
            results.append(result);wire.write(json.dumps(dict(action_id=aid,response_raw=json.dumps(c.actions[aid])))+'\n')
            if not result['passed']:break
        report=dict(schema='openarm-grasp-static-acceptance.v2',scope='dual-arm native kitchen gripper-only, no arm/base requests',
            task='counter_to_sink',seed=101,results=results,stops=stops,
            B_passed=len(results)==4 and all(r['passed'] for r in results),
            C='UNVERIFIED: authenticated V2 dual-arm executor absent; no moving handoff attempted',
            native_evaluation=kitchen.native_evaluate(),formal_eligible=False,goal_complete=False)
        (out/'static-result.json').write_text(json.dumps(report,indent=2)+'\n')
        (out/'component-events.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in events))
        print(json.dumps({k:report[k] for k in ('B_passed','C','stops')}))
    finally:
        stop('validation_cleanup')
        for _ in range(240):kitchen.step()
        v=np.array(rig.robot.qvel);rest=bool(max(abs(v[rig.arm]))<=.05 and max(abs(v[rig.finger]))<=.005 and max(abs(v[rig.base]))<=.005)
        (out/'cleanup.json').write_text(json.dumps(dict(physical_rest_confirmed=rest,qvel=v.tolist(),formal_eligible=False))+'\n')
        (out/'partial-results.json').write_text(json.dumps(results,indent=2)+'\n')
        (out/'component-events.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in events))
        trajectory.close();wire.close();kitchen.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--static',action='store_true');p.add_argument('--task-source',type=Path)
    args=p.parse_args();args.out.mkdir(parents=True,exist_ok=False)
    if args.task_source:sys.path.insert(0,str(args.task_source))
    sources={}
    for file in Path(__file__).parent.glob('*.py'):
        dest=args.out/'source_snapshot'/file.name;dest.parent.mkdir(exist_ok=True);shutil.copy2(file,dest)
        sources[file.name]=hashlib.sha256(file.read_bytes()).hexdigest()
    (args.out/'source-identity.json').write_text(json.dumps(sources,indent=2)+'\n')
    calibration(args.out)
    if args.static:static(args.out)

if __name__=='__main__':main()
