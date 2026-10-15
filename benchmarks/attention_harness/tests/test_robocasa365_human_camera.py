"""Display-only rendering boundaries; fixture pixels are not real-run evidence."""
import json
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from benchmarks.attention_harness.robocasa365.recording import StepRecorder, finalize_recording


def context():
    import time
    return dict(run_id='run:fixture', attempt_id='attempt:fixture:0',
                camera_name='robot0_agentview_left', started_monotonic=time.monotonic())


def test_native_human_render_matches_robocasa_without_changing_policy_observations(tmp_path):
    from benchmarks.attention_harness.robocasa365.human_camera import capture_human_view
    from benchmarks.attention_harness.robocasa_native.human_camera_service import WIDTH, HEIGHT
    calls = []
    policy = np.zeros((128, 128, 3), dtype=np.uint8)
    pixels = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    pixels[:HEIGHT//2, :, 0] = 255
    def render(**kwargs):
        calls.append(kwargs)
        return pixels
    env = SimpleNamespace(sim=SimpleNamespace(render=render), obs=policy)
    recorder = StepRecorder(tmp_path, context())
    capture_human_view(recorder, env, 'episode', 0)
    capture_human_view(recorder, env, 'episode', 0)
    assert calls == [dict(width=WIDTH, height=HEIGHT,
                          camera_name='robot0_agentview_left', depth=False)]
    assert env.obs is policy and policy.shape == (128, 128, 3)
    row = json.loads((tmp_path/'recording_frames/frames.jsonl').read_text())
    assert (row['width'], row['height']) == (1920, 1080)
    assert row['source'] == 'official_simulator_display_rgb_after_each_control_step'
    with Image.open(tmp_path/'recording_frames/000000.jpg') as image:
        assert image.size == (1920, 1080)
        assert image.getpixel((100, 100))[0] < 10
        assert image.getpixel((100, 1000))[0] > 240


@pytest.mark.parametrize('bad', ['exception', 'wrong_size'])
def test_display_fault_is_recorded_without_falling_back_to_upscaled_policy_pixels(tmp_path, bad):
    from benchmarks.attention_harness.robocasa365.human_camera import capture_human_view
    def render(**kwargs):
        if bad == 'exception':
            raise RuntimeError('render unavailable')
        return np.zeros((128, 128, 3), dtype=np.uint8)
    recorder = StepRecorder(tmp_path, context())
    capture_human_view(recorder, SimpleNamespace(sim=SimpleNamespace(render=render)), 'episode', 0)
    assert recorder.error and recorder.count == 0
    assert (tmp_path/'recording_frames/error.json').exists()
    assert not (tmp_path/'recording_frames/000000.jpg').exists()


def test_hd_finalize_preserves_original_policy_evidence_and_native_media_size(tmp_path, monkeypatch):
    from benchmarks.attention_harness.robocasa365.human_camera import capture_human_view
    import benchmarks.attention_harness.robocasa365.recording as module
    import subprocess
    clock = iter([10.0, 10.2, 10.4])
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(clock))
    rec = StepRecorder(tmp_path, {**context(), 'started_monotonic': 10.0})
    for i in range(2):
        env = SimpleNamespace(sim=SimpleNamespace(render=lambda **kw: np.full((1080,1920,3), 40+i*20, dtype=np.uint8)))
        capture_human_view(rec, env, 'episode', i)
    monkeypatch.undo()
    for label in ('initial','final'):
        Image.new('RGB', (128,128), 'blue').save(tmp_path/f'camera_{label}.png')
    result = finalize_recording(tmp_path, rec.context, 'completed')
    assert result['video_saved'], result['error']
    assert result['source'] == 'official_simulator_display_rgb_after_each_control_step'
    assert (result['source_width'], result['source_height']) == (1920,1080)
    for label in ('initial','final'):
        with Image.open(tmp_path/f'camera_{label}.png') as im:
            assert im.size == (1920,1080)
        with Image.open(tmp_path/f'policy_camera_{label}.png') as im:
            assert im.size == (128,128)
    probe = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','stream=width,height','-of','json',str(tmp_path/'advisor_replay.mp4')]))
    assert (probe['streams'][0]['width'], probe['streams'][0]['height']) == (1920,1080)


@pytest.mark.parametrize('source,transform', [
    ('official_simulator_display_rgb_after_each_control_step', None),
    ('official_simulator_public_rgb_after_each_control_step', 'flip_y')])
def test_saved_camera_orientation_distinguishes_hd_display_from_legacy_raw_rows(tmp_path,source,transform):
    import hashlib
    from benchmarks.attention_harness.ui_server import saved_execution_camera
    episode=tmp_path/'episode';episode.mkdir()
    Image.new('RGB',(2,2),'blue').save(episode/'camera_final.png')
    (episode/'camera_recording.json').write_text(json.dumps({
        'schema_version':'attentionbench.camera-recording.v1','source':source}))
    evidence=[]
    for name,kind,mime in [('camera_final.png','camera_final','image/png'),
                           ('camera_recording.json','camera_recording','application/json')]:
        evidence.append(dict(kind=kind,evidence_id=name,uri='artifact://episode/'+name,
                             sha256=hashlib.sha256((episode/name).read_bytes()).hexdigest(),
                             mime_type=mime,visibility=['advisor']))
    camera=saved_execution_camera(tmp_path,[dict(attempt_id='a',trace_id='t',evidence=evidence)],{'attempt_id':'a'})
    assert camera['camera_image_transform']==transform
