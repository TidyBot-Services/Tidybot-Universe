"""Replay sidecars: originals are immutable; all media tests run locally on tiny clips."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

import numpy as np
from PIL import Image
import pytest

from benchmarks.attention_harness import replay_trim as trim
from benchmarks.attention_harness.ffmpeg_hold import final_hold


def hashes(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def execution(root, times=(0., .2, 4., 4.2), colors=(20, 20, 180, 220), views=()):
    root.mkdir(parents=True)
    source=root.parent/(root.name+'-frames');source.mkdir()
    for i, (seconds, color) in enumerate(zip(times, colors)):
        path=source/f'{i:06d}.png';Image.new('RGB',(64,36),(color,)*3).save(path)
        import os
        stamp=1_000_000_000_000_000_000+round(seconds*1e9);os.utime(path, ns=(stamp,stamp))
    subprocess.run(['ffmpeg','-nostdin','-v','error','-f','image2','-ts_from_file','2','-framerate','25',
        '-i',str(source/'%06d.png'),'-vsync','vfr','-c:v','libx264','-crf','20','-pix_fmt','yuv420p',
        '-enc_time_base','1:60000','-video_track_timescale','60000',*final_hold(.04),
        str(root/'advisor_replay.mp4')],check=True,capture_output=True)
    shutil.rmtree(source)
    for view in views:shutil.copyfile(root/'advisor_replay.mp4', root/f'advisor_replay_{view}.mp4')
    journal=''.join(json.dumps(dict(physical_sequence=i,trace_seconds=t+10,file=f'{i:06d}.jpg',sha256='0'*64))+'\n'
                    for i,t in enumerate(times))
    (root/'frames.jsonl').write_text(journal)
    (root/'camera_recording.json').write_text(json.dumps(dict(video_saved=True, initial_frame_saved=True,
        frame_count=len(times),frame_manifest_sha256=hashlib.sha256(journal.encode()).hexdigest(),
        replay_timing=dict(frame_trace_seconds=[t+10 for t in times],frame_video_seconds=list(times)))))
    (root/'camera_views.json').write_text(json.dumps(dict(views={v:dict(frame_count=len(times)) for v in views})))
    return root


# NEGATIVE CONTROL FIRST: the builder must never rewrite evidence or its frame source.
def test_negative_control_originals_and_evidence_unchanged(tmp_path):
    from benchmarks.attention_harness.dev_agent_recording import recording_frames
    from benchmarks.attention_harness.episode_trace import _evidence_refs
    source=execution(tmp_path/'source',views=('side',))
    root=tmp_path/'copy';shutil.copytree(source,root)
    options=dict(public_visibility=('advisor',),stdout='',stderr='',context={})
    evidence_before=_evidence_refs(root,**options)
    before=hashes(root)
    trim.build(root)
    after=hashes(root)
    assert {p:h for p,h in after.items() if not p.startswith('replay_trim/')} == before
    assert all(p.startswith('replay_trim/') for p in after.keys()-before.keys())
    assert _evidence_refs(root,**options) == evidence_before
    restored=list(recording_frames(root,20))
    assert len(restored)==8
    assert all(row['data'].startswith(b'\xff\xd8') for row in restored)  # JPEG since 11-08


@pytest.mark.parametrize('times,colors,expected', [
    ((0.,.2,.4),(20,100,180),()),                         # no gaps
    ((0.,3.,6.),(20,100,180),('wait','wait')),             # all gaps
    ((0.,1.,2.,3.),(20,20,20,180),('still',)),             # gap == threshold
    ((0.,),(20,),()),
    ((0.,3.),(20,180),('wait',)),
])
def test_mapping_edges(tmp_path,times,colors,expected):
    root=execution(tmp_path/'episode',times,colors)
    plan=trim.analyze(root)
    frames=plan['frames']
    assert all(b['trim']>a['trim'] for a,b in zip(frames,frames[1:]))
    assert tuple(b['kind'] for b in plan['breaks'])==expected
    for frame in frames:
        # The UI samples the last frame whose trace <= event, just as originals do.
        selected=[f for f in frames if f['trace']<=frame['trace']][-1]
        original=[i for i,t in enumerate(times) if t+10<=frame['trace']][-1]
        assert selected['src_index']==original
        assert selected['trim']==frame['trim']
    # On a skipped interval orig->trim stays at the end of its displayed hold;
    # both adjoining retained spans meet this same boundary, without a jump.
    def orig_to_trim(t):
        frame=[f for f in frames if f['orig']<=t][-1]
        index=frames.index(frame)
        return frame['trim']+min(max(0,t-frame['orig']),plan['durations'][index])
    for gap in plan['breaks']:
        assert orig_to_trim(gap['orig_start'])==pytest.approx(gap['trim_at'])
        assert orig_to_trim(gap['orig_end'])==pytest.approx(gap['trim_at'])
        assert orig_to_trim(gap['orig_start']-1e-6)==pytest.approx(gap['trim_at'],abs=2e-6)
        assert orig_to_trim(gap['orig_end']+1e-6)==pytest.approx(gap['trim_at'],abs=2e-6)
        assert gap['trace_start']==pytest.approx(10+gap['orig_start'])


def test_compare_last_kept_and_minimum_duration(tmp_path):
    root=execution(tmp_path/'episode',(0.,.02,.04,.06,.08),(20,26,32,38,44))
    plan=trim.analyze(root)
    assert [f['src_index'] for f in plan['frames']]==[0,2,4]
    assert min(plan['durations'])>=1/60
    # Duplicate sample clocks are allowed, but the output still has positive DTS.
    meta=json.loads((root/'camera_recording.json').read_text())
    meta['replay_timing']['frame_video_seconds']=[0.,0.,.04,.04,.08]
    (root/'camera_recording.json').write_text(json.dumps(meta))
    assert min(trim.analyze(root)['durations'])>=1/60


@pytest.mark.parametrize('change', ['unequal','decreasing','nan','missing','unsaved'])
def test_invalid_timing_refused(tmp_path,change):
    root=execution(tmp_path/'episode')
    path=root/'camera_recording.json';meta=json.loads(path.read_text())
    if change=='unequal':meta['replay_timing']['frame_trace_seconds'].pop()
    if change=='decreasing':meta['replay_timing']['frame_video_seconds'][2]=-1
    if change=='nan':meta['replay_timing']['frame_video_seconds'][2]=float('nan')
    if change=='missing':del meta['replay_timing']
    if change=='unsaved':meta['video_saved']=False
    path.write_text(json.dumps(meta))
    with pytest.raises(ValueError):trim.analyze(root)
    assert not (root/'replay_trim').exists()


def packets(path):
    raw=subprocess.run(['ffprobe','-v','error','-count_packets','-show_packets','-show_streams','-of','json',str(path)],
                       capture_output=True,check=True).stdout
    return json.loads(raw)


def test_build_duration_packets_dts_and_event_alignment(tmp_path):
    root=execution(tmp_path/'episode',views=('side','top'))
    plan=trim.analyze(root);value=trim.build(root)
    expected=sum(plan['durations']);count=len(value['frames'])
    original=trim._gray(root/'advisor_replay.mp4',trim.PARAMS)
    for view in ('overview','side','top'):
        path=root/value['videos'][view]['file'];probe=packets(path)
        assert int(probe['streams'][0]['nb_read_packets'])==count
        assert float(probe['streams'][0]['duration'])==pytest.approx(expected,abs=1/30)
        dts=[float(p['dts_time']) for p in probe['packets']]
        assert all(b>a for a,b in zip(dts,dts[1:]))
        pts=sorted(float(p['pts_time']) for p in probe['packets'])
        decoded=trim._gray(path,trim.PARAMS)
        assert len(decoded)==count
        for i,frame in enumerate(value['frames']):
            assert pts[i]==pytest.approx(frame['trim'],abs=1/60000)
            assert np.abs(decoded[i].astype(int)-original[frame['src_index']]).mean()<2
    assert trim.load_verified(root)==value


def test_terminal_hold_and_count_mismatch(tmp_path):
    root=execution(tmp_path/'episode')
    meta=json.loads((root/'camera_recording.json').read_text())
    for array in meta['replay_timing'].values():array.pop()
    meta['frame_count']-=1
    (root/'camera_recording.json').write_text(json.dumps(meta))
    plan=trim.analyze(root)
    assert plan['source']['frame_count']==4
    assert plan['frames'][-1]['orig']==pytest.approx(plan['original_duration_seconds'])
    assert plan['frames'][-1]['trace']==pytest.approx(10+plan['original_duration_seconds'])
    for array in meta['replay_timing'].values():array.pop()
    (root/'camera_recording.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError,match='count_mismatch'):trim.analyze(root)


def test_view_mismatch_and_idempotence(tmp_path,monkeypatch):
    root=execution(tmp_path/'episode',views=('side',))
    other=execution(tmp_path/'other',(0.,.2),(20,100))
    shutil.copyfile(other/'advisor_replay.mp4',root/'advisor_replay_top.mp4')
    result=trim.build(root)
    assert set(result['videos'])=={'overview','side'}
    assert result['skipped_views']['top']=='recording_frame_count_mismatch'
    before=hashes(root)
    monkeypatch.setattr(trim,'analyze',lambda *a,**kw:pytest.fail('idempotent build re-analyzed'))
    monkeypatch.setattr(trim,'_encode',lambda *a,**kw:pytest.fail('idempotent build re-encoded'))
    assert trim.build(root)==result
    assert hashes(root)==before


def projection_fixture(tmp_path,monkeypatch):
    from benchmarks.attention_harness import dev_agent_ui_projection as projection
    cell=dict(artifact_dir=str(tmp_path),key='fixture',policy_id='fixture')
    episode=execution(tmp_path/'agent/private/executions/e000002/openarm365-dev-fixture',views=('side',))
    monkeypatch.setattr(projection,'public',lambda cell,name,default=None:default)
    version=dict(version=1,execution_id='e000002')
    return projection,cell,version,episode


def test_projection_public_shape_and_range_path(tmp_path,monkeypatch):
    projection,cell,version,root=projection_fixture(tmp_path,monkeypatch)
    original,assets=projection.version_evidence(cell,version)
    assert 'trim' not in original['recording']
    value=trim.build(root)
    row,new_assets=projection.version_evidence(cell,version)
    public=row['recording'].pop('trim')
    assert row['recording']==original['recording']
    assert row['replays']==original['replays']
    assert projection.recording(cell,'e000002')==projection.recording(cell,'e000002')  # stays a 3-tuple
    assert len(projection.recording(cell,'e000002'))==3
    assert set(public)=={'schema','params','original_duration_seconds','trimmed_duration_seconds','skipped_seconds','replay_timing','breaks','videos'}
    assert public['params']==dict(gap_s=1.,hold_s=1.)
    timing=public['replay_timing']
    assert timing==dict(frame_trace_seconds=[round(f['trace'],3) for f in value['frames']],
                       frame_video_seconds=[round(f['trim'],3) for f in value['frames']],
                       frame_orig_video_seconds=[round(f['orig'],3) for f in value['frames']])
    uri=public['videos']['overview']['uri']
    assert uri==f"agent-view/replays/e000002/{value['videos']['overview']['sha256']}/trim/overview.mp4"
    path,mime=projection.evidence(cell,uri.removeprefix('agent-view/'))
    assert path==root/'replay_trim/overview.mp4' and mime=='video/mp4'
    assert projection.file_digest(path)==value['videos']['overview']['sha256']
    with pytest.raises(FileNotFoundError):projection.evidence(cell,'replays/e000002/'+'0'*64+'/trim/overview.mp4')
    for filename in ('trim/../overview.mp4','trim/evil.mp4','replay_trim/overview.mp4'):
        with pytest.raises(FileNotFoundError):projection.episode_path(cell,'e000002',filename)
    assert assets['camera_views.json']==new_assets['camera_views.json']


@pytest.mark.parametrize('tamper',['segments','source','manifest','trim','video_symlink','directory_symlink','execution_symlink'])
def test_tamper_falls_back_and_rejects_serving(tmp_path,monkeypatch,tamper):
    projection,cell,version,root=projection_fixture(tmp_path,monkeypatch)
    baseline=projection.version_evidence(cell,version)[0]
    trim.build(root)
    public=projection.trim_for(cell,'e000002');uri=public['videos']['overview']['uri'].removeprefix('agent-view/')
    if tamper in ('segments','source','trim'):
        path=root/({'segments':'replay_trim/segments.json','source':'advisor_replay.mp4','trim':'replay_trim/overview.mp4'}[tamper])
        data=bytearray(path.read_bytes());data[0]^=1;path.write_bytes(data)
    elif tamper=='manifest':
        path=root/'camera_recording.json';value=json.loads(path.read_text());value['frame_manifest_sha256']='f'*64;path.write_text(json.dumps(value))
    else:
        path=root/'replay_trim/overview.mp4' if tamper=='video_symlink' else root/'replay_trim' if tamper=='directory_symlink' else root
        moved=tmp_path/('moved-'+tamper);path.rename(moved);path.symlink_to(moved)
    assert trim.load_verified(root) is None
    assert projection.trim_for(cell,'e000002') is None
    # An execution-directory symlink is rejected by today's original reader too.
    if tamper!='execution_symlink':
        row=projection.version_evidence(cell,version)[0]
        assert 'trim' not in row['recording']
        if tamper not in ('source','manifest'):assert row==baseline
    with pytest.raises(FileNotFoundError):projection.evidence(cell,uri)


def seed_run(root,name,state='completed',job_state='settled',agent_job=None):
    run=root/name;run.mkdir(parents=True)
    with sqlite3.connect(run/'ledger.sqlite3') as db:
        db.execute('CREATE TABLE v2_experiments (payload TEXT)')
        db.execute('INSERT INTO v2_experiments VALUES (?)',(json.dumps(dict(state=state)),))
        db.execute('CREATE TABLE v2_jobs (state TEXT)');db.execute('INSERT INTO v2_jobs VALUES (?)',(job_state,))
        if agent_job:
            db.execute('CREATE TABLE v2_agent_jobs (state TEXT)');db.execute('INSERT INTO v2_agent_jobs VALUES (?)',(agent_job,))
    episode=execution(run/'artifacts/run/cells/cell/agent/private/executions/e000002/openarm365-dev-fixture')
    return episode


def test_backfill_dry_run_execute_skip_force_and_disk_guard(tmp_path,capsys,monkeypatch):
    root=tmp_path/'runs';ready=seed_run(root,'devexp-ready')
    seed_run(root,'devexp-running',state='running')
    seed_run(root,'devexp-claimed',job_state='claimed')
    seed_run(root,'devexp-agent-review',agent_job='needs_review')
    done=seed_run(root,'devexp-done');trim.build(done)
    report=tmp_path/'report.json';before=hashes(root)
    assert trim.main(['backfill','--root',str(root),'--report',str(report)])==0
    assert hashes(root)==before
    data=json.loads(report.read_text());rows={r['run']:r for r in data['executions']}
    assert rows['devexp-ready']['status']=='planned'
    assert rows['devexp-ready']['predicted_extra_bytes']=='unknown'
    assert rows['devexp-done']['reason']=='valid_sidecar_already_present'
    assert all(rows['devexp-'+n]['reason']=='run_not_terminal_or_jobs_active' for n in ('running','claimed','agent-review'))
    assert data['totals']['planned']==1 and data['totals']['skipped']==4
    monkeypatch.setattr(trim.shutil,'disk_usage',lambda path:shutil._ntuple_diskusage(10**12,0,10**12))
    assert trim.main(['backfill','--root',str(root),'--run','devexp-ready','--execute'])==0
    after=hashes(root)
    assert all('/replay_trim/' in p for p in after.keys()-before.keys())
    assert {p:after[p] for p in before}==before
    assert trim.load_verified(ready)
    assert trim.main(['backfill','--root',str(root),'--run','devexp-ready','--force','--dry-run'])==0
    assert '"status": "planned"' in capsys.readouterr().out
    before=hashes(root)
    monkeypatch.setattr(trim.shutil,'disk_usage',lambda path:shutil._ntuple_diskusage(1,1,0))
    assert trim.main(['backfill','--root',str(root),'--run','devexp-ready','--force','--execute'])==2
    assert hashes(root)==before
    assert 'disk_guard' in capsys.readouterr().out
    with pytest.raises(SystemExit):trim.main(['backfill','--root',str(root),'--report',str(root/'report.json')])


def test_backfill_records_bad_sources_and_frame_count(tmp_path,capsys):
    root=tmp_path/'runs'
    bad=seed_run(root,'bad-timing');path=bad/'camera_recording.json';meta=json.loads(path.read_text());del meta['replay_timing'];path.write_text(json.dumps(meta))
    bad=seed_run(root,'bad-source');(bad/'advisor_replay.mp4').unlink()
    bad=seed_run(root,'bad-count');path=bad/'camera_recording.json';meta=json.loads(path.read_text())
    for array in meta['replay_timing'].values():del array[2:]
    path.write_text(json.dumps(meta))
    before=hashes(root)
    assert trim.main(['backfill','--root',str(root)])==0
    assert hashes(root)==before
    output=capsys.readouterr().out
    assert 'invalid_replay_timing' in output and 'count_mismatch' in output and 'missing_or_empty' in output


def test_hook_disabled_raising_and_hard_timeout(tmp_path,monkeypatch):
    logs=[];monkeypatch.setenv('ATTENTIONBENCH_REPLAY_TRIM','0')
    monkeypatch.setattr(trim.subprocess,'Popen',lambda *a,**kw:pytest.fail('disabled hook spawned'))
    trim.build_safe(tmp_path,logs.append);assert not logs
    monkeypatch.setenv('ATTENTIONBENCH_REPLAY_TRIM','1')
    def fail(*a,**kw):raise RuntimeError('stub failure')
    monkeypatch.setattr(trim.subprocess,'Popen',fail)
    trim.build_safe(tmp_path,logs.append);assert 'stub failure' in logs[-1]
    monkeypatch.undo()
    monkeypatch.setattr(trim,'HOOK_TIMEOUT',.1)
    popen=subprocess.Popen;processes=[]
    def slow(*a,**kw):
        process=popen([__import__('sys').executable,'-c','import time; time.sleep(20)'],**kw)
        processes.append(process);return process
    monkeypatch.setattr(trim.subprocess,'Popen',slow)
    start=time.monotonic();trim.build_safe(tmp_path,logs.append)
    assert time.monotonic()-start<2
    assert processes[0].poll() is not None and 'TimeoutExpired' in logs[-1]


@pytest.mark.parametrize('fault',['raise','slow'])
def test_agent_hook_failure_preserves_settled_result_and_ledger(tmp_path,monkeypatch,fault):
    from benchmarks.attention_harness.dev_agent_workspace import Workspace,atomic_write
    from benchmarks.attention_harness.dev_agent_ledger import AgentLedger
    from benchmarks.attention_harness.dev_agent_loop import drive
    from benchmarks.attention_harness.dev_agent_adapters import FakeProber
    from benchmarks.attention_harness.tests.test_dev_agent_d2 import CONFIG,SOURCE,Executor,fake_session
    ws=Workspace(tmp_path/'agent').prepare(dict(task_prompt='Offline fixture.'),CONFIG)
    atomic_write(ws.ws,'work/main.py',SOURCE)
    ledger=AgentLedger(ws.private/'ledger.sqlite3','fixture')
    class Recorded(Executor):
        def execute(self,source,directory,**kw):
            result=super().execute(source,directory,**kw)
            self.episode=directory/'openarm365-dev-fixture';self.episode.mkdir()
            return result
    executor=Recorded((1,));called=[]
    if fault=='raise':
        def failing(episode,log):
            rows=ledger.jobs();request=next(j for j in rows if j['id']=='request:fixture-submit')
            assert request['state']=='settled'
            called.append(request)
            raise RuntimeError('stub trim failure')
        monkeypatch.setattr(trim,'build_safe',failing)
    else:
        popen=subprocess.Popen
        def slow(*a,**kw):
            request=next(j for j in ledger.jobs() if j['id']=='request:fixture-submit')
            assert request['state']=='settled';called.append(request)
            return popen([__import__('sys').executable,'-c','import time; time.sleep(20)'],**kw)
        monkeypatch.setattr(trim.subprocess,'Popen',slow)
        # subprocess is a shared module: only redirect the trim command.
        def only_trim(args,*a,**kw):
            return slow(args,*a,**kw) if 'benchmarks.attention_harness.replay_trim' in args else popen(args,*a,**kw)
        monkeypatch.setattr(trim.subprocess,'Popen',only_trim)
        monkeypatch.setattr(trim,'HOOK_TIMEOUT',.1)
    session=fake_session(ws,["tool('submit','--request-id','fixture-submit'); time.sleep(5)"])
    start=time.monotonic()
    result=drive(ws,session,ledger,executor=executor,prober=FakeProber())
    assert time.monotonic()-start<5
    assert result['state']=='completed' and result['reason']=='native_success'
    assert len(called)==1
    request=next(j for j in ledger.jobs() if j['id']=='request:fixture-submit')
    assert request==called[0]
    assert request['receipt']['status']=='completed'
    assert json.loads((ws.private/'versions/v0001.json').read_text())['native_success'] is True
    assert not (ws.private/'settlement-error.json').exists()


def test_irregular_vfr_duration_and_terminal_hold(tmp_path):
    times=(0.,.63,.70,.78,.85,5.8,5.87,5.94,6.02,6.17,12.2,12.27,12.34)
    root=execution(tmp_path/'episode',times,tuple(20+(i%6)*32 for i in range(len(times))))
    meta=json.loads((root/'camera_recording.json').read_text())
    for array in meta['replay_timing'].values():array.pop()
    (root/'camera_recording.json').write_text(json.dumps(meta))
    result=trim.build(root);probe=packets(root/'replay_trim/overview.mp4')
    assert int(probe['streams'][0]['nb_read_packets'])==len(result['frames'])
    assert float(probe['streams'][0]['duration'])==pytest.approx(result['trimmed_duration_seconds'],abs=1/30)
    assert sorted(float(p['pts_time']) for p in probe['packets'])==pytest.approx([f['trim'] for f in result['frames']],abs=1/60000)


def test_backfill_pending_wal_never_opens_writable_sqlite(tmp_path,capsys):
    root=tmp_path/'runs';episode=seed_run(root,'pending-wal')
    (root/'pending-wal/ledger.sqlite3-wal').write_bytes(b'pending')
    before=hashes(root)
    assert trim.main(['backfill','--root',str(root)])==0
    assert hashes(root)==before
    assert 'ledger_has_pending_wal' in capsys.readouterr().out


def test_dozens_of_frames_sampled_events_and_encoder_stderr(tmp_path,monkeypatch):
    import bisect
    times=tuple(i*.08+(3 if i>=12 else 0)+(4 if i>=24 else 0) for i in range(36))
    colors=tuple(20+((i//2)%6)*35 for i in range(36))
    root=execution(tmp_path/'episode',times,colors)
    run=subprocess.run;errors=[]
    def observe(args,**kw):
        result=run(args,**kw)
        if '-c:v' in args:errors.append(result.stderr)
        return result
    monkeypatch.setattr(trim.subprocess,'run',observe)
    value=trim.build(root)
    assert errors==[b'']
    original=trim._gray(root/'advisor_replay.mp4',trim.PARAMS)
    decoded=trim._gray(root/'replay_trim/overview.mp4',trim.PARAMS)
    frames=value['frames'];traces=[f['trace'] for f in frames]
    pts=sorted(float(p['pts_time']) for p in packets(root/'replay_trim/overview.mp4')['packets'])
    for event in (t+10 for t in times):
        position=bisect.bisect_right(traces,event)-1
        original_position=bisect.bisect_right([t+10 for t in times],event)-1
        selected=bisect.bisect_right(pts,frames[position]['trim']+1/60000)-1
        assert selected==position
        assert np.abs(decoded[selected].astype(int)-original[original_position]).mean()<2


def test_trace_interpolation_and_unknown_video_end(tmp_path,monkeypatch):
    root=execution(tmp_path/'episode',(0.,.2,4.,4.2),(20,20,180,220))
    path=root/'camera_recording.json';meta=json.loads(path.read_text())
    meta['replay_timing']['frame_trace_seconds']=[10.,11.,20.,22.]
    path.write_text(json.dumps(meta))
    value=trim.analyze(root);gap=value['breaks'][0]
    assert gap['trace_start']==pytest.approx(11+(gap['orig_start']-.2)*9/3.8)
    assert gap['trace_end']==20.
    probe=trim._probe
    monkeypatch.setattr(trim,'_probe',lambda p:(probe(p)[0],0.))
    value=trim.analyze(root)
    assert value['original_duration_seconds']==4.2
    assert value['durations'][-1]==pytest.approx(1/30)


@pytest.mark.parametrize('offered,expected',[
    (b'  -ts <string>\n  -duration <string>\n',['-bf','0','-bsf:v','setts=duration=0.500000000/TB']),
    (b'  -ts <string>\n  -pts <string>\n',['-r','2.000000000'])])
def test_final_hold_uses_what_this_ffmpeg_offers(monkeypatch,offered,expected):
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(a,0,stdout=offered))
    assert final_hold(.5)==expected


def test_setup_media_check_passes_on_this_ffmpeg(tmp_path):
    root=Path(__file__).resolve().parents[3]
    done=subprocess.run([sys.executable,str(root/'scripts/attentionbench/media_check.py'),str(tmp_path)],
                        env=os.environ|{'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=60)
    assert done.returncode==0,done.stdout+done.stderr


def test_commit_marker_not_published_when_encoding_fails(tmp_path,monkeypatch):
    root=execution(tmp_path/'episode',views=('side',));before=hashes(root)
    encode=trim._encode
    def fail_second(source,*a):
        if source.name=='advisor_replay_side.mp4':raise RuntimeError('stub encoding failure')
        return encode(source,*a)
    monkeypatch.setattr(trim,'_encode',fail_second)
    with pytest.raises(RuntimeError,match='stub encoding failure'):trim.build(root)
    assert not (root/'replay_trim/segments.json').exists()
    assert trim.load_verified(root) is None
    assert hashes(root)==before


def test_force_rebuild_and_source_symlink_refusal(tmp_path,monkeypatch):
    root=execution(tmp_path/'episode');value=trim.build(root)
    count=[];encode=trim._encode
    def record(*a):count.append(1);return encode(*a)
    monkeypatch.setattr(trim,'_encode',record)
    assert trim.build(root,force=True)==value and len(count)==1
    source=root/'advisor_replay.mp4';source.rename(tmp_path/'original.mp4');source.symlink_to(tmp_path/'original.mp4')
    assert trim.load_verified(root) is None
    with pytest.raises(ValueError,match='symlink'):trim.analyze(root)


def test_malformed_marker_shapes_fail_closed(tmp_path):
    root=execution(tmp_path/'episode');value=trim.build(root);path=root/'replay_trim/segments.json'
    for key,bad in (('params',list(trim.PARAMS)),('videos',[]),('source',[]),('frames',{}),('breaks',{}),('skipped_views',[])):
        path.write_text(json.dumps({**value,key:bad}))
        assert trim.load_verified(root) is None
        assert trim.public_trim(root) is None


def test_terminal_projection_cache_sees_a_sidecar_published_later(tmp_path,monkeypatch):
    """A cached terminal view must refresh once replay_trim/segments.json appears (backfill after the run ended)."""
    from types import SimpleNamespace
    from benchmarks.attention_harness import dev_agent_view
    store=SimpleNamespace(path=tmp_path/'ledger.sqlite3');store.path.write_bytes(b'x')
    cell={'state':sorted(dev_agent_view.TERMINAL)[0],'key':'k','artifact_dir':str(tmp_path/'cell')}
    episode=tmp_path/'cell/agent/private/executions/e000001/openarm365-dev-aaaa';episode.mkdir(parents=True)
    calls=[];monkeypatch.setattr(dev_agent_view,'_projection',lambda *a,**k:calls.append(1) or {'n':len(calls)})
    dev_agent_view._CACHE.clear()
    assert dev_agent_view.projection(store,cell)=={'n':1} and dev_agent_view.projection(store,cell)=={'n':1}
    (episode/'replay_trim').mkdir();(episode/'replay_trim/segments.json').write_text('{}')
    assert dev_agent_view.projection(store,cell)=={'n':2},'publishing a sidecar must invalidate the cached view'
    dev_agent_view._CACHE.clear()


def test_hook_skips_an_execution_the_recorder_kept_no_video_for(tmp_path,monkeypatch):
    # 11-11 (Cora's run, e000007): base move refused at once -> 1 frame -> video_saved false. Not a trim failure.
    (tmp_path/'camera_recording.json').write_text(json.dumps({'video_saved':False,'error':'ValueError: Fewer than two real control-step frames were saved'}))
    logs=[];monkeypatch.setattr(trim.subprocess,'Popen',lambda *a,**kw:pytest.fail('spawned a builder for an execution with no video'))
    trim.build_safe(tmp_path,logs.append);assert not logs
    # A receipt that does not say so (missing, unreadable, or video_saved true) still goes to the builder.
    def fail(*a,**kw):raise RuntimeError('builder reached')
    monkeypatch.setattr(trim.subprocess,'Popen',fail)
    for text in (None,'not json',json.dumps({'video_saved':True})):
        receipt=tmp_path/'camera_recording.json'
        if text is None:receipt.unlink(missing_ok=True)
        else:receipt.write_text(text)
        trim.build_safe(tmp_path,logs.append);assert 'builder reached' in logs[-1]
