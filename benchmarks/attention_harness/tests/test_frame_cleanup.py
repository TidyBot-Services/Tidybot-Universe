"""Only temporary synthetic runs; one real ffprobe/ffmpeg integration check."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from unittest.mock import patch

import pytest
from benchmarks.attention_harness import frame_cleanup as cleanup
from benchmarks.attention_harness.dev_agent_replay import freeze_origin, provision_replay
from benchmarks.attention_harness.dev_agent_workspace import Workspace
from benchmarks.attention_harness.dev_agent_view import artifact, _link
from benchmarks.attention_harness.home_status import scene_image


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def bind(cell, *, state='completed', reason=None, owner=None, experiment_state='completed'):
    exp = cell.parent.parent; ledger = exp/'ledger.sqlite3'
    write(exp/'manifest.json', dict(ledger_path=str(ledger)))
    c = dict(key=cell.name, artifact_dir=str(cell), experiment_id=exp.name, state=state, reason=reason, owner=owner)
    with sqlite3.connect(ledger) as db:
        db.execute('CREATE TABLE IF NOT EXISTS v2_cells(payload TEXT)'); db.execute('DELETE FROM v2_cells')
        db.execute('INSERT INTO v2_cells VALUES (?)',(json.dumps(c),))
        db.execute('CREATE TABLE IF NOT EXISTS v2_experiments(payload TEXT)'); db.execute('DELETE FROM v2_experiments')
        db.execute('INSERT INTO v2_experiments VALUES (?)',(json.dumps(dict(experiment_id=exp.name,state=experiment_state,owner=None)),))


@pytest.fixture
def run(tmp_path, monkeypatch):
    cell = tmp_path/('devexp-'+'a'*24)/'cells'/('b'*64); cell.mkdir(parents=True)
    bind(cell)
    episode = cell/'v0001/executions/openarm365-dev-test'
    rows = [dict(file=f'{i:06d}.jpg', physical_sequence=i,trace_seconds=float(i)) for i in range(3)]
    for i, row in enumerate(rows):
        path=episode/'recording_frames'/row['file']; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(bytes([i])*100)
    journal=episode/'recording_frames/frames.jsonl'; journal.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    write(episode/'camera_recording.json',dict(video_saved=True,frame_count=3))
    (episode/'advisor_replay.mp4').write_bytes(b'video')
    (episode/'camera_initial.png').write_bytes(b'cover')
    monkeypatch.setattr(cleanup,'probe_count',lambda path: 4 if path.is_file() else (_ for _ in ()).throw(FileNotFoundError(str(path))))
    return cell, episode


def test_apply_receipt_first_frame_readonly_idempotent(run):
    cell, ep = run
    frame = ep/'recording_frames/000001.jpg'; frame.chmod(0o444)
    p=cleanup.plan(cell); assert p['frames_deleted']==2 and p['bytes_freed']==200
    receipt=cleanup.apply(cell)
    raw=(cell/'frame-cleanup.json').read_bytes()
    assert receipt['schema']==cleanup.SCHEMA and receipt['status']=='completed'
    assert len(receipt['views'][0]['deleted_files'])==2
    assert (ep/'recording_frames/000000.jpg').exists() and (ep/'camera_initial.png').exists()
    assert (ep/'recording_frames/frames.jsonl').exists() and (ep/'advisor_replay.mp4').exists()
    assert cleanup.apply(cell)==receipt and raw==(cell/'frame-cleanup.json').read_bytes()
    row=dict(artifact_dir=str(cell)); assert scene_image(row,cell.parents[2]) is not None


@pytest.mark.parametrize('links',[2,3,4])
def test_inode_aware_all_links_must_be_deleted(run, tmp_path, links):
    cell,ep=run; frame=ep/'recording_frames/000001.jpg'
    for n in range(links-1):os.link(frame,ep/f'recording_frames/extra-{n}.jpg')
    assert cleanup.plan(cell)['bytes_freed']==200
    os.link(frame,tmp_path/'outside.jpg')
    assert cleanup.plan(cell)['bytes_freed']==100


@pytest.mark.parametrize('problem',['missing','corrupt','mismatch'])
def test_video_failures_skip_entire_view(run,monkeypatch,problem):
    cell,ep=run
    if problem=='missing':(ep/'advisor_replay.mp4').unlink()
    elif problem=='corrupt':monkeypatch.setattr(cleanup,'probe_count',lambda p: (_ for _ in ()).throw(ValueError('corrupt')))
    else:monkeypatch.setattr(cleanup,'probe_count',lambda p: 3)
    p=cleanup.plan(cell);assert p['frames_deleted']==0 and p['views'][0]['skipped']
    cleanup.apply(cell); assert (ep/'recording_frames/000001.jpg').exists()


@pytest.mark.parametrize('frame_count', [0, 1])
def test_probe_owner_with_only_kept_frames_has_no_skip_or_notice(tmp_path,frame_count):
    from benchmarks.attention_harness.tests.test_notices import ledger,Bridge
    from benchmarks.attention_harness.notices import NoticeReader
    cell=tmp_path/'experiment/cells/cell';cell.mkdir(parents=True)
    bind(cell)
    probe=cell/'agent/private/probe-owner-0001'
    for view in ('overview','side','top','left_wrist','right_wrist'):
        folder=probe/('recording_frames' if view=='overview' else 'recording_views/'+view)
        folder.mkdir(parents=True)
        rows=[dict(file='000000.jpg')]*frame_count
        (folder/'frames.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
        if frame_count:(folder/'000000.jpg').write_bytes(b'kept first frame')
    with patch.object(cleanup,'_expected',side_effect=AssertionError('unnecessary producer check')), \
         patch.object(cleanup,'probe_count',side_effect=AssertionError('unnecessary video check')):
        planned=cleanup.plan(cell)
        assert len(planned['views'])==5*frame_count
        assert all(g['nothing_to_delete'] and not g.get('skipped') for g in planned['views'])
        receipt=cleanup.apply(cell)
    assert receipt['frames_deleted']==receipt['bytes_freed']==0
    assert all(not g['files'] and not g['already_missing'] for g in receipt['views'])
    assert len(list(probe.rglob('000000.jpg')))==5*frame_count
    assert cleanup.notices(cell)==[]
    notice_dir=tmp_path/'notices';notice_dir.mkdir()
    path=ledger(notice_dir)
    with sqlite3.connect(path) as db:
        row=json.loads(db.execute('SELECT payload FROM v2_cells').fetchone()[0])
        row['artifact_dir']=str(cell)
        db.execute('UPDATE v2_cells SET payload=?',(json.dumps(row),))
    notices=NoticeReader(Bridge(path),probe=lambda:True).read()['notices']
    assert not any(n['title']=='Frame cleanup needs attention' for n in notices)


@pytest.mark.parametrize('status,reason,owner',[('running',None,None),('unknown',None,None),('planned','operator_paused',None),('completed',None,{'pid':os.getpid()})])
def test_active_paused_refused(run,status,reason,owner):
    cell,_=run
    if owner:
        from benchmarks.attention_harness.dev_loop import process_identity
        owner=process_identity()
    bind(cell,state=status,reason=reason,owner=owner)
    with pytest.raises(ValueError,match='terminal, unpaused'):cleanup.apply(cell)


def test_symlink_refused(run,tmp_path):
    cell,ep=run; (ep/'recording_frames/escape.jpg').symlink_to(tmp_path/'outside')
    with pytest.raises(ValueError,match='symlink'):cleanup.plan(cell)
    with pytest.raises(ValueError,match='symlink'):cleanup.apply(cell)


def test_trash_restore_preserves_bytes_inode_links(run,tmp_path):
    cell,ep=run; frame=ep/'recording_frames/000001.jpg'; alias=ep/'recording_frames/alias.jpg'
    os.link(frame,alias); before=frame.stat();raw=frame.read_bytes()
    cleanup.apply(cell,trash_dir=tmp_path/'trash')
    assert not frame.exists()
    assert cleanup.restore(tmp_path/'trash')==1
    assert frame.read_bytes()==raw and frame.stat().st_ino==before.st_ino==alias.stat().st_ino
    assert not (cell/'frame-cleanup.json').exists()


def agent(run):
    cell,old=run
    w=Workspace(cell/'agent')
    rows=[]
    for i in range(3):
        name=f'executions/e000001/frames/overview-{i:06d}-sim-{i:.6f}s.jpg'
        receipt=w.publish(name,bytes([i+5])*100)
        rows.append(dict(filename=name.split('/',2)[2],sha256=receipt['sha256'],view='overview',index=i))
    w.publish('executions/e000001/frames.json',rows)
    w.publish('work-proof.json',{'preserve':True})
    ep=cell/'agent/private/executions/e000001/openarm365-dev-test';ep.mkdir(parents=True)
    write(ep/'camera_recording.json',dict(video_saved=True,frame_count=3))
    (ep/'advisor_replay.mp4').write_bytes(b'video')
    cut=freeze_origin(w,'post-exec-v1')
    return cell,w,rows,ep,cut


def test_agent_copies_snapshots_replay_and_ui(run,tmp_path):
    cell,w,rows,ep,cut=agent(run)
    name='executions/e000001/'+rows[1]['filename']; frame=w.canonical/name
    evalcopy=w.private/'eval/v0001/ws'/rows[1]['filename'];evalcopy.parent.mkdir(parents=True);os.link(frame,evalcopy)
    cleanup.apply(cell)
    assert not frame.exists() and not evalcopy.exists()
    assert not (w.private/'snapshots/objects'/rows[1]['sha256']).exists()
    assert (w.private/'snapshots/objects'/rows[0]['sha256']).exists()
    replay=provision_replay(cut,tmp_path/'replay')
    assert not (replay.ws/name).exists()
    assert (replay.ws/'work-proof.json').exists()
    assert json.loads((replay.private/'replay-origin.json').read_bytes())['frames_cleaned']==2
    view=dict(artifact_dir=str(cell),dev_mode='agent')
    link=_link('/', 'frame',w.canonical,name)
    assert link['label']==cleanup.CLEANED and link['available'] is False
    with pytest.raises(cleanup.CleanedFrame,match='cleaned up'):
        artifact(view,name)
    assert artifact(view,'executions/e000001/'+rows[0]['filename'])[1]=='image/jpeg'
    listing=artifact(view,'executions/e000001/frames.json')[0]
    assert listing[1]['available'] is False and listing[1]['label']==cleanup.CLEANED
    assert 'available' not in listing[0]


def test_failed_view_keeps_agent_ws_public_eval_objects(run,monkeypatch):
    cell,w,rows,ep,cut=agent(run)
    monkeypatch.setattr(cleanup,'probe_count',lambda p:3)
    cleanup.apply(cell)
    for row in rows:
        assert (w.ws/'executions/e000001'/row['filename']).exists()
        assert (w.private/'snapshots/objects'/row['sha256']).exists()


@pytest.mark.parametrize('name', ['work-proof.json', 'scratch/grasp.png'])
def test_missing_nonframe_object_still_fails(run,tmp_path,name):
    cell,w,rows,ep,cut=agent(run)
    if name.startswith('scratch/'):
        (w.ws/'scratch').mkdir()
        (w.ws/name).write_bytes(b'authored drawing')
        cut=freeze_origin(w,'post-feedback-v1')
    manifest=json.loads(Path(cut['path']).read_bytes())
    obj=w.private/'snapshots/objects'/manifest['files'][name]['sha256']
    obj.unlink()
    planned=cleanup.plan(cell)
    assert str(obj.relative_to(cell)) not in {n for g in planned['views'] for n in g['already_missing']}
    cleanup.apply(cell)
    with pytest.raises(ValueError) as error:provision_replay(cut,tmp_path/'replay')
    assert str(error.value)==f'snapshot_object_missing: {name} (file was deleted; continue-from-attempt is unavailable for this cut)'


def test_recording_missing_only_receipt_and_frame_boundary(run):
    cell,ep=run
    folder=ep/'recording_frames'
    for path in folder.glob('*.jpg'):path.unlink()
    with (folder/'frames.jsonl').open('a') as stream:
        for name in ('notes.json','camera_initial.png','camera_final.png'):
            stream.write(json.dumps(dict(file=name))+'\n')
    planned=cleanup.plan(cell)
    missing={str((folder/f'{i:06d}.jpg').relative_to(cell)) for i in range(3)}
    assert set(planned['views'][0]['already_missing'])==missing
    assert planned['frames_deleted']==0
    receipt=cleanup.apply(cell)
    assert (cell/'frame-cleanup.json').is_file()
    assert cleanup.cleaned_files(cell,receipt)==missing
    assert cleanup.was_cleaned(folder/'000001.jpg')
    assert receipt['frames_deleted']==receipt['bytes_freed']==0
    assert cleanup.apply(cell)==receipt


@pytest.mark.parametrize('keep_both_manifests', [True, False])
def test_prior_agent_prune_recorded_in_receipt_and_consumed_by_ui_replay(run,tmp_path,keep_both_manifests):
    cell,w,rows,ep,cut=agent(run)
    missing=set()
    for row in rows[1:]:
        for base in (w.ws,w.canonical):
            path=base/'executions/e000001'/row['filename']
            path.unlink();missing.add(str(path.relative_to(cell)))
        obj=w.private/'snapshots/objects'/row['sha256']
        obj.unlink();missing.add(str(obj.relative_to(cell)))
    if not keep_both_manifests:(w.ws/'executions/e000001/frames.json').unlink()
    planned=cleanup.plan(cell)
    g=next(g for g in planned['views'] if g['execution'].startswith('agent/'))
    assert set(g['already_missing'])==missing
    receipt=cleanup.apply(cell)
    assert missing<=cleanup.cleaned_files(cell,receipt)
    replay=provision_replay(cut,tmp_path/'replay')
    assert json.loads((replay.private/'replay-origin.json').read_bytes())['frames_cleaned']==2
    if not keep_both_manifests:
        (w.ws/'executions/e000001/frames.json').write_bytes((w.canonical/'executions/e000001/frames.json').read_bytes())
    name='executions/e000001/'+rows[1]['filename']
    assert _link('/', 'frame',w.canonical,name)['label']==cleanup.CLEANED
    with pytest.raises(cleanup.CleanedFrame,match='cleaned up'):
        artifact(dict(artifact_dir=str(cell),dev_mode='agent'),name)
    listing=artifact(dict(artifact_dir=str(cell),dev_mode='agent'),'executions/e000001/frames.json')[0]
    assert listing[1]['available'] is False and listing[1]['label']==cleanup.CLEANED


def test_cleaned_files_unions_deleted_missing_and_partial_intent(tmp_path):
    receipt=dict(status='in_progress',views=[dict(deleted_files=['frames/a.jpg'],
        already_missing=['frames/b.jpg'],files=['frames/a.jpg','frames/c.jpg','frames/d.jpg'])])
    (tmp_path/'frames').mkdir();(tmp_path/'frames/d.jpg').write_bytes(b'present')
    assert cleanup.cleaned_files(tmp_path,receipt)=={'frames/a.jpg','frames/b.jpg','frames/c.jpg'}
    assert cleanup.cleaned_files(tmp_path,dict(views=[dict(deleted_files=['frames/a.jpg'])]))=={'frames/a.jpg'}


def test_real_ffprobe(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):pytest.skip('ffmpeg/ffprobe unavailable')
    path=tmp_path/'tiny.mp4'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=size=16x16:rate=5',
                    '-frames:v','4','-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True,capture_output=True)
    assert cleanup.probe_count(path)==4
    path.write_bytes(b'corrupt')
    with pytest.raises(subprocess.CalledProcessError):cleanup.probe_count(path)


def test_cleaned_demo_source_starts_and_plays(tmp_path):
    from benchmarks.attention_harness.tests.test_demo_engine import Bridge,EID,KEY,Clock
    from benchmarks.attention_harness.demo_engine import DemoEngine
    class CleanedBridge(Bridge):
        def get(self,path):
            assert not path.endswith(('.jpg','.png','.jpeg')), 'lobby opened a frame'
            return super().get(path)
    bridge=CleanedBridge();clock=Clock()
    demo=DemoEngine(dict(mode='demo',state_dir=str(tmp_path),speed=1,seed=1,
                        sources={EID:dict(task_key='lift',title='Lift cup')}),bridge,clock=clock)
    owner,_=demo.lobby.identify();card=demo.lobby.enqueue(owner,task_key='lift');demo.tick()
    clock.step(60);demo.tick();run=demo.lobby.find(card['experiment_id']);key=demo.key(run)
    assert demo.get(run,f'/cells/{key}/view',owner)[0]['dev_v2_compat']['versions']
    result=demo.get(run,f'/cells/{key}/agent/files/replays/e000001/'+('c'*64)+'/advisor_replay.mp4',owner)
    assert result[1]=='video/mp4'


def test_backfill_dry_run_report(run,tmp_path):
    from scripts.attentionbench.frame_cleanup_backfill import inventory,markdown
    cell,_=run
    value=inventory(tmp_path)
    assert value['totals']['runs']==1 and value['runs'][0]['terminal']
    assert 'Bytes freed' in markdown(value)
    assert not (cell/'frame-cleanup.json').exists()


def test_http_cleaned_frame_is_410(monkeypatch):
    from types import SimpleNamespace
    from benchmarks.attention_harness import ui_server
    monkeypatch.setattr(ui_server, 'ThreadingHTTPServer', lambda address, handler: SimpleNamespace(handler=handler))
    class Bridge:
        def get(self, path):raise cleanup.CleanedFrame(cleanup.CLEANED)
    server=ui_server.create_server(dev_v2_bridge=Bridge(),port=0)
    handler=object.__new__(server.handler)
    handler.path='/api/dev-v2/experiments/devexp-'+'a'*24+'/cells/'+'b'*64+'/agent/files/executions/e000001/frames/x.jpg'
    result=[];handler._json=lambda value,status=200:result.append((value,status))
    assert handler._v2_get() is True
    assert result==[({'error':cleanup.CLEANED},410)]


def test_catalog_builder_uses_frame_rule(tmp_path):
    from benchmarks.attention_harness.memory_independent_validator import sealed_files
    for name in ('live/view.jpg','frames/view.png','recording_views/side/000000.jpg','recording_frames/1.jpeg',
                 'camera_initial.png','camera_final.png','frames/frames.json','clip.mp4',
                 'snapshots/objects/abc','snapshots/objects/a.png','snapshots/objects/code.py'):
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture')
    files=sealed_files(tmp_path,recursive=True)
    assert set(files)=={'camera_initial.png','camera_final.png','frames/frames.json','clip.mp4',
                       'snapshots/objects/abc','snapshots/objects/a.png','snapshots/objects/code.py'}


@pytest.mark.parametrize('name,expected', [
    ('live/a.jpg',True),('frames/a.jpeg',True),('recording_frames/a.png',True),
    ('recording_views/top/a.jpg',True),('snapshots/objects/a.png',False),
    ('snapshots/objects/abc',False),('snapshots/objects/code.py',False),
    ('live/camera_initial.png',False),('frames/camera_final.png',False),
    ('a.png',False),('frames/a.json',False)])
def test_frame_image_rule(name,expected):
    assert cleanup.is_frame_image(name) is expected


def test_terminal_hook_detaches_and_never_changes_outcome(run,monkeypatch):
    cell,_=run;calls=[]
    monkeypatch.setattr(cleanup.subprocess,'Popen',lambda cmd,**kw:calls.append((cmd,kw)))
    cleanup.schedule(cell)
    assert calls and calls[0][1]['start_new_session'] is True
    assert str(os.getpid()) in calls[0][0]
    assert calls[0][1]['env']['PYTHONDONTWRITEBYTECODE']=='1'
    repo=Path(cleanup.__file__).resolve().parents[2]
    assert calls[0][1]['cwd']==repo
    assert calls[0][1]['env']['PYTHONPATH'].split(os.pathsep)[0]==str(repo)
    monkeypatch.setattr(cleanup.subprocess,'Popen',lambda *a,**k:(_ for _ in ()).throw(OSError('spawn failed')))
    cleanup.schedule(cell)
    assert 'spawn failed' in cleanup.notices(cell)[0]


def test_terminal_hook_resolves_release_symlink_and_preserves_pythonpath(run,tmp_path,monkeypatch):
    cell,_=run;calls=[]
    repo=Path(cleanup.__file__).resolve().parents[2]
    link=tmp_path/'repos/attentionbench';link.parent.mkdir();link.symlink_to(repo,target_is_directory=True)
    monkeypatch.setattr(cleanup,'__file__',str(link/'benchmarks/attention_harness/frame_cleanup.py'))
    monkeypatch.setenv('PYTHONPATH','/existing/module/path')
    monkeypatch.setattr(cleanup.subprocess,'Popen',lambda cmd,**kw:calls.append((cmd,kw)))
    cleanup.schedule(cell)
    assert calls[0][1]['cwd']==repo
    assert calls[0][1]['env']['PYTHONPATH']==str(repo)+os.pathsep+'/existing/module/path'


def test_trash_restore_rejects_tampering(run,tmp_path):
    cell,ep=run;trash=tmp_path/'trash'
    cleanup.apply(cell,trash_dir=trash)
    moved=next(trash.rglob('000001.jpg'));moved.write_bytes(b'changed')
    with pytest.raises(ValueError,match='byte/inode'):cleanup.restore(trash)


def test_additional_view_first_frames_and_live_mapping(run):
    cell,ep=run
    for view in ('side','top'):
        journal=ep/f'recording_views/{view}/frames.jsonl'
        write(journal.parent/'000000.jpg',{'frame':0});write(journal.parent/'000001.jpg',{'frame':1})
        journal.write_text(''.join(json.dumps(dict(file=f'{i:06d}.jpg'))+'\n' for i in range(3)))
        (ep/f'advisor_replay_{view}.mp4').write_bytes(b'video')
        for i in range(2):write(ep/f'live/views/{view}/frame-{i:06d}.jpg',{'frame':i})
    cleanup.apply(cell)
    for view in ('side','top'):
        assert (ep/f'recording_views/{view}/000000.jpg').exists()
        assert not (ep/f'live/views/{view}/frame-000001.jpg').exists()
        assert scene_image(dict(artifact_dir=str(cell)),cell.parents[2],view) is not None


def test_cleanup_errors_are_visible_notices(run):
    from types import SimpleNamespace
    from benchmarks.attention_harness.tests.test_notices import ledger,Bridge
    from benchmarks.attention_harness.notices import NoticeReader
    cell,_=run
    path=ledger(cell.parent.parent.parent)
    with sqlite3.connect(path) as db:
        row=json.loads(db.execute('SELECT payload FROM v2_cells').fetchone()[0])
        row['artifact_dir']=str(cell)
        db.execute('UPDATE v2_cells SET payload=?',(json.dumps(row),))
    write(cell/'frame-cleanup-error.json',dict(error='ffprobe failed'))
    notices=NoticeReader(Bridge(path),probe=lambda:True).read()['notices']
    assert any(n['title']=='Frame cleanup needs attention' and n['detail']=='ffprobe failed' for n in notices)


def test_missing_frame_object_without_cleanup_receipt_uses_verified_index(run,tmp_path):
    cell,w,rows,ep,cut=agent(run)
    (w.private/'snapshots/objects'/rows[1]['sha256']).unlink()
    replay=provision_replay(cut,tmp_path/'replay')
    assert json.loads((replay.private/'replay-origin.json').read_bytes())['frames_cleaned']==1
    assert not (cell/'frame-cleanup.json').exists()


def test_missing_unlisted_frame_object_still_fails(run,tmp_path):
    cell,w,rows,ep,cut=agent(run)
    name='executions/e000001/frames/overview-999999-sim-9.000000s.jpg'
    entry=w.publish(name,b'unlisted frame')
    cut=freeze_origin(w,'post-feedback-v1')
    (w.private/'snapshots/objects'/entry['sha256']).unlink()
    with pytest.raises(FileNotFoundError):provision_replay(cut,tmp_path/'replay')


def test_no_receipt_frame_skip_requires_untampered_index(run,tmp_path):
    cell,w,rows,ep,cut=agent(run)
    (w.private/'snapshots/objects'/rows[1]['sha256']).unlink()
    manifest=json.loads(Path(cut['path']).read_bytes())
    index=manifest['files']['executions/e000001/frames.json']
    (w.private/'snapshots/objects'/index['sha256']).write_bytes(b'[]')
    with pytest.raises(ValueError,match='snapshot_object_mismatch'):provision_replay(cut,tmp_path/'replay')


def test_detached_cleanup_after_worker_exit(run,tmp_path,monkeypatch):
    import sys,time
    cell,ep=run
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):pytest.skip('ffmpeg/ffprobe unavailable')
    (ep/'advisor_replay.mp4').unlink()
    subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=size=16x16:rate=5',
                    '-frames:v','4','-c:v','libx264','-pix_fmt','yuv420p',str(ep/'advisor_replay.mp4')],check=True,capture_output=True)
    code='import sys; sys.path.insert(0,sys.argv[2]); from benchmarks.attention_harness.frame_cleanup import schedule; schedule(sys.argv[1])'
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('PYTHONPATH',str(tmp_path))
    subprocess.run([sys.executable,'-B','-c',code,str(cell.relative_to(tmp_path)),str(Path(cleanup.__file__).resolve().parents[2])],check=True,
                   env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        receipt=cell/'frame-cleanup.json'
        if receipt.is_file() and json.loads(receipt.read_bytes()).get('status')=='completed':break
        time.sleep(.05)
    assert json.loads(receipt.read_bytes())['status']=='completed'
    assert not (ep/'recording_frames/000001.jpg').exists()


def test_backfill_cli_writes_json_and_markdown(run,tmp_path,monkeypatch):
    from scripts.attentionbench.frame_cleanup_backfill import main
    import sys
    output=tmp_path/'inventory.json'
    monkeypatch.setattr(sys,'argv',['backfill','--runs-root',str(tmp_path),'--output',str(output)])
    main()
    assert json.loads(output.read_bytes())['totals']['runs']==1
    assert output.with_suffix('.md').is_file()
    assert not (run[0]/'frame-cleanup.json').exists()


def test_backfill_approved_apply_restore_roundtrip(run,tmp_path,monkeypatch):
    from scripts.attentionbench.frame_cleanup_backfill import main
    import sys
    cell,ep=run;approved=tmp_path/'approved.json';approved.write_text(json.dumps([cell.parent.parent.name]))
    trash=tmp_path/'trash';output=tmp_path/'applied.json'
    monkeypatch.setattr(sys,'argv',['backfill','--runs-root',str(tmp_path),'--apply','--trash-dir',str(trash),
                                  '--runs',str(approved),'--output',str(output)])
    main()
    assert not (ep/'recording_frames/000001.jpg').exists()
    assert (trash/cell.parent.parent.name/'cells'/cell.name/'restore.json').exists()
    monkeypatch.setattr(sys,'argv',['backfill','--restore',str(trash)])
    main()
    assert (ep/'recording_frames/000001.jpg').exists() and not (cell/'frame-cleanup.json').exists()


def test_isolated_service_queue_row_is_never_dispatched(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from benchmarks.attention_harness.memory_validation_worker import isolate_queue_errors
    from benchmarks.attention_harness import memory_cross_seed_worker as cross
    row=dict(memory_id='missing',status='isolated',reason='registered evidence file missing')
    state={};isolate_queue_errors(state,[row])
    assert state['note_errors']['missing']['reason']==row['reason']
    monkeypatch.setattr(cross,'settings',lambda root:dict(cross_seed=dict(enabled=True)))
    deps=SimpleNamespace(root=tmp_path,client=SimpleNamespace(cross_seed_queue=lambda:[row]),runner=None)
    messages=[]
    cross.choose({},deps,lambda:None,messages.append)
    assert messages==[row['reason']]


def test_monitor_displays_isolation_reason_without_reverifying_row():
    from benchmarks.attention_harness.memory_monitor import automatic_checks
    from types import SimpleNamespace
    row=dict(memory_id='missing',title='Missing note',status='isolated',reason='registered evidence file missing')
    gateway=SimpleNamespace(validation_queue=lambda:[row],memory_events=lambda **kw:[],cross_seed_queue=lambda:[row],
                            cross_seed_status=lambda **kw:(_ for _ in ()).throw(AssertionError('isolated row reverified')))
    value=automatic_checks(root=None,gateway=gateway)
    assert value['queue'][0]['status']=='isolated'
    assert any('registered evidence file missing' in error for error in value['errors'])
