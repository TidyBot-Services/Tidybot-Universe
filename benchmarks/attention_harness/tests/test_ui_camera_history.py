from __future__ import annotations
import hashlib,json,time
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from benchmarks.attention_harness.ui_media import rgb_png
from benchmarks.attention_harness.robosuite_memory.formal_runner import _config
from benchmarks.attention_harness.formal_runner_boundary import FormalRunRequest


def test_1080p_config_and_native_public_rgb(tmp_path):
    code=tmp_path/'policy.py'; code.write_text('pass\n')
    config=tmp_path/'config.json'
    value={'schema_version':'attentionbench.robosuite-formal-config.v1','suite':'robosuite',
      'task_id':'cube_lift','seed':101,'perception_mode':'sim_gt','camera_name':'agentview',
      'horizon':500,'camera_width':1920,'camera_height':1080,'scene_id':'s','object_set_id':'o',
      'service_revision':'a'*40,'safety_limits':{'max_delta_m':.25,'max_observed_step_m':.5}}
    config.write_text(json.dumps(value))
    def request():
        return FormalRunRequest(suite='robosuite',task_id='cube_lift',seed=101,
          policy_code_path=code,policy_sha256=hashlib.sha256(code.read_bytes()).hexdigest(),
          config_path=config,config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
          artifact_root=tmp_path/'artifacts',overall_deadline_seconds=300)
    assert _config(request())['camera_width']==1920
    frame=rgb_png(np.zeros((1080,1920,3),dtype=np.uint8))
    import struct
    assert struct.unpack('>II',frame[16:24])==(1920,1080)
    value['camera_width']=2049;config.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='camera_width'): _config(request())


def test_attempt_recording_saves_rgb_before_service_closes_and_keeps_error_nonfatal(tmp_path,monkeypatch):
    from benchmarks.attention_harness import ui_media
    colors=iter([40,90,150]);closed=False
    def fetch():
        assert not closed
        return rgb_png(np.full((16,16,3),next(colors,150),dtype=np.uint8))
    monkeypatch.setattr(ui_media.AdvisorCameraRecorder,'start',lambda self: None)
    monkeypatch.setattr(ui_media.AdvisorCameraRecorder,'stop',lambda self: None)
    run_dir=tmp_path/'run';episode=run_dir/'attempts'/'episode'
    with ui_media.AttemptCameraRecording(episode,fetch,run_dir=run_dir) as capture:
        pass
    closed=True
    assert (episode/'camera_initial.png').read_bytes() != (episode/'camera_final.png').read_bytes()
    assert (run_dir/'public_frame.png').read_bytes()==(episode/'camera_final.png').read_bytes()
    receipt=json.loads((episode/'camera_recording.json').read_text())
    assert receipt['initial_frame_saved'] and receipt['final_frame_saved']
    assert receipt['source_width']==16 and receipt['source_height']==16
    def failed(): raise OSError('camera offline')
    with ui_media.AttemptCameraRecording(tmp_path/'broken',failed) as broken: pass
    assert 'camera offline' in broken.receipt['error']


def test_camera_files_are_authorized_hash_bound_episode_evidence(tmp_path):
    from benchmarks.attention_harness.episode_trace import _evidence_refs
    from benchmarks.attention_harness.core.models import TraceVisibility
    from benchmarks.attention_harness.ui_media import authorized_evidence_path,render_evidence
    episode=tmp_path/'episode';episode.mkdir()
    frame=rgb_png(np.zeros((1080,1920,3),dtype=np.uint8))
    (episode/'camera_initial.png').write_bytes(frame);(episode/'camera_final.png').write_bytes(frame)
    refs=_evidence_refs(episode,public_visibility=(TraceVisibility.ADVISOR,),stdout='',stderr='',context={})
    from dataclasses import asdict
    evidence=[asdict(ref) for ref in refs]
    assert {'evidence:camera_initial.png','evidence:camera_final.png'} <= {e['evidence_id'] for e in evidence}
    # Enum values serialize as the same Advisor visibility accepted by media routes.
    path,mime=authorized_evidence_path(tmp_path,{'evidence':evidence},'evidence:camera_final.png')
    assert render_evidence(path,mime)==(frame,'image/png')
    path.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='hash mismatch'):
        authorized_evidence_path(tmp_path,{'evidence':evidence},'evidence:camera_final.png')


@pytest.mark.parametrize('suite,task',[('robosuite','cube_lift'),('robocasa','counter_to_sink')])
def test_ui_launch_enables_recording_and_exposes_1080p_without_creating_run(tmp_path,monkeypatch,suite,task):
    from benchmarks.attention_harness import ui_launch
    from benchmarks.attention_harness.core.store import AttentionStore
    code=tmp_path/'policy.py';code.write_text('pass\n')
    config=tmp_path/'config.json';config.write_text('{}')
    profile={'id':'fhd','suite':suite,'task':task,'seed':101,
      'execution_target':suite+'_sim','service_source_root':str(tmp_path),
      'sim_source_root':str(tmp_path),'agent_source_root':str(tmp_path),'task_source_root':str(tmp_path),
      'sim_python':str(tmp_path),'agent_python':str(tmp_path),
      'code':str(code),'config':str(config),'camera_width':1920,'camera_height':1080,
      'approved_policy_sha256':hashlib.sha256(code.read_bytes()).hexdigest(),
      'approved_config_sha256':hashlib.sha256(config.read_bytes()).hexdigest()}
    monkeypatch.setattr(ui_launch,'inspect_formal_entry',lambda **kw:({'sha256':'locked'},None))
    calls=[]
    def popen(command,**kw): calls.append(command);return SimpleNamespace(pid=999999)
    store=AttentionStore(tmp_path/'attention.sqlite3')
    selection={'profile_id':'fhd','seed':101,'attention_policy':'reactive_help',
      'execution_target':suite+'_sim','assistance_mode':'live_human_first',
      'max_attempts':10,'assistance_credits':9,'token_limit':30000,
      'human_deadline_seconds':600,'overall_deadline_seconds':300}
    launch=ui_launch.launch_formal_run(selection,profiles=[profile],store=store,
       artifact_root=tmp_path/'artifacts',popen=popen)
    assert '--record-replay' in calls[0]
    assert launch['locked']['approved_config_sha256']==profile['approved_config_sha256']
    assert ui_launch.public_options([profile])['profiles'][0]['camera_width']==1920
    assert not [e for e in store.events() if e['event_type']=='run.created']


def test_recording_receipt_is_revalidated_before_ui_exposes_it(tmp_path):
    from benchmarks.attention_harness.ui_server import _recording_summary
    episode=tmp_path/'episode';episode.mkdir()
    path=episode/'camera_recording.json'
    path.write_text(json.dumps({'schema_version':'attentionbench.camera-recording.v1',
      'source_width':1920,'source_height':1080,'frame_count':4,'truncated':True,
      'source':'public_rgb_observation','private':'must not expose'}))
    trace={'evidence':[{'kind':'camera_recording','evidence_id':'e',
      'visibility':['advisor'],'uri':'artifact://episode/camera_recording.json',
      'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'mime_type':'application/json'}]}
    result=_recording_summary(tmp_path,trace)
    assert result['source_width']==1920 and result['truncated'] is True
    assert 'private' not in result
    path.write_text('{}')
    assert 'failed validation' in _recording_summary(tmp_path,trace)['error']


def test_sample_video_preserves_elapsed_time_instead_of_accelerating_slow_capture(tmp_path):
    import shutil,subprocess
    from benchmarks.attention_harness.ui_media import AdvisorCameraRecorder
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'): pytest.skip('ffmpeg unavailable')
    frame=rgb_png(np.zeros((16,16,3),dtype=np.uint8))
    recorder=AdvisorCameraRecorder(tmp_path,lambda:frame,image_format='png',fps=5)
    recorder.frames=[frame,frame];recorder.frame_times=[10,11.2]
    video=recorder.stop()
    value=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-of','json',str(video)],text=True))
    assert float(value['format']['duration'])>=1.2


def test_formal_runner_records_before_dedicated_service_is_closed(tmp_path,monkeypatch):
    from benchmarks.attention_harness.robosuite_memory import formal_runner as module
    order=[]
    class Service:
        base_url='http://127.0.0.1:12345';revision='a'*40;stop_receipt=None
        def __init__(self,**kwargs): pass
        def __enter__(self): order.append('service_open');return self
        def __exit__(self,*exc):
            order.append('service_close');self.stop_receipt={'reason':'normal_cleanup','leader_reaped':True,'process_group_gone':True}
        def source_unchanged(self): return True
    class Adapter:
        trace=[]
        def __init__(self,*args,**kwargs): pass
        def reset_attested(self,*args,**kwargs): return {},{}
        def public_rgb_snapshot(self): return np.zeros((16,16,3),dtype=np.uint8), 1., 1
        def native_success(self): return False
    class Monitor:
        def __init__(self,*args,**kwargs): pass
        def observe(self): return {}
        def write_artifact(self,path,**kwargs): path.write_text('{}');return path
    class Recording:
        def __init__(self,*args,**kwargs): pass
        def __enter__(self): order.append('camera_start');return self
        def __exit__(self,*exc): order.append('camera_finish')
        def finalize(self): order.append('video_encode')
    def execute(**kwargs):
        assert kwargs['memory_limit_mb']==2048
        order.append('policy');return SimpleNamespace(status='completed',error=None)
    monkeypatch.setattr(module,'DedicatedRobosuiteService',Service)
    monkeypatch.setattr(module,'RobosuiteSimGTBackend',Adapter)
    monkeypatch.setattr(module,'SafetyMonitorBackend',Monitor)
    monkeypatch.setattr(module,'AttemptCameraRecording',Recording)
    monkeypatch.setattr(module,'ModeBoundPerceptionBackend',lambda *args,**kwargs:None)
    monkeypatch.setattr(module,'RobosuiteGTPerception',lambda *args,**kwargs:None)
    monkeypatch.setattr(module,'TidyBotSDK',lambda *args,**kwargs:SimpleNamespace(sensors=SimpleNamespace(find_objects=lambda:[],get_observation=lambda:{})))
    monkeypatch.setattr(module,'execute_formal_policy',execute)
    monkeypatch.setattr(module,'probe_sandbox',lambda:{'host_home_visible':False,'worker_has_simulator_client':False})
    monkeypatch.setattr(module,'validate_generated_policy',lambda code:None)
    monkeypatch.setattr(module,'_config',lambda req:{'camera_name':'agentview','camera_width':1920,'camera_height':1080,
      'horizon':500,'scene_id':'s','object_set_id':'o','service_revision':'a'*40,
      'safety_limits':{'max_delta_m':.25,'max_observed_step_m':.5}})
    code=tmp_path/'policy.py';code.write_text('pass\n');config=tmp_path/'config';config.write_text('{}')
    req=SimpleNamespace(validate=lambda:None,suite='robosuite',task_id='cube_lift',seed=101,
      policy_code_path=code,policy_sha256=hashlib.sha256(code.read_bytes()).hexdigest(),
      config_path=config,config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
      artifact_root=tmp_path/'attempts',effective_attempt_deadline_seconds=300,
      run_id=None,attempt_id=None,attention_input=None,sdk_call_limit=None,entry_lock=None)
    result=module.RobosuiteFormalSuiteRunner(service_source_root=tmp_path,record_replay=True).execute(req)
    assert order==['service_open','camera_start','policy','camera_finish','service_close','video_encode']
    assert result['native_success'] is False


def test_recording_maps_sdk_origin_to_actual_encoder_sample_gaps(tmp_path, monkeypatch):
    from benchmarks.attention_harness.ui_media import AttemptCameraRecording
    capture=AttemptCameraRecording(tmp_path, lambda:b'')
    capture.recorder.frame_times=[10,10.01,10.5]
    capture.recorder.frames=[b'a',b'b',b'c']
    capture.bind_trace_origin(10.1)
    capture.bind_trace_origin(20)  # Later SDK clock reads must not reset zero.
    monkeypatch.setattr(capture.recorder,'stop',lambda:tmp_path/'video.mp4')
    capture.finalize()
    timing=json.loads((tmp_path/'camera_recording.json').read_text())['replay_timing']
    assert timing['frame_trace_seconds']==[-.1,-.09,.4]
    assert timing['frame_video_seconds']==[0,.04,.53]
    assert timing['event_clock']=='sdk_completion'


def test_recording_without_clock_or_video_never_claims_synchronization(tmp_path,monkeypatch):
    from benchmarks.attention_harness.ui_media import AttemptCameraRecording
    capture=AttemptCameraRecording(tmp_path,lambda:b'')
    capture.recorder.frame_times=[10];capture.recorder.frames=[b'a']
    monkeypatch.setattr(capture.recorder,'stop',lambda:tmp_path/'video.mp4')
    capture.finalize()
    assert 'replay_timing' not in capture.receipt
    capture.bind_trace_origin(10)
    monkeypatch.setattr(capture.recorder,'stop',lambda:None)
    capture.finalize()
    assert 'replay_timing' not in capture.receipt


def test_recording_summary_validates_and_authorizes_sample_mapping(tmp_path):
    from benchmarks.attention_harness.ui_server import _recording_summary
    episode=tmp_path/'episode';episode.mkdir()
    path=episode/'camera_recording.json'
    value={'schema_version':'attentionbench.camera-recording.v1','frame_count':2,
      'replay_timing':{'basis':'relative','event_clock':'sdk_completion',
        'frame_trace_seconds':[-.1,1.2],'frame_video_seconds':[0,1.3],'private':'secret'}}
    def read():
        path.write_text(json.dumps(value))
        trace={'evidence':[{'kind':'camera_recording','evidence_id':'e','visibility':['advisor'],
          'uri':'artifact://episode/camera_recording.json',
          'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'mime_type':'application/json'}]}
        return _recording_summary(tmp_path,trace)
    assert read()['replay_timing']['frame_video_seconds']==[0,1.3]
    assert 'private' not in read()['replay_timing']
    value['replay_timing']['frame_video_seconds']=[0,-2]
    assert 'failed validation' in read()['error']
    value['replay_timing']['frame_video_seconds']=[0,1.3]
    value['replay_timing']['frame_trace_seconds']=[2,1]
    assert 'failed validation' in read()['error']
