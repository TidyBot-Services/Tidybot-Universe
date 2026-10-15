"""Isolated preview regressions; no simulator, real run or robot commands."""
import io
import json
import threading
import time
from dataclasses import replace
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pytest
from PIL import Image

from benchmarks.attention_harness.core.models import RunStatus
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.public_station import publish_public_station
from benchmarks.attention_harness.tests.test_attention_store import records
from benchmarks.attention_harness.ui_server import create_server


def setup_run(tmp_path):
    store = AttentionStore(tmp_path / 'fixture.sqlite3')
    run, _, _, _ = records()
    name = 'attention-robosuite-preview-fixture'
    run = replace(run, run_id='run:' + name, execution_target='robosuite_sim')
    store.create_run(run)
    store.transition_run(run.run_id, RunStatus.RUNNING, event_key='fixture-start')
    attempt_id = 'attempt:' + name + ':0'
    run_dir = tmp_path / name
    publish_public_station(run_dir=run_dir, run_id=run.run_id, attempt_id=attempt_id,
                           suite='robosuite', origin='http://127.0.0.1:9', camera_name='agentview')
    return store, run, attempt_id, run_dir


def start_server(store, tmp_path):
    server = create_server(store=store, artifact_root=tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f'http://127.0.0.1:{server.server_port}'


def test_preview_metadata_endpoint_exists_without_contacting_service(tmp_path, monkeypatch):
    store, run, attempt_id, _ = setup_run(tmp_path)
    import benchmarks.attention_harness.ui_server as module
    monkeypatch.setattr(module, 'urlopen', lambda *a, **k: pytest.fail('preview must not pull Service'))
    server, thread, base = start_server(store, tmp_path)
    try:
        query = urlencode({'run': run.run_id, 'attempt': attempt_id})
        with urlopen(base + '/api/station/preview-state?' + query) as response:
            value = json.load(response)
        assert value['state'] == 'unavailable'
        assert value['age_seconds'] is None
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


def test_atomic_packet_identity_integrity_and_real_age(tmp_path):
    from benchmarks.attention_harness.live_preview import save_preview, read_preview, finish_preview
    from benchmarks.attention_harness.ui_media import rgb_jpeg
    _, run, attempt_id, run_dir = setup_run(tmp_path)
    frame = rgb_jpeg(np.full((18, 32, 3), 90, dtype=np.uint8))
    captured_at = time.time() - 8.5
    save_preview(run_dir, run_id=run.run_id, attempt_id=attempt_id,
                 frame=frame, sequence=3, captured_at=captured_at, width=32, height=18)
    header, actual = read_preview(run_dir, run.run_id, attempt_id)
    assert actual == frame and header['captured_at'] == captured_at
    assert read_preview(run_dir, run.run_id, attempt_id[:-1] + '1') is None
    finish_preview(run_dir, run.run_id, attempt_id)
    header, _ = read_preview(run_dir, run.run_id, attempt_id)
    assert header['ended_at'] >= captured_at and header['captured_at'] == captured_at
    assert header['sequence'] == 3
    path = run_dir / 'public_preview-0.frame'
    original = path.read_bytes(); path.write_bytes(original[:-3] + b'bad')
    assert read_preview(run_dir, run.run_id, attempt_id) is None
    path.unlink(); other = tmp_path / 'other'; other.write_bytes(original); path.symlink_to(other)
    assert read_preview(run_dir, run.run_id, attempt_id) is None
    with pytest.raises(ValueError):
        save_preview(run_dir, run_id='run:../escape', attempt_id=attempt_id,
                     frame=frame, sequence=3, captured_at=captured_at, width=32, height=18)


def test_native_fhd_jpeg_avoids_png_transport_without_resize():
    from benchmarks.attention_harness.ui_media import rgb_jpeg, rgb_png
    # Smooth synthetic RGB with texture, matching an actual camera payload shape.
    rng = np.random.default_rng(4)
    rgb = rng.integers(80, 120, size=(1080, 1920, 3), dtype=np.uint8)
    jpeg = rgb_jpeg(rgb)
    assert Image.open(io.BytesIO(jpeg)).size == (1920, 1080)
    assert len(jpeg) < len(rgb_png(rgb)) / 2
    with pytest.raises(ValueError): rgb_jpeg(np.zeros((8, 8), dtype=np.uint8))


def test_cached_rgb_source_has_exact_assignment_clock_and_never_fetches_service(monkeypatch):
    from benchmarks.attention_harness.robosuite_memory.adapter import RobosuiteSimGTBackend
    backend = object.__new__(RobosuiteSimGTBackend)
    backend.camera_name = 'agentview'
    backend._client = None  # Any Service read would fail.
    rgb = np.full((8, 8, 3), 90, dtype=np.uint8)
    import benchmarks.attention_harness.robosuite_memory.adapter as module
    monkeypatch.setattr(module.time, 'time', lambda: 123.5)
    backend._last_observation = {'agentview_image': rgb, 'agentview_depth': 'private'}
    image, captured_at, sequence = backend.public_rgb_snapshot()
    assert image.shape == (8, 8, 3) and captured_at == 123.5
    image[:] = 0; assert rgb[0, 0, 0] == 90
    monkeypatch.setattr(module.time, 'time', lambda: 140)
    assert backend.public_rgb_snapshot()[1:] == (captured_at, sequence)
    backend._last_observation = {'agentview_image': rgb}
    assert backend.public_rgb_snapshot()[1:] == (140, sequence + 1)


def test_preview_survives_recording_cap_and_finishes_with_original_capture_time(tmp_path, monkeypatch):
    from benchmarks.attention_harness.ui_media import AttemptCameraRecording
    from benchmarks.attention_harness.live_preview import read_preview
    _, run, attempt_id, run_dir = setup_run(tmp_path)
    source = [np.full((18, 32, 3), 90, dtype=np.uint8), time.time() - 3, 1]
    capture = AttemptCameraRecording(run_dir / 'attempts' / 'fixture', lambda: pytest.fail('no Service PNG pull'),
            run_dir=run_dir, run_id=run.run_id, attempt_id=attempt_id,
            fetch_rgb=lambda: tuple(source), defer_encoding=True)
    capture.recorder.max_frames = 1
    capture.recorder.fps = 50
    with capture:
        deadline = time.monotonic() + 2
        while len(capture.recorder.frames) < 1 and time.monotonic() < deadline: time.sleep(.01)
        source[:] = [np.full((18, 32, 3), 180, dtype=np.uint8), time.time(), 2]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            packet = read_preview(run_dir, run.run_id, attempt_id)
            if packet and packet[0]['sequence'] == 2: break
            time.sleep(.01)
        else: pytest.fail('preview stopped at recording cap')
        assert len(capture.recorder.frames) == 1
    packet = read_preview(run_dir, run.run_id, attempt_id)
    assert packet[0]['captured_at'] == source[1] and packet[0]['ended_at']
    assert not capture.recorder._thread.is_alive()
    assert capture.recorder.truncated


def test_sequential_frame_endpoint_returns_latest_and_skips_unchanged_without_service_calls(tmp_path, monkeypatch):
    from benchmarks.attention_harness.live_preview import save_preview, finish_preview
    from benchmarks.attention_harness.ui_media import rgb_jpeg
    store, run, attempt_id, run_dir = setup_run(tmp_path)
    frame = rgb_jpeg(np.full((18,32,3),90,dtype=np.uint8))
    def publish(sequence):
        save_preview(run_dir,run_id=run.run_id,attempt_id=attempt_id,frame=frame,
                     sequence=sequence,captured_at=time.time()-8.5,width=32,height=18)
    publish(1)
    import benchmarks.attention_harness.ui_server as module
    monkeypatch.setattr(module,'urlopen',lambda *a,**k:pytest.fail('preview must not pull Service'))
    server,thread,base=start_server(store,tmp_path)
    query=urlencode({'run':run.run_id,'attempt':attempt_id})
    def state():
        with urlopen(base+'/api/station/preview-state?'+query) as response: return json.load(response)
    try:
        assert state()['state']=='stale' and state()['age_seconds']>=8.5
        with urlopen(base+'/api/station/preview-frame?'+query) as response:
            assert response.read()==frame
            assert response.headers['X-Frame-Sequence']=='1'
            assert float(response.headers['X-Frame-Age'])>=8.5
        with urlopen(base+'/api/station/preview-frame?'+query+'&after=1') as response:
            assert response.status==204 and response.read()==b''
        publish(2);publish(3);publish(4)
        with urlopen(base+'/api/station/preview-frame?'+query+'&after=1') as response:
            assert response.read()==frame and response.headers['X-Frame-Sequence']=='4'
        finish_preview(run_dir,run.run_id,attempt_id)
        assert state()['state']=='ended'
        with urlopen(base+'/api/station/preview-frame?'+query) as response:
            assert response.read()==frame and response.headers['X-Preview-State']=='ended'
        with pytest.raises(HTTPError) as error:
            urlopen(base+'/api/station/preview-frame?'+urlencode({'run':run.run_id,'attempt':attempt_id[:-1]+'1'}))
        assert error.value.code==409
    finally:
        server.shutdown();server.server_close();thread.join(2)
