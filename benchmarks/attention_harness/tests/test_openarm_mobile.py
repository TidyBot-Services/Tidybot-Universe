"""Acceptance contracts for independent mobile OpenArm, including negative cases."""
import importlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import pytest
from benchmarks.attention_harness.robocasa_native.openarm_backend import OpenArmActionBackend

ROOT = Path(__file__).resolve().parents[3]

def mobile(name):
    return importlib.import_module('openarm_mobile.' + name)


def test_native_display_three_mounts_policy_camera_is_separate():
    cameras = mobile('display').camera_specs('left')
    assert set(cameras) == {'chest', 'wrist', 'external'}
    assert all((c['width'], c['height']) == (1920, 1080) for c in cameras.values())
    assert cameras['chest']['parent'] == 'openarm_body_link0'
    assert cameras['wrist']['parent'] == 'openarm_left_link7'
    assert cameras['external']['parent'] is None
    assert mobile('display').POLICY_CAMERA == {'name':'base_camera','width':640,'height':480}
    assert mobile('display').camera_specs('right')['wrist']['parent'] == 'openarm_right_link7'


def test_mobile_model_physical_joints_inertia_geometry_and_preserved_arms():
    value = mobile('model').build_model()
    xml = ET.parse(value['path']).getroot()
    old = ET.parse(ROOT / 'openarm_phase0_recheck/model/compat_v6/robot.urdf').getroot()
    for name, kind in [('base_x_joint','prismatic'),('base_y_joint','prismatic'),('base_yaw_joint','revolute')]:
        assert xml.find(f"joint[@name='{name}']").get('type') == kind
    base = xml.find("link[@name='base_link']")
    assert float(base.find('inertial/mass').get('value')) == 50
    assert base.find('collision/geometry') is not None
    assert xml.find("joint[@name='openarm_body_world_joint']/parent").get('link') == 'base_link'
    for node in old.findall('joint'):
        if node.get('name') != 'openarm_body_world_joint':
            assert ET.tostring(xml.find(f"joint[@name='{node.get('name')}']")) == ET.tostring(node)
    assert value['source_sha256'] and value['sha256']


def test_sdk_base_dispatches_real_epoch_action_and_attests_capability():
    calls=[]
    def transport(method,path,body,timeout):
        calls.append((method,path,body))
        if path=='/health':return {'ready':True,'robot':{'model':'OpenArm','execution_arm':'left'},'capabilities':['mobile_base_joint_drive']}
        if path=='/state':return {'robot':{'model':'OpenArm','execution_arm':'left'},'epoch':4,'base':{'qpos':[0,0,0]}}
        if path=='/base/actions':return {'id':'4:b1','status':'running'}
        return {'id':'4:b1','status':'completed','physical_steps':720,'base_error_m':0.0001}
    backend=mobile('backend').MobileOpenArmActionBackend('http://127.0.0.1:8872',transport=transport)
    backend.move_base_delta(.1,.02,.2)
    assert ('POST','/base/actions',{'delta':[.1,.02,.2],'epoch':4}) in calls
    assert backend.action_receipts[-1]['physical_steps']==720


@pytest.mark.parametrize('delta',[(float('nan'),0,0),(.31,0,0),(0,0,.51),(True,0,0)])
def test_sdk_base_rejects_invalid_without_dispatch(delta):
    calls=[]
    def transport(method,path,body,timeout):
        calls.append(path)
        return {'ready':True,'robot':{'model':'OpenArm','execution_arm':'left'},'capabilities':['mobile_base_joint_drive']}
    backend=mobile('backend').MobileOpenArmActionBackend('http://127.0.0.1:8872',transport=transport)
    with pytest.raises(ValueError):backend.move_base_delta(*delta)
    assert calls==['/health']


def test_fixed_base_still_rejects_motion():
    def transport(*args):return {'ready':True,'robot':{'model':'OpenArm','execution_arm':'left'}}
    backend=OpenArmActionBackend('http://127.0.0.1:8872',transport=transport)
    from tidybot_sdk import CapabilityNotAvailableError
    with pytest.raises(CapabilityNotAvailableError):backend.move_base_delta(.1,0,0)


def test_synchronized_recording_actual_sampling_and_no_fabricated_fps():
    result=mobile('recording').sampling_metrics([10.,10.2,10.7,11.])
    assert result['actual_capture_fps']==3.
    assert result['frame_count']==4
    assert result['capture_span_seconds']==1.
    assert result['max_gap_seconds']==pytest.approx(.5)
    assert mobile('recording').sampling_metrics([])['actual_capture_fps'] is None


def test_per_view_preview_roundtrip_is_bound_and_unknown_view_is_rejected(tmp_path):
    from benchmarks.attention_harness.live_preview import save_preview,read_preview
    run_id='run:attention-robocasa-counter_to_sink-seed101-testmobile'
    run_dir=tmp_path/run_id.removeprefix('run:');run_dir.mkdir()
    attempt_id='attempt:'+run_dir.name+':0'
    for index,key in enumerate(['external','chest','wrist'],1):
        save_preview(run_dir,run_id=run_id,attempt_id=attempt_id,frame=b'\xff\xd8'+key.encode()+b'\xff\xd9',sequence=index,captured_at=1234.,width=1920,height=1080,view=key)
    assert [read_preview(run_dir,run_id,attempt_id,key)[0]['sequence'] for key in ['external','chest','wrist']]==[1,2,3]
    assert read_preview(run_dir,run_id,attempt_id,'chest')[0]['timestamp_basis']=='physical_snapshot_wall_clock'
    assert read_preview(run_dir,run_id,attempt_id) is None
    with pytest.raises(ValueError):
        save_preview(run_dir,run_id=run_id,attempt_id=attempt_id,frame=b'\xff\xd8\xff\xd9',sequence=1,captured_at=1234.,width=1920,height=1080,view='../secret')


def test_renderer_reads_actual_geometry_and_does_not_step_physics():
    import ast
    source=ast.parse((ROOT/'openarm_mobile/display.py').read_text())
    assert not any(isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='step' for node in ast.walk(source))
    assert 'export_scene(self.kitchen)' in (ROOT/'openarm_mobile/service.py').read_text()
    assert 'put_nowait(snapshot)' in (ROOT/'openarm_mobile/service.py').read_text()


def test_original_approved_model_and_mount_stay_fixed():
    import hashlib
    model=ROOT/'openarm_phase0_recheck/model/compat_v6/robot.urdf'
    assert hashlib.sha256(model.read_bytes()).hexdigest()=='ae5f37c7f0c509df81d01229c13f0f624d619e253542cedfb0008aaa6d827456'
    assert ET.parse(model).find("joint[@name='openarm_body_world_joint']/parent").get('link')=='world'


def test_actual_sampling_is_independent_of_encoded_clock():
    metrics=mobile('recording').sampling_metrics([20,20.8,22,22.4])
    assert metrics['actual_capture_fps']==pytest.approx(1.25)
    assert 'encoded_fps' not in metrics


def test_mobile_config_attests_model_sources_runtime_and_keeps_policy_camera():
    from types import SimpleNamespace
    import copy
    config=mobile('config')
    path=ROOT/'openarm_mobile/launch_configs/counter_to_sink_left_candidate5.json'
    value=json.loads(path.read_text())
    request=SimpleNamespace(task_id='counter_to_sink',seed=101)
    # The approved historical binding remains unchanged and refuses shared
    # source drift. Validate the separately published engineering version.
    with pytest.raises(ValueError,match='source differs'):config.validate_config(request,value)
    if (ROOT/'openarm_bimanual/current.json').exists():
        from openarm_bimanual.entry import resolve_daily_catalog
        catalog=json.loads(resolve_daily_catalog(root=ROOT).read_text())
        # The daily engineering clone attests current source without rewriting
        # either historical approval or the independent Attention-policy pointer.
        value=next(v for p in catalog['profiles']
            for v in [json.loads(Path(p['config']).read_text())]
            if v.get('schema_version')==config.SCHEMA and v.get('robot')=={'model':'OpenArm','execution_arm':'left'})
    else:
        active=json.loads((ROOT/'attention_policy_switch/current.json').read_text())
        value=json.loads(Path(active['config']).read_text())
    assert config.validate_config(request,value)['camera_names']==['base_camera']
    for key in ['model_sha256','scene_id']:
        bad=copy.deepcopy(value)
        if key=='scene_id':
            bad['camera_names']=['chest']
        else:bad[key]='0'*64
        with pytest.raises(ValueError):config.validate_config(request,bad)
    bad=copy.deepcopy(value);bad['service_files']['openarm_mobile/service.py']='0'*64
    with pytest.raises(ValueError,match='source differs'):config.validate_config(request,bad)
    bad=copy.deepcopy(value);bad['safety_limits']['max_base_delta_m']=.31
    with pytest.raises(ValueError,match='safety limit'):config.validate_config(request,bad)


def test_independent_mobile_dag_policy_config_binding():
    import hashlib
    graph=json.loads((ROOT/'openarm_mobile/launch_configs/openarm-mobile-counter-to-sink-left_candidate5_graph.json').read_text())
    entry=graph['entries'][0]['attentionbench']
    assert graph['graph_id']=='openarm-mobile-counter-to-sink-left'
    assert graph['formal_eligible'] is False
    for field,sha in [('generated_policy_file','approved_policy_sha256'),('formal_config_file','approved_config_sha256')]:
        assert hashlib.sha256((ROOT/entry[field]).read_bytes()).hexdigest()==entry[sha]


def test_sdk_local_base_motion_rotates_using_actual_pose():
    import numpy as np
    commands=[]
    pose=np.eye(4);pose[:2,:2]=[[0,-1],[1,0]]
    def transport(method,path,body,timeout):
        if path=='/health':return {'ready':True,'robot':{'model':'OpenArm','execution_arm':'left'},'capabilities':['mobile_base_joint_drive']}
        if path=='/state':return {'robot':{'model':'OpenArm','execution_arm':'left'},'epoch':3,'base':{'world_pose':pose.tolist()}}
        if path=='/base/actions':commands.append(body);return {'id':'3:b1','status':'running'}
        return {'id':'3:b1','status':'completed','physical_steps':720}
    backend=mobile('backend').MobileOpenArmActionBackend('http://127.0.0.1:8872',transport=transport)
    backend.move_base_delta(.1,0,.03,frame='local')
    assert commands==[{'delta':[0.,.1,.03],'epoch':3}]


def test_preview_producer_failure_is_explicit_and_preserves_last_frame(tmp_path):
    from benchmarks.attention_harness.live_preview import save_preview,read_preview,mark_preview_offline,finish_preview
    rid="run:attention-mobile-offline";aid="attempt:attention-mobile-offline:0"
    rd=tmp_path/rid.removeprefix("run:");rd.mkdir()
    save_preview(rd,run_id=rid,attempt_id=aid,frame=b"\xff\xd8last\xff\xd9",sequence=3,captured_at=1234.,width=1920,height=1080,view="chest")
    mark_preview_offline(rd,rid,aid,"renderer failed","chest")
    header,frame=read_preview(rd,rid,aid,"chest")
    assert header["producer_error"]=="renderer failed" and header["ended_at"] is None
    assert frame==b"\xff\xd8last\xff\xd9"
    finish_preview(rd,rid,aid,"chest")
    assert read_preview(rd,rid,aid,"chest")[0]["ended_at"]>0


def test_mobile_stop_waits_for_actual_base_velocity_without_stepping():
    speeds=iter([.02,.004]);calls=[]
    def transport(method,path,body,timeout):
        calls.append(path)
        if path=="/health":return {"ready":True,"robot":{"model":"OpenArm","execution_arm":"left"},"capabilities":["mobile_base_joint_drive"]}
        if path=="/stop":return {"active_action":None,"robot":{"model":"OpenArm","execution_arm":"left"}}
        return {"base":{"qvel":[next(speeds),0,0]}}
    backend=mobile("backend").MobileOpenArmActionBackend("http://127.0.0.1:8872",transport=transport)
    result=backend.stop()
    assert result["base_physical_rest_confirmed"] is True
    assert calls==["/health","/stop","/state","/state"]


def test_promoted_mobile_default_preserves_all_original_approved_profiles():
    from benchmarks.attention_harness.ui_launch import load_catalog
    path=ROOT/'benchmarks/attention_harness/protocol/v2/skill_graphs/live_run_catalog.json'
    profiles=load_catalog(path)
    assert len(profiles)==5
    mobile_profile=next(p for p in profiles if p.get('base_capability'))
    openarm_left=[p for p in profiles if p.get('robot')=={'model':'OpenArm','execution_arm':'left'}]
    assert openarm_left[0]['id']==mobile_profile['id']
    assert mobile_profile['code']==str(ROOT/'openarm_mobile/policies/counter_to_sink_mobile_v1.py')
    assert mobile_profile['approved_policy_sha256']=='de68d0397eb561181ed18ce54e18402f8faa7d914dba3f60cab80c59b6f28431'
    assert mobile_profile['approved_config_sha256']=='6abe32992c9f38a098775d92c32172a90920abbfebe67bcee98d87a9be480563'
    assert mobile_profile['skill_graph_sha256']=='8ee9e41b7d6ad8f08dae91f1fe5137a6361ac8d83a5948fc3fa4e69c16831545'
    original=[p for p in profiles if not p.get('base_capability')]
    assert len(original)==4
    assert {p['robot']['execution_arm'] for p in original if p.get('robot',{}).get('model')=='OpenArm'}=={'left','right'}
    assert {p['approved_policy_sha256'] for p in original if p.get('robot',{}).get('model')=='OpenArm'}=={'1746a6d97dba999be6f567a2983e82f98723f1293d3f271a9bb5ce259f18d960'}
