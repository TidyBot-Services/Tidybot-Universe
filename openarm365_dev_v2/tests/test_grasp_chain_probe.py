import json,math
from benchmarks.attention_harness.diagnostics.grasp_chain_probe import base_steps,policy_source,summarize,markdown
from openarm365_dev.policy_validation import validate_generated_policy

def test_steps_compose_and_policy_is_sdk_valid():
    for shift in ((0,.3,0),(.3,.2,.4),(-.4,.5,-.6)):
        steps=base_steps(shift);x=y=yaw=0.
        for dx,dy,dyaw in steps:
            assert math.hypot(dx,dy)<=.2+1e-12 and abs(dyaw)<=.3+1e-12
            x+=math.cos(yaw)*dx-math.sin(yaw)*dy
            y+=math.sin(yaw)*dx+math.cos(yaw)*dy
            yaw+=dyaw
        assert max(abs(x-shift[0]),abs(y-shift[1]),abs(yaw-shift[2]))<1e-12
    for tilt in (False,True):
        source=policy_source((0,.3,0),tilt)
        validate_generated_policy(source)
        assert "target[2] + 0.10" in source
        assert "obj_after[2] - obj_before[2] >= 0.05" in source

def test_public_summary_from_real_feedback_shape(tmp_path):
    version=tmp_path/'artifacts'/'e'/'cells'/'c'/'v1';version.mkdir(parents=True)
    episode=version/'executions'/'openarm365-dev-test';episode.mkdir(parents=True)
    def obs(z):return {'robot0_left_eef_pos':[0,0,z],'robot0_left_gripper_qpos':[.02,.02]}
    events=[]
    def event(op,**extra):events.append(dict(operation=op,status='completed',**extra))
    event('move_delta',source='base');event('get_observation',observation=obs(1.))
    event('open');event('get_observation',observation=obs(1.))
    event('find_objects',objects=[{'name':'obj','position':[0,0,.9]}])
    event('move_delta',source='left_arm');event('get_observation',observation=obs(1.1))
    event('move_delta',source='left_arm');event('get_observation',observation=obs(.9))
    event('close');event('get_observation',observation=obs(.9))
    event('find_objects',objects=[{'name':'obj','position':[0,0,.9]}])
    event('move_delta',source='left_arm');event('get_observation',observation=obs(1.))
    event('find_objects',objects=[{'name':'obj','position':[0,0,.96]}])
    (version/'public-feedback.json').write_text(json.dumps({'action_trace':events}))
    base={'robot':obs(1.),'perception':{'values':{'obj_pos':[0,0,.9]}}}
    (episode/'initial_public_observation.json').write_text(json.dumps(base))
    (episode/'final_public_observation.json').write_text(json.dumps({'robot':obs(1.),'perception':{'values':{'obj_pos':[0,0,.96]}}}))
    row=summarize(tmp_path,102,(0,.3,0),'movable_objects',False,1.)
    assert [x['stage'] for x in row['stages']]==['base','open','pregrasp','descend','close','lift','held']
    assert row['object_lifted_5cm'] is True
    assert row['stages'][-1]['object_position']==[0,0,.96]
    assert '上升至少 5 cm' in markdown(row)
