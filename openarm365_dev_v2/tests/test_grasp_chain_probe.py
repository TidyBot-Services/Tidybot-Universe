import hashlib,json,math,sys,types
import pytest
from benchmarks.attention_harness.diagnostics.grasp_chain_probe import base_steps,policy_source,summarize,markdown,rotation_steps,approach_steps,oriented_policy_source,sequenced_policy_source,main
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


def test_rotation_split_and_oriented_policy(capsys):
    import ast
    from scipy.spatial.transform import Rotation
    target=Rotation.from_euler('xyz',[.8,-.6,.2]).as_quat()
    steps=rotation_steps([0,0,0,1],target)
    assert len(steps)>1 and all(max(abs(v) for v in step)<=.5 for step in steps)
    source=oriented_policy_source((0,.3,0),target,
        {'position_world_m':[.5,-2.3,.94],'z_range_world_m':[.92,.96]})
    validate_generated_policy(source)
    validate_generated_policy(oriented_policy_source((0,.3,0),target))
    assert '使用仿真真值,仅诊断' in source
    assert 'oracle_z[0]+oracle_z[1]' in source
    assert 'obs_orient' in source and 'robot0_left_eef_quat' in source
    tree=ast.parse(source)
    functions=ast.Module(body=[node for node in tree.body if isinstance(node,ast.FunctionDef)],type_ignores=[])
    namespace={};exec(compile(functions,'<policy-functions>','exec'),namespace)
    computed=namespace['remaining_rotvec']([0,0,0,1],list(target))
    assert computed==pytest.approx(Rotation.from_quat(target).as_rotvec(),abs=1e-7)
    main(['--seed','102','--base-shift','0,.3,0','--grasp-quat',','.join(map(str,target)),
          '--oracle-from-reset','--dry-run'])
    assert '使用仿真真值,仅诊断' in capsys.readouterr().out

def test_sequence_policy_and_step_limits(capsys,tmp_path):
    from scipy.spatial.transform import Rotation
    target=(.3007057995042732,.9537169507482269,0,0)
    oracle={'position_world_m':[.4969098174320634,-2.37729484580756,.9348288902306693],
            'z_range_world_m':[.92,.96]}
    baseline=oriented_policy_source((0,.25,0),target,oracle)
    assert hashlib.sha256(baseline.encode()).hexdigest()=='62925282a21da10197a5cd0f3f7d236ec14b4370cea474697f8afffddf5b176d'
    assert hashlib.sha256(oriented_policy_source((0,.25,0),target).encode()).hexdigest()=='84b268d2e0aa762626beae45b9b9084edce708f79369b3ebbda784753708a8f2'
    current_pos=(.4104425806153241,-2.5261967543350234,1.0894136503658094)
    current_quat=(.0778981124810766,.7601637435567035,.643,0.)
    current_quat=tuple(Rotation.from_quat(current_quat).as_quat())
    pre=(oracle['position_world_m'][0],oracle['position_world_m'][1],oracle['z_range_world_m'][1]+.1)
    state=tmp_path/'dry-run-state.json'
    state.write_text(json.dumps({'eef_position':current_pos,'eef_quat':current_quat,
                                 'pregrasp_position':pre}))
    assert len(approach_steps((0,0,0),(.045,0,0),(0,0,0,1),(0,0,0,1),'combined'))==1
    assert len(approach_steps((0,0,0),(0,0,0),(0,0,0,1),(0,0,0,1),'translate-first'))==1
    for sequence in ('translate-first','combined'):
        steps=approach_steps(current_pos,pre,current_quat,target,sequence)
        distance=math.dist(current_pos,pre)
        rv=(Rotation.from_quat(target)*Rotation.from_quat(current_quat).inv()).as_rotvec()
        expected=(math.floor(distance/.045)+1+math.floor(max(abs(v) for v in rv)/.45)+1
                  if sequence=='translate-first' else
                  max(1,math.ceil(distance/.045),math.ceil(max(abs(v) for v in rv)/.45)))
        assert len(steps)==expected
        assert all(math.dist((0,0,0),delta)<.05 and max(abs(v) for v in turn)<=.5
                   for delta,turn in steps)
        if sequence=='combined':assert all(max(abs(v) for v in turn)<=.45 for _,turn in steps)
        for injected in (None,oracle):
            source=sequenced_policy_source((0,.25,0),target,injected,sequence=sequence)
            validate_generated_policy(source)
            assert 'obs_pre = obs_open' in source
            assert 'grasp_z = (oracle_z[0]+oracle_z[1])/2' in source
        main(['--base-shift','0,.25,0','--grasp-quat',','.join(map(str,target)),
              '--sequence',sequence,'--dry-run','--dry-run-state',str(state)])
        output=capsys.readouterr().out
        assert f'sequence={sequence}' in output and 'approach_steps' in output
        counts=json.loads(output.split('# dry-run approach step counts: ',1)[1].split('\n',1)[0])
        assert counts[sequence]==expected
        assert set(counts)=={'rotate-first','translate-first','combined'}
    main(['--base-shift','0,.25,0','--grasp-quat',','.join(map(str,target)),
          '--sequence','rotate-first','--dry-run','--dry-run-state',str(state)])
    assert '"rotate-first":' in capsys.readouterr().out

def test_failed_step_has_public_target_and_certificate(tmp_path):
    version=tmp_path/'artifacts'/'e'/'cells'/'c'/'v1';version.mkdir(parents=True)
    episode=version/'executions'/'openarm365-dev-test';episode.mkdir(parents=True)
    planner=episode/'planner';planner.mkdir()
    (planner/'continuous-1.json').write_text(json.dumps({'certificate':{'queries':8840,'elapsed_seconds':4.9,'result':'PASS'}}))
    obs={'robot0_left_eef_pos':[.4,-2.5,1.1],'robot0_left_eef_quat':[0,0,0,1]}
    events=[{'operation':'get_observation','status':'completed','result':obs},
            {'operation':'move_delta','source':'left_arm','status':'completed',
             'arguments':{'dx':.03,'dy':0,'dz':0,'rotation_delta':[0,.2,0]}},
            {'operation':'get_observation','status':'completed','result':obs},
            {'operation':'move_delta','source':'left_arm','status':'failed',
             'arguments':{'dx':.02,'dy':0,'dz':0,'rotation_delta':[0,.2,0]},
             'error':{'message':'official_ik_non_success'}}]
    (episode/'trace.json').write_text(json.dumps({'sdk_events':events}))
    (episode/'initial_public_observation.json').write_text(json.dumps({'robot':obs}))
    result=summarize(tmp_path,102,(0,.25,0),'movable_objects',False,1.,
                     grasp_quat=[0,0,0,1],sequence='combined')
    assert len(result['steps'])==2
    assert result['steps'][0]['certificate_queries']==8840
    assert result['steps'][1]['failure_reason']=={'message':'official_ik_non_success'}
    assert result['steps'][1]['target_eef_position_world_m']==pytest.approx([.42,-2.5,1.1])
    assert result['steps'][1]['target_eef_quat_world_xyzw'] is not None
    assert '| 2 |' in markdown(result)

@pytest.mark.parametrize('sequence',('translate-first','combined'))
def test_generated_sequence_remeasures_and_reaches_pregrasp(monkeypatch,sequence):
    from scipy.spatial.transform import Rotation
    target=(.3007057995042732,.9537169507482269,0,0)
    oracle={'position_world_m':[.5,-2.38,.935],'z_range_world_m':[.92,.96]}
    state={'pos':[.41,-2.526,1.089],'quat':[0,0,0,1],'closed':False,'moves':[],'observations':0}
    def observation():
        state['observations']+=1
        return {'robot0_left_eef_pos':state['pos'][:],
                'robot0_left_eef_quat':state['quat'][:],
                'robot0_left_gripper_qpos':[.04,.04]}
    def move(dx,dy,dz,rotation_delta=None):
        turn=rotation_delta or [0,0,0]
        state['moves'].append(((dx,dy,dz),turn,state['pos'][:]))
        state['pos']=[state['pos'][j]+v for j,v in enumerate((dx,dy,dz))]
        state['quat']=(Rotation.from_rotvec(turn)*Rotation.from_quat(state['quat'])).as_quat().tolist()
    def objects():
        lift=max(0,state['pos'][2]-.94) if state['closed'] else 0
        return [{'name':'obj','position':[.5,-2.38,.935+lift]}]
    sdk=types.ModuleType('robot_sdk')
    sdk.sensors=types.SimpleNamespace(get_observation=observation,find_objects=objects)
    sdk.base=types.SimpleNamespace(move_delta=lambda *args:None)
    sdk.left_arm=types.SimpleNamespace(move_delta=move)
    sdk.left_gripper=types.SimpleNamespace(open=lambda:None,close=lambda:state.update(closed=True))
    monkeypatch.setitem(sys.modules,'robot_sdk',sdk)
    exec(sequenced_policy_source((0,.25,0),target,oracle,sequence=sequence),{})
    assert state['observations']>=len(state['moves'])
    assert math.dist(state['pos'],[.5,-2.38,1.04])<1e-9
    assert (Rotation.from_quat(target)*Rotation.from_quat(state['quat']).inv()).magnitude()<1e-9
    before_close=state['moves'][:-4]
    if sequence=='translate-first':
        first_turn=next(i for i,(_,turn,_) in enumerate(before_close) if max(abs(v) for v in turn)>1e-8)
        assert first_turn>0
        assert math.dist(before_close[first_turn][2],[.5,-2.38,1.06])<1e-9
    else:
        delta,turn,_=before_close[0]
        assert math.dist(delta,[0,0,0])>0 and max(abs(v) for v in turn)>0
