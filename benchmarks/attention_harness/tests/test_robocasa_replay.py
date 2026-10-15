from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import time
import time

import numpy as np
import pytest

from benchmarks.attention_harness.ui_media import AttemptCameraRecording, rgb_jpeg


def test_robocasa_jpeg_capture_keeps_native_pixels_and_run_cache(tmp_path):
    colors = iter([35, 180])
    def fetch():
        return rgb_jpeg(np.full((32, 48, 3), next(colors), dtype=np.uint8))
    capture = AttemptCameraRecording(tmp_path/'episode', fetch, run_dir=tmp_path/'run', suite='robocasa')
    capture._frame('initial')
    capture._frame('final')
    assert capture.receipt['initial_frame_saved'] and capture.receipt['final_frame_saved']
    assert (capture.receipt['source_width'],capture.receipt['source_height']) == (48,32)
    assert (tmp_path/'episode/camera_initial.png').read_bytes() != (tmp_path/'episode/camera_final.png').read_bytes()
    assert (tmp_path/'run/public_frame.jpg').read_bytes().startswith(b'\xff\xd8')
    assert not (tmp_path/'run/public_frame.png').exists()


@pytest.mark.parametrize('human_run',[False,True])
@pytest.mark.parametrize('camera_error',[False,True])
def test_robocasa_records_policy_and_closes_camera_before_services(tmp_path,monkeypatch,camera_error,human_run):
    from benchmarks.attention_harness.robocasa_native import formal_runner as module
    order=[]
    class Services:
        human_camera_url = None
        sim_url='http://127.0.0.1:6300';agent_url='http://127.0.0.1:8880';stop_receipt=None
        def __init__(self,**kwargs):
            assert kwargs["human_camera"] is human_run
            self.human_camera_url="http://127.0.0.1:6390" if human_run else None
        def __enter__(self): order.append('services_open');return self
        def __exit__(self,*exc):
            order.append('services_close')
            self.stop_receipt={'reason':'normal_cleanup','services':{key:{'leader_reaped':True,'process_group_gone':True} for key in ['simulator','agent']}}
        def source_unchanged(self): return True
    class Client:
        def __init__(self,*args,**kwargs): pass
        def assert_task(self):return {}
        def _call(self,*args,**kwargs):return {'status':'ok','applied_variation':{'scene_id':'scene','object_set_id':'objects'}}
        def observe(self): return SimpleNamespace(cameras=['base_camera'],language='move the drink')
        def native_success(self): return 'policy' in order
    class Action:
        cancellation_receipts=[];planned_motion_receipts=[];plan_query_receipts=[]
        def __init__(self,**kwargs): pass
        def set_episode_deadline(self,*args): pass
        def set_interrupt_check(self,*args): pass
        def assert_cancellation_available(self): pass
    class Monitor:
        def __init__(self,*args,**kwargs):pass
        def observe(self):return {}
        def write_artifact(self,path,**kwargs):path.write_text('{}');return path
    class Recording:
        def __init__(self,*args,**kwargs):
            assert kwargs['suite']=='robocasa'
            assert callable(kwargs['fetch_rgb']) is human_run
            self.capture=AttemptCameraRecording(args[0],lambda:fetch(),**kwargs) if camera_error else None
        def __enter__(self):
            order.append('camera_start')
            if self.capture:self.capture.__enter__()
            return self
        def bind_trace_origin(self,now):
            assert now>0
            if 'clock' not in order:order.append('clock')
        def __exit__(self,*exc):
            order.append('camera_finish')
            if self.capture:self.capture.__exit__(*exc)
        def finalize(self):
            order.append('video_encode')
            if self.capture:self.capture.finalize()
    def sdk(*args,**kwargs):
        kwargs['clock']()
        return SimpleNamespace(sensors=SimpleNamespace(get_observation=lambda:{},find_objects=lambda:[]))
    def execute(**kwargs):order.append('policy');return SimpleNamespace(status='completed',error=None,call_count=1)
    def fetch(*args,**kwargs):
        if camera_error:raise OSError('camera unavailable')
        return rgb_jpeg(np.zeros((16,16,3),dtype=np.uint8))
    monkeypatch.setattr(module,'DedicatedRobocasaServices',Services)
    monkeypatch.setattr(module,'RobocasaSimClient',Client)
    monkeypatch.setattr(module,'AgentServerActionBackend',Action)
    monkeypatch.setattr(module,'SafetyMonitorBackend',Monitor)
    monkeypatch.setattr(module,'AttemptCameraRecording',Recording)
    monkeypatch.setattr(module,'RobocasaMobileSDK',sdk)
    monkeypatch.setattr(module,'ModeBoundPerceptionBackend',lambda *args,**kwargs:None)
    monkeypatch.setattr(module,'RobocasaGTPerception',lambda *args,**kwargs:None)
    monkeypatch.setattr(module,'execute_formal_policy',execute)
    monkeypatch.setattr(module,'probe_sandbox',lambda:{'host_home_visible':False,'worker_has_simulator_client':False})
    monkeypatch.setattr(module,'validate_generated_policy',lambda code:None)
    monkeypatch.setattr(module,'robocasa_camera_frame',fetch)
    def human_rgb(origin):
        assert origin=='http://127.0.0.1:6390'
        if camera_error:raise OSError('camera unavailable')
        return np.zeros((1080,1920,3),dtype=np.uint8),time.time(),1
    monkeypatch.setattr(module,'robocasa_human_rgb',human_rgb)
    monkeypatch.setattr(module,'_config',lambda req:{'scene_id':'scene','object_set_id':'objects','camera_names':['base_camera'],
        'task_prompt':'move the drink','port_offset':800,'safety_limits':{},'sim_service':{'revision':'sim'},
        'agent_service':{'revision':'agent'},'task_source':{'revision':'task'},'sim_runtime':{}})
    code=tmp_path/'policy.py';code.write_text('pass\n');cfg=tmp_path/'config';cfg.write_text('{}')
    request=SimpleNamespace(validate=lambda:None,suite='robocasa',task_id='counter_to_sink',seed=101,
        policy_code_path=code,policy_sha256=hashlib.sha256(code.read_bytes()).hexdigest(),config_path=cfg,
        config_sha256=hashlib.sha256(cfg.read_bytes()).hexdigest(),artifact_root=tmp_path/'attention-robocasa-recording'/'attempts',
        effective_attempt_deadline_seconds=300,run_id='run:attention-robocasa-recording' if human_run else None,attempt_id='attempt:attention-robocasa-recording:0' if human_run else None,attention_input=None,sdk_call_limit=None,entry_lock=None)
    result=module.RobocasaFormalSuiteRunner(sim_source_root=tmp_path,agent_source_root=tmp_path,
        task_source_root=tmp_path,sim_python=Path('/usr/bin/python3'),agent_python=Path('/usr/bin/python3'),record_replay=True).execute(request)
    assert result['status']=='completed',result['error']
    assert order==['services_open','camera_start','clock','policy','camera_finish','services_close','video_encode']
    assert result['native_success'] is True
    if camera_error:
        receipt=json.loads(next(tmp_path.rglob('camera_recording.json')).read_text())
        assert 'camera unavailable' in receipt['error']
        assert receipt['video_saved'] is False


def test_mobile_sdk_uses_recording_clock_for_sdk_completion():
    from benchmarks.attention_harness.robocasa_native.mobile_sdk import RobocasaMobileSDK
    class Backend:
        control_frame='arm_base'
        def move_base_delta(self,*args,**kwargs):return None
    times=iter([100.,100.2,100.8]);events=[];backend=Backend()
    sdk=RobocasaMobileSDK(backend,action_backend=backend,event_sink=events.append,clock=lambda:next(times))
    sdk.base.move_delta(.1,0)
    assert events[0]['timestamp']==.8
    assert events[0]['duration_ms']==600.
