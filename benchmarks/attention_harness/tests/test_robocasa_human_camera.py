"""Display-camera regressions: native pixels, original policy inputs and identity."""
import io
import json
import threading
import time
from dataclasses import dataclass, replace
from email.message import Message
from types import SimpleNamespace
from urllib.request import urlopen

import numpy as np
import pytest
from PIL import Image

from benchmarks.attention_harness.public_station import publish_public_station, read_public_station
from benchmarks.attention_harness.ui_media import AttemptCameraRecording, rgb_jpeg


def camera_env():
    @dataclass
    class Config:
        uid: str = "base_camera"
        width: int = 512
        height: int = 512
        pose: object = "original-pose"
        fov: float = 1.745329
        near: float = .01
        far: float = 10
        intrinsic: object = None
        entity_uid: str = "base_link"
        mount: object = None
    source = SimpleNamespace(config=Config(), entity=object())
    sensors = {"base_camera": source, "wrist_camera": SimpleNamespace(config=Config(uid="wrist_camera"))}
    scene = SimpleNamespace(human_render_cameras={})
    return SimpleNamespace(_sensors=sensors, scene=scene), source


def test_native_human_camera_preserves_sensor_config_mount_fov_and_registry():
    from benchmarks.attention_harness.robocasa_native.human_camera_service import configure_human_camera, CAMERA_NAME
    env, source = camera_env()
    original = source.config.__dict__.copy()
    sensors = env._sensors
    configure_human_camera(env, lambda config, scene: SimpleNamespace(config=config))
    display = env._human_render_cameras[CAMERA_NAME].config
    assert env._sensors is sensors and source.config.__dict__ == original
    assert (display.width, display.height) == (1920,1080)
    assert all((x.config.width,x.config.height)==(512,512) for x in sensors.values())
    assert display.pose is source.config.pose and display.mount is source.entity
    assert (display.fov,display.near,display.far)==(source.config.fov,source.config.near,source.config.far)
    assert display is not source.config and env.scene.human_render_cameras is env._human_render_cameras
    assert CAMERA_NAME not in sensors


def test_capture_runs_no_physics_and_keeps_actual_clock_with_five_hz_bound():
    from benchmarks.attention_harness.robocasa_native.human_camera_service import HumanCamera, CAMERA_NAME
    env,_=camera_env(); calls=[]
    env.scene.update_render=lambda **kw: calls.append(kw)
    pixels=np.zeros((1,1080,1920,3),dtype=np.uint8)
    env.scene.get_human_render_camera_images=lambda **kw: {CAMERA_NAME:pixels}
    camera=HumanCamera(); camera.capture(env,now=1)
    first,frame=camera.snapshot()
    assert Image.open(io.BytesIO(frame)).size==(1920,1080)
    assert first['policy_camera_sizes']=={'base_camera':[512,512],'wrist_camera':[512,512]}
    assert first['sequence']==1 and first['captured_at']<=time.time()
    camera.capture(env,now=1.1)
    assert camera.snapshot()[0]['sequence']==1
    pixels[...,1]=200;camera.capture(env,now=1.3)
    second,frame2=camera.snapshot()
    assert second['sequence']==2 and frame2!=frame
    assert calls==[{'update_sensors':False,'update_human_render_cameras':True}]*2


def test_low_resolution_or_render_failure_cannot_be_reported_as_hd():
    from benchmarks.attention_harness.robocasa_native.human_camera_service import HumanCamera, CAMERA_NAME
    env,_=camera_env();env.scene.update_render=lambda **kw:None
    env.scene.get_human_render_camera_images=lambda **kw:{CAMERA_NAME:np.zeros((1,512,512,3),dtype=np.uint8)}
    camera=HumanCamera();camera.capture(env)
    state,frame=camera.snapshot()
    assert frame is None and state['sequence']==0 and 'native 1920x1080' in state['error']


def test_http_reads_cached_capture_without_rendering_and_preserves_stale_time():
    from benchmarks.attention_harness.robocasa_native.human_camera_service import HumanCamera
    from benchmarks.attention_harness.ui_media import robocasa_human_rgb
    camera=HumanCamera();camera.frame=rgb_jpeg(np.zeros((1080,1920,3),dtype=np.uint8))
    camera.sequence=7;camera.captured_at=time.time()-9
    server,thread=camera.serve(0)
    try:
        rgb,stamp,sequence=robocasa_human_rgb(f'http://127.0.0.1:{server.server_port}')
        assert rgb.shape==(1080,1920,3) and stamp==camera.captured_at and sequence==7
        assert time.time()-stamp>8.5
        assert camera.sequence==7 and len(camera.render_ms)==0
    finally:server.shutdown();server.server_close();thread.join(2)


@pytest.mark.parametrize('kind',['low_resolution','bad_timestamp','bad_sequence','wrong_mime','too_large'])
def test_human_transport_rejects_invalid_native_frames(monkeypatch,kind):
    import benchmarks.attention_harness.ui_media as module
    frame=rgb_jpeg(np.zeros((512,512,3) if kind=='low_resolution' else (1080,1920,3),dtype=np.uint8))
    headers=Message();headers['Content-Type']='text/plain' if kind=='wrong_mime' else 'image/jpeg'
    headers['X-Captured-At']='nan' if kind=='bad_timestamp' else str(time.time())
    headers['X-Sequence']='0' if kind=='bad_sequence' else '1'
    class Response(io.BytesIO):pass
    response=Response(b'x'*8_000_001 if kind=='too_large' else frame);response.headers=headers
    monkeypatch.setattr(module,'urlopen',lambda *a,**kw:response)
    with pytest.raises(ValueError):module.robocasa_human_rgb('http://127.0.0.1:1234')
    with pytest.raises(ValueError):module.robocasa_human_rgb('http://example.com:1234')


def test_human_station_requires_loopback_run_attempt_and_display_device(tmp_path):
    run_dir=tmp_path/'attention-robocasa-hd';run='run:'+run_dir.name;attempt='attempt:'+run_dir.name+':0'
    args=dict(run_dir=run_dir,run_id=run,attempt_id=attempt,suite='robocasa',
              origin='http://127.0.0.1:6390',device_id='human_base_camera',camera_transport='robocasa_human_http')
    publish_public_station(**args)
    station=read_public_station(tmp_path,run,'robocasa',{attempt})
    assert station['camera_transport']=='robocasa_human_http'
    assert read_public_station(tmp_path,run,'robocasa',{'another'}) is None
    for changes in [dict(origin='http://example.com:6390'),dict(device_id='maniskill_base'),dict(attempt_id='attempt:wrong:0')]:
        with pytest.raises(ValueError):publish_public_station(**{**args,**changes})
    payload=json.loads((run_dir/'public_station.json').read_text());payload['camera_transport']='unknown'
    (run_dir/'public_station.json').write_text(json.dumps(payload))
    assert read_public_station(tmp_path,run,'robocasa',{attempt}) is None


def test_recording_uses_native_human_source_sequence_and_original_capture_age(tmp_path):
    from benchmarks.attention_harness.live_preview import read_preview
    name='attention-robocasa-hd';run='run:'+name;attempt='attempt:'+name+':0';run_dir=tmp_path/name
    rgb=np.full((1080,1920,3),80,dtype=np.uint8);captured=time.time()-3
    capture=AttemptCameraRecording(run_dir/'attempts/0',lambda:pytest.fail('must use display RGB'),
        fetch_rgb=lambda:(rgb,captured,9),run_dir=run_dir,run_id=run,attempt_id=attempt,suite='robocasa')
    capture._frame('final')
    header,frame=read_preview(run_dir,run,attempt)
    assert (header['source_width'],header['source_height'])==(1920,1080)
    assert header['captured_at']==captured and header['sequence']==9
    assert Image.open(io.BytesIO(frame)).size==(1920,1080)
    assert Image.open(run_dir/'attempts/0/camera_final.png').size==(1920,1080)


def test_human_preview_projection_and_attempt_endpoint_use_run_bound_cache(tmp_path,monkeypatch):
    from benchmarks.attention_harness.tests.test_live_preview import start_server
    from benchmarks.attention_harness.tests.test_attention_store import records
    from benchmarks.attention_harness.core.store import AttentionStore
    from benchmarks.attention_harness.live_preview import save_preview,finish_preview
    from benchmarks.attention_harness.core.models import RunStatus
    from urllib.parse import urlencode,quote
    store=AttentionStore(tmp_path/'fixture.sqlite3');run,_,_,_=records()
    name='attention-robocasa-hd';run=replace(run,run_id='run:'+name,suite='robocasa',execution_target='robocasa_sim')
    store.create_run(run);store.transition_run(run.run_id,RunStatus.RUNNING,event_key='fixture-start')
    attempt='attempt:'+name+':0';run_dir=tmp_path/name
    publish_public_station(run_dir=run_dir,run_id=run.run_id,attempt_id=attempt,suite='robocasa',
        origin='http://127.0.0.1:9',device_id='human_base_camera',camera_transport='robocasa_human_http')
    save_preview(run_dir,run_id=run.run_id,attempt_id=attempt,frame=rgb_jpeg(np.zeros((1080,1920,3),dtype=np.uint8)),
                 sequence=2,captured_at=time.time()-8,width=1920,height=1080)
    import benchmarks.attention_harness.ui_server as module
    monkeypatch.setattr(module,'robocasa_human_rgb',lambda *a:pytest.fail('preview must read cache only'))
    server,thread,base=start_server(store,tmp_path)
    try:
        with urlopen(base+'/api/runs/'+quote(run.run_id,safe='')) as response:snapshot=json.load(response)
        assert '/api/station/preview-frame?' in snapshot['station']['camera_preview_url']
        query=urlencode({'run':run.run_id,'attempt':attempt})
        with urlopen(base+'/api/station/preview-state?'+query) as response:state=json.load(response)
        assert state['state']=='stale' and state['source_width']==1920 and state['age_seconds']>=8
        finish_preview(run_dir,run.run_id,attempt)
        with urlopen(base+'/api/station/preview-state?'+query) as response:state=json.load(response)
        assert state['state']=='ended'
    finally:server.shutdown();server.server_close();thread.join(2)


@pytest.mark.parametrize('enabled',[False,True])
def test_service_wrapper_is_opt_in_and_retains_original_entry_and_sdk_endpoints(tmp_path,monkeypatch,enabled):
    import benchmarks.attention_harness.robocasa_native.formal_services as module
    for name in ['maniskill_server/__main__.py','server.py']:
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('')
    monkeypatch.setattr(module,'simulator_runtime_identity',lambda *a:{})
    monkeypatch.setattr(module.DedicatedRobocasaServices,'_check_source',lambda *a:{})
    monkeypatch.setattr(module,'RobocasaSimClient',lambda *a,**kw:SimpleNamespace(assert_task=lambda:{'task':'ok'}))
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def connect_ex(self,endpoint):return 1
    monkeypatch.setattr(module.socket,'socket',lambda *a,**kw:Socket())
    class Health:
        status=200
        def __enter__(self):return self
        def __exit__(self,*a):pass
    monkeypatch.setattr(module.urllib.request,'urlopen',lambda *a,**kw:Health())
    commands=[]
    monkeypatch.setattr(module.DedicatedRobocasaServices,'_spawn',lambda self,name,command,**kw:commands.append((name,command,kw)))
    monkeypatch.setattr(module.DedicatedRobocasaServices,'_wait',lambda self,name,probe:probe())
    monkeypatch.setenv('ATTENTIONBENCH_HUMAN_CAMERA_PORT','1')
    services=module.DedicatedRobocasaServices(task_id='counter_to_sink',sim_source_root=tmp_path,
        agent_source_root=tmp_path,task_source_root=tmp_path,sim_python=__import__('pathlib').Path(__import__('sys').executable),
        agent_python=__import__('pathlib').Path(__import__('sys').executable),expected_sim={},expected_agent={},
        expected_task={},expected_runtime={},port_offset=800,log_dir=tmp_path/'logs',deadline=time.monotonic()+10,human_camera=enabled)
    services.__enter__();services._stopped.set();services._watchdog.join(2)
    sim=commands[0];agent=commands[1]
    assert sim[0]=='simulator' and agent[0]=='agent'
    assert sim[1][-3:]==['--port-offset','800','--no-mocap-bridge']
    assert agent[1][-3:]==['--no-service-manager','--no-dashboard','--no-reset-on-release']
    assert sim[2]['env']['PYTHONPATH']==str(tmp_path)
    if enabled:
        assert sim[1][1].endswith('/human_camera_service.py')
        assert sim[2]['env']['ATTENTIONBENCH_HUMAN_CAMERA_PORT']=='6390'
        assert services.human_camera_url=='http://127.0.0.1:6390'
    else:
        assert sim[1][1:3]==['-m','maniskill_server']
        assert 'ATTENTIONBENCH_HUMAN_CAMERA_PORT' not in sim[2]['env']
        assert services.human_camera_url is None


def test_reconfiguration_discards_old_scene_frame_and_bypasses_previous_rate_window():
    from benchmarks.attention_harness.robocasa_native.human_camera_service import HumanCamera,CAMERA_NAME
    env,_=camera_env();pixels=np.zeros((1,1080,1920,3),dtype=np.uint8)
    env.scene.update_render=lambda **kw:None
    env.scene.get_human_render_camera_images=lambda **kw:{CAMERA_NAME:pixels}
    camera=HumanCamera();camera.capture(env,now=1)
    state,old=camera.snapshot();assert state['sequence']==1 and old is not None
    camera.invalidate();state,frame=camera.snapshot()
    assert frame is None and state['captured_at'] is None and state['policy_camera_sizes']=={}
    pixels[...,2]=255;camera.capture(env,now=1.01)
    state,new=camera.snapshot()
    assert state['sequence']==2 and new!=old and state['captured_at'] is not None
