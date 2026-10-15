"""Recording lifecycle contracts; synthetic pixels never presented as real evidence."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from benchmarks.attention_harness.robocasa365.recording import StepRecorder, finalize_recording


def context():
    return dict(run_id='run:fixture', attempt_id='attempt:fixture:0',
                camera_name='robot0_agentview_left', started_monotonic=10.0)


def capture(tmp_path, monkeypatch, count=4):
    import benchmarks.attention_harness.robocasa365.recording as module
    clock=iter(10+i*.12 for i in range(count))
    monkeypatch.setattr(module.time,'monotonic',lambda:next(clock))
    rec=StepRecorder(tmp_path,context())
    for i in range(count):
        raw=np.full((18,32,3),i*30,dtype=np.uint8)
        rec.capture({'robot0_agentview_left_image':raw},'ep',i)
    monkeypatch.undo()
    return rec


def test_each_physical_sequence_is_saved_once_with_native_pixels_and_durable_identity(tmp_path,monkeypatch):
    rec=capture(tmp_path,monkeypatch)
    rec.capture({},'ep',3)
    rows=[json.loads(line) for line in (tmp_path/'recording_frames/frames.jsonl').read_text().splitlines()]
    assert [r['physical_sequence'] for r in rows]==list(range(4))
    assert [r['trace_seconds'] for r in rows]==pytest.approx([0,.12,.24,.36])
    assert all(r['run_id']=='run:fixture' and r['attempt_id']=='attempt:fixture:0' for r in rows)
    assert all(hashlib.sha256((tmp_path/'recording_frames'/r['file']).read_bytes()).hexdigest()==r['sha256'] for r in rows)
    assert rec.error is None


@pytest.mark.parametrize('outcome',['completed','failed','cancelled','timeout'])
def test_finalize_keeps_playable_video_on_all_execution_exits(tmp_path,monkeypatch,outcome):
    capture(tmp_path,monkeypatch)
    result=finalize_recording(tmp_path,context(),outcome)
    assert result['status']=='saved' and result['video_saved']
    assert result['frame_count']==4 and result['sampled_seconds']==pytest.approx(.36)
    assert result['execution_status']==outcome
    assert result['replay_timing']['frame_video_seconds']==pytest.approx([0,.12,.24,.36])
    assert (tmp_path/'advisor_replay.mp4').stat().st_size>0


def test_no_frames_and_single_frame_never_become_fake_execution_videos(tmp_path,monkeypatch):
    assert finalize_recording(tmp_path,context(),'failed')['status']=='missing'
    capture(tmp_path,monkeypatch,1)
    result=finalize_recording(tmp_path,context(),'cancelled')
    assert result['frame_count']==1 and not result['video_saved'] and result['error']
    assert not (tmp_path/'advisor_replay.mp4').exists()


def test_tampered_or_cross_attempt_frames_are_rejected(tmp_path,monkeypatch):
    capture(tmp_path,monkeypatch)
    (tmp_path/'recording_frames/000001.jpg').write_bytes(b'tampered')
    result=finalize_recording(tmp_path,context(),'failed')
    assert result['status']=='failed' and not result['video_saved'] and 'hash' in result['error']


def test_capture_failure_preserves_previous_real_frames_and_does_not_raise(tmp_path,monkeypatch):
    rec=capture(tmp_path,monkeypatch)
    rec.capture({},'ep',4)
    assert rec.error
    result=finalize_recording(tmp_path,context(),'failed')
    assert result['video_saved'] and result['error'] and result['status']=='partial'


def test_process_interruption_during_last_journal_write_keeps_durable_prefix(tmp_path,monkeypatch):
    capture(tmp_path,monkeypatch)
    with (tmp_path/'recording_frames/frames.jsonl').open('a') as stream:
        stream.write('{"run_id":')
    result=finalize_recording(tmp_path,context(),'cancelled')
    assert result['video_saved'] and result['frame_count']==4 and result['status']=='partial'
    assert 'incomplete' in result['error']


def test_realistic_uneven_frame_times_keep_every_decodable_frame_and_terminal_hold(tmp_path,monkeypatch):
    import benchmarks.attention_harness.robocasa365.recording as module
    times=[0,.353497,.489488,.512044,.633122,.656561,.737795,.896409,1.011466]
    clock=iter(10+t for t in times)
    monkeypatch.setattr(module.time,'monotonic',lambda:next(clock))
    rec=StepRecorder(tmp_path,context())
    for i in range(len(times)):
        rec.capture({'robot0_agentview_left_image':np.full((128,128,3),i*20,dtype=np.uint8)},'ep',i)
    monkeypatch.undo()
    result=finalize_recording(tmp_path,context(),'completed')
    assert result['video_saved'],result['error']
    assert len(result['replay_timing']['frame_video_seconds'])==len(times)
    assert result['duration_seconds']>times[-1]+.04
