"""Run-bound camera regressions; synthetic pixels and isolated stores only."""
import hashlib,io,json,threading,time
from dataclasses import replace
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pytest
from PIL import Image

from benchmarks.attention_harness.core.models import RunStatus
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.public_station import publish_public_station,read_public_station
from benchmarks.attention_harness.live_preview import read_preview
from benchmarks.attention_harness.robocasa365.adapter import Adapter
from benchmarks.attention_harness.tests.test_attention_store import records
from benchmarks.attention_harness.ui_server import create_server
from benchmarks.attention_harness.ui_media import rgb_png


def identity(tmp_path):
    name='attention-robocasa365-camera-fixture'
    return tmp_path/name,'run:'+name,'attempt:'+name+':0'


def test_365_station_accepts_only_its_public_preview_transport(tmp_path):
    directory,run,attempt=identity(tmp_path)
    publish_public_station(run_dir=directory,run_id=run,attempt_id=attempt,suite='robocasa365',
        origin='http://127.0.0.1:9',camera_name='robot0_agentview_left',camera_transport='robocasa365_preview')
    value=read_public_station(tmp_path,run,'robocasa365',{attempt})
    assert value['camera_transport']=='robocasa365_preview'
    assert value['camera_name']=='robot0_agentview_left'
    assert read_public_station(tmp_path,run,'robocasa',{attempt}) is None
    with pytest.raises(ValueError):
        publish_public_station(run_dir=directory,run_id=run,attempt_id=attempt,suite='robocasa365',
            origin='http://127.0.0.1:9',camera_name='robot0_agentview_left')


def test_adapter_publishes_only_after_observation_identity_and_receipt_checks():
    from test_robocasa365 import PublicClient
    client=PublicClient(); adapter=Adapter(client,{'episode_id':'ep','capabilities':{}});seen=[]
    adapter.observation_sink=seen.append
    observation=adapter.public_observation()
    assert seen==[observation]
    client.sequence=1
    with pytest.raises(Exception,match='identity/sequence mismatch'):
        adapter.public_observation()
    assert seen==[observation]


def test_preview_uses_native_pixels_real_age_deduplication_and_finish(tmp_path):
    from benchmarks.attention_harness.robocasa365.preview import RoboCasa365Preview
    directory,run,attempt=identity(tmp_path)
    producer=RoboCasa365Preview(directory,run,attempt,'http://127.0.0.1:9','robot0_agentview_left')
    raw=np.zeros((18,32,3),dtype=np.uint8);raw[:9,:,0]=255;raw[9:,:,2]=255
    obs={'episode_id':'ep','sequence':0,'images':{'robot0_agentview_left_image':raw.tolist()}}
    producer.publish(obs);header,frame=read_preview(directory,run,attempt)
    image=np.asarray(Image.open(io.BytesIO(frame)))
    assert image.shape==(18,32,3) and image[0,0,2]>240 and image[-1,0,0]>240
    captured=header['captured_at'];producer.publish(obs)
    assert read_preview(directory,run,attempt)[0]['captured_at']==captured
    obs['sequence']=1;producer.publish(obs)
    assert read_preview(directory,run,attempt)[0]['sequence']==2
    producer.finish();ended,_=read_preview(directory,run,attempt)
    assert ended['ended_at']>=ended['captured_at']
    assert producer.receipt['frame_count']==2 and producer.receipt['error'] is None
    assert 'token' not in json.dumps(read_public_station(tmp_path,run,'robocasa365',{attempt})).lower()


def test_camera_error_is_nonfatal_and_never_creates_a_fake_frame(tmp_path):
    from benchmarks.attention_harness.robocasa365.preview import RoboCasa365Preview
    directory,run,attempt=identity(tmp_path)
    producer=RoboCasa365Preview(directory,run,attempt,'http://127.0.0.1:9','robot0_agentview_left')
    producer.publish({'episode_id':'ep','sequence':0,'images':{}})
    assert producer.receipt['error'] and producer.receipt['frame_count']==0
    assert read_preview(directory,run,attempt) is None
    producer.finish()


def test_365_preview_http_is_file_only_and_reports_ended(tmp_path,monkeypatch):
    from benchmarks.attention_harness.robocasa365.preview import RoboCasa365Preview
    import benchmarks.attention_harness.ui_server as module
    directory,run_id,attempt=identity(tmp_path);store=AttentionStore(tmp_path/'fixture.sqlite3')
    run,_,_,_=records();run=replace(run,run_id=run_id,suite='robocasa365',task_id='pick_place_counter_to_sink',execution_target='robocasa365_sim')
    store.create_run(run);store.transition_run(run_id,RunStatus.RUNNING,event_key='fixture-start')
    producer=RoboCasa365Preview(directory,run_id,attempt,'http://127.0.0.1:9','robot0_agentview_left')
    producer.publish({'episode_id':'ep','sequence':0,'images':{'robot0_agentview_left_image':np.zeros((18,32,3),dtype=np.uint8).tolist()}})
    monkeypatch.setattr(module,'urlopen',lambda *a,**kw:pytest.fail('UI must not contact authenticated Service'))
    server=create_server(store=store,artifact_root=tmp_path,port=0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}';query=urlencode({'run':run_id,'attempt':attempt})
    try:
        with urlopen(base+'/api/station/preview-state?'+query) as response:state=json.load(response)
        assert state['state']=='live' and state['sequence']==1
        with urlopen(base+'/api/station/preview-frame?'+query) as response:
            assert response.headers['Content-Type']=='image/jpeg' and response.read().startswith(b'\xff\xd8')
        producer.finish()
        with urlopen(base+'/api/station/preview-state?'+query) as response:state=json.load(response)
        assert state['state']=='ended'
    finally:
        server.shutdown();server.server_close();thread.join(2);store.close()


def saved_trace(tmp_path):
    episode=tmp_path/'episode';episode.mkdir()
    refs=[]
    for label,value in [('initial',40),('final',90)]:
        path=episode/f'camera_{label}.png';path.write_bytes(rgb_png(np.full((18,32,3),value,dtype=np.uint8)))
        refs.append(dict(evidence_id=f'evidence:{path.name}',kind='camera_'+label,uri=f'artifact://episode/{path.name}',
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),mime_type='image/png',visibility=['advisor']))
    return dict(trace_id='trace:fixture',attempt_id='attempt:fixture:0',evidence=refs)


def test_current_view_selects_verified_final_image_without_rewriting_history(tmp_path):
    from benchmarks.attention_harness.ui_server import saved_execution_camera
    trace=saved_trace(tmp_path);attempt=dict(attempt_id=trace['attempt_id'],index=0,status='failed')
    camera=saved_execution_camera(tmp_path,[trace],attempt)
    assert camera['camera_url'].endswith('evidence%3Acamera_final.png')
    assert camera['camera_attempt_id']==attempt['attempt_id'] and camera['camera_image_transform']=='flip_y'
    path=tmp_path/'episode/camera_final.png';path.write_bytes(b'tampered')
    assert saved_execution_camera(tmp_path,[trace],attempt)['camera_url'].endswith('evidence%3Acamera_initial.png')
    (tmp_path/'episode/camera_initial.png').unlink()
    assert saved_execution_camera(tmp_path,[trace],attempt) is None


def test_saved_camera_never_borrows_an_earlier_attempt_or_unpublished_image(tmp_path):
    from benchmarks.attention_harness.ui_server import saved_execution_camera
    trace=saved_trace(tmp_path)
    assert saved_execution_camera(tmp_path,[trace],dict(attempt_id='attempt:fixture:1',status='running')) is None
    assert saved_execution_camera(None,[trace],dict(attempt_id=trace['attempt_id'],status='failed')) is None
    trace['evidence'][0]['visibility']=[];trace['evidence'][1]['visibility']=[]
    assert saved_execution_camera(tmp_path,[trace],dict(attempt_id=trace['attempt_id'],status='failed')) is None


def test_preview_uses_the_service_image_suffix_contract(tmp_path):
    from benchmarks.attention_harness.robocasa365.preview import RoboCasa365Preview
    directory,run,attempt=identity(tmp_path)
    producer=RoboCasa365Preview(directory,run,attempt,'http://127.0.0.1:9','robot0_agentview_left')
    producer.publish({'episode_id':'ep','sequence':0,'images':{'robot0_agentview_left_image':np.zeros((128,128,3),dtype=np.uint8).tolist()}})
    assert producer.receipt['frame_count']==1 and producer.receipt['error'] is None
