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


@pytest.fixture
def nested_run(run, tmp_path, request):
    """The host layout: runs/<devexp>/artifacts/<devexp>/cells/<64hex>."""
    cell, episode = run
    experiment = cell.parent.parent
    root = tmp_path/getattr(request, 'param', 'runs')
    artifact = root/experiment.name/'artifacts'/experiment.name
    artifact.parent.mkdir(parents=True)
    shutil.move(str(experiment), str(artifact))
    episode = artifact/episode.relative_to(experiment)
    cell = artifact/'cells'/cell.name
    bind(cell)
    return root, cell, episode


def test_apply_receipt_first_frame_readonly_idempotent(run):
    cell, ep = run
    frame = ep/'recording_frames/000001.jpg'; frame.chmod(0o444)
    p=cleanup.plan(cell); assert p['frames_deleted']==2 and p['bytes_freed']==200
    receipt=cleanup.apply(cell)
    raw=(cell/'frame-cleanup.json').read_bytes()
    assert receipt['schema']==cleanup.SCHEMA and receipt['status']=='completed'
    assert receipt['bytes_freed']==200 and receipt['frames_deleted']==2
    assert receipt['views'][0]['bytes_freed']==200 and receipt['views'][0]['delete_count']==2
    assert 'resumes' not in receipt and 'resumed_at' not in receipt
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


def test_queued_cell_without_artifact_dir_has_no_cleanup_errors(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from benchmarks.attention_harness.dev_agent_view import projection
    store=SimpleNamespace(path=tmp_path/'ledger.sqlite3')
    cell=dict(key='queued',experiment_id='experiment',dev_mode='agent',state='planned',versions=[])
    with sqlite3.connect(store.path) as db:
        db.execute('CREATE TABLE v2_experiments(id TEXT PRIMARY KEY,payload TEXT)')
        db.execute('INSERT INTO v2_experiments VALUES (?,?)',
                   (cell['experiment_id'],json.dumps(dict(selection={}))))
    # An unset artifact directory must not fall back to the current directory.
    monkeypatch.chdir(tmp_path)
    write(tmp_path/'frame-cleanup-error.json',dict(error='unrelated cleanup failure'))
    write(tmp_path/'frame-cleanup.json',dict(error='unrelated receipt failure'))
    view=projection(store,cell,now=123)
    assert view['state']=='planned'
    assert view['agent']['errors']==[]
    assert view['dev_v2_compat']['versions']==[]


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


def interrupt_cleanup(cell, monkeypatch, *, trash=None):
    original = os.rename if trash else Path.unlink
    calls = []
    def interrupted(*args, **kw):
        source = Path(args[0])
        if source.suffix == '.jpg':
            calls.append(source)
            if len(calls) == 2:raise KeyboardInterrupt('power loss')
        return original(*args, **kw)
    with monkeypatch.context() as patcher:
        patcher.setattr(os if trash else Path, 'rename' if trash else 'unlink', interrupted)
        with pytest.raises(KeyboardInterrupt):cleanup.apply(cell, trash_dir=trash)
    value = cleanup.load(cell/'frame-cleanup.json')
    assert value['status'] == 'in_progress'
    assert len(value['views'][0]['deleted_files']) == 1
    return value


def cleanup_totals(value):
    keys = ('bytes_freed', 'frames_deleted', 'delete_count', 'bytes_reclaimable')
    return [{k: row[k] for k in keys if k in row} for row in (value, *value['views'])]


def test_resume_interrupted_cleanup(run, monkeypatch):
    cell, ep = run
    before = interrupt_cleanup(cell, monkeypatch)
    value = cleanup.apply(cell)
    assert value['status'] == 'completed' and len(value['resumed_at']) == 1
    assert cleanup_totals(value) == cleanup_totals(before)
    assert value['resumes'] == [dict(at=value['resumed_at'][0], deleted_files=1,
                                    already_missing=1, bytes_deleted=100, newly_skipped_views=0)]
    assert len(value['views'][0]['deleted_files']) == 2
    assert before['views'][0]['deleted_files'][0] in value['views'][0]['already_missing']
    assert (ep/'recording_frames/000000.jpg').exists()
    assert not (ep/'recording_frames/000002.jpg').exists()


def test_resume_no_files_group_with_missing_video_has_no_skip_or_notice(run):
    cell, ep = run
    probe = cell/'agent/private/probe-owner-0001'
    frames = probe/'recording_frames'
    frames.mkdir(parents=True)
    (frames/'000000.jpg').write_bytes(b'kept first frame')
    (frames/'frames.jsonl').write_text(json.dumps(dict(file='000000.jpg'))+'\n')
    value = cleanup.plan(cell)
    group = next(g for g in value['views'] if g['execution'] == str(probe.relative_to(cell)))
    assert group['files'] == [] and not group.get('skipped')
    assert not (cell/group['mp4']).exists()
    value['status'] = 'in_progress'
    cleanup.atomic(cell/'frame-cleanup.json', value)

    after = cleanup.apply(cell)
    group = next(g for g in after['views'] if g['execution'] == str(probe.relative_to(cell)))
    assert after['status'] == 'completed'
    assert not group.get('skipped')
    assert cleanup.notices(cell) == []
    assert (frames/'000000.jpg').exists()
    assert not (ep/'recording_frames/000002.jpg').exists()


def test_resume_trash_same_directory_and_restore(run, tmp_path, monkeypatch):
    cell, ep = run; trash = tmp_path/'trash'
    before = interrupt_cleanup(cell, monkeypatch, trash=trash)
    value = cleanup.apply(cell)  # No caller-supplied trash destination on restart.
    assert value['status'] == 'completed' and value['trash_run'] == before['trash_run']
    assert cleanup_totals(value) == cleanup_totals(before)
    assert value['resumes'] == [dict(at=value['resumed_at'][0], deleted_files=1,
                                    already_missing=1, bytes_deleted=100, newly_skipped_views=0)]
    restore = cleanup.load(Path(value['trash_run'])/'restore.json')
    assert len(restore['files']) == 2
    cleanup.restore(trash)
    assert (ep/'recording_frames/000001.jpg').exists() and (ep/'recording_frames/000002.jpg').exists()


def test_resume_missing_trash_refuses_unlink(run, tmp_path, monkeypatch):
    cell, ep = run; trash = tmp_path/'trash'
    before = interrupt_cleanup(cell, monkeypatch, trash=trash)
    shutil.rmtree(trash)
    with pytest.raises(ValueError, match='trash.*missing'):cleanup.apply(cell)
    assert (ep/'recording_frames/000002.jpg').exists()
    assert cleanup.load(cell/'frame-cleanup.json') == before


@pytest.mark.parametrize('failure', ['count', 'missing', 'decode', 'producer'])
def test_resume_reverifies_and_skips_failed_video(run, monkeypatch, failure):
    cell, ep = run
    before = interrupt_cleanup(cell, monkeypatch)
    if failure == 'count':monkeypatch.setattr(cleanup, 'probe_count', lambda p:3)
    if failure == 'missing':(ep/'advisor_replay.mp4').unlink()
    if failure == 'decode':monkeypatch.setattr(cleanup, 'probe_count', lambda p:(_ for _ in ()).throw(ValueError('decode error')))
    if failure == 'producer':(ep/'recording_frames/frames.jsonl').write_text('{}\n')
    value = cleanup.apply(cell)
    assert value['status'] == 'completed' and value['views'][0]['skipped']
    assert cleanup_totals(value) == cleanup_totals(before)
    assert value['resumes'] == [dict(at=value['resumed_at'][0], deleted_files=0,
                                    already_missing=0, bytes_deleted=0, newly_skipped_views=1)]
    assert (ep/'recording_frames/000002.jpg').exists()


def test_resume_never_reconsiders_skipped_group(run):
    cell, ep = run
    value = cleanup.plan(cell);value['status'] = 'in_progress'
    value['views'][0]['skipped'] = 'previous verification failure'
    cleanup.atomic(cell/'frame-cleanup.json', value)
    after = cleanup.apply(cell)
    assert after['views'][0]['skipped'] == 'previous verification failure'
    assert cleanup_totals(after) == cleanup_totals(value)
    assert after['resumes'][0]['newly_skipped_views'] == 0
    assert (ep/'recording_frames/000001.jpg').exists()


@pytest.mark.parametrize('status', ['completed', 'error'])
def test_finished_receipts_untouched(run, status):
    cell, _ = run
    value = dict(status=status, views=[]);cleanup.atomic(cell/'frame-cleanup.json', value)
    before = (cell/'frame-cleanup.json').read_bytes()
    assert cleanup.apply(cell) == value
    assert (cell/'frame-cleanup.json').read_bytes() == before


def test_cleanup_lock_refuses_second_process(run):
    cell, _ = run
    with cleanup.run_lock(cell):
        with pytest.raises(ValueError, match='another frame_cleanup'):cleanup.apply(cell)
    assert cleanup.apply(cell)['status'] == 'completed'


def test_resume_guard_still_refuses_paused_run(run, monkeypatch):
    cell, ep = run;interrupt_cleanup(cell, monkeypatch)
    bind(cell, state='paused')
    with pytest.raises(ValueError, match='terminal, unpaused'):cleanup.apply(cell)
    assert (ep/'recording_frames/000002.jpg').exists()


def test_backfill_continues_past_refused_run(run, tmp_path, monkeypatch):
    from scripts.attentionbench import frame_cleanup_backfill as backfill
    cell, _ = run
    original = cell.parent.parent
    refused = tmp_path/'devexp-refused';refused.mkdir()
    write(refused/'manifest.json', {})
    runs = [dict(run_id='devexp-refused',path=str(refused)),dict(run_id=original.name,path=str(original))]
    monkeypatch.setattr(backfill, 'inventory', lambda root:dict(runs=runs, totals={}))
    monkeypatch.setattr(backfill, 'markdown', lambda value:'report')
    approved=tmp_path/'approved.json';write(approved,[r['run_id'] for r in runs])
    output=tmp_path/'report.json'
    monkeypatch.setattr(__import__('sys'), 'argv', ['backfill','--runs-root',str(tmp_path),'--runs',str(approved),
        '--apply','--trash-dir',str(tmp_path/'trash'),'--output',str(output)])
    backfill.main()
    result=cleanup.load(output)['applied']
    assert result[0]['status'].startswith('refused:')
    assert result[1]['cells'][0]['status']=='completed'


def test_backfill_inventory_marks_in_progress_and_resumes(run, tmp_path, monkeypatch):
    from scripts.attentionbench.frame_cleanup_backfill import inventory
    cell, _ = run;interrupt_cleanup(cell, monkeypatch)
    assert inventory(tmp_path)['runs'][0]['cleanup_status'] == 'in_progress'
    assert cleanup.apply(cell.parent.parent)['cells'][0]['status'] == 'completed'


@pytest.mark.parametrize('nested_run', ['runs', 'daily-experiments'], indirect=True)
def test_startup_sweep_background_resume_and_notice(nested_run, monkeypatch):
    root, cell, _ = nested_run;interrupt_cleanup(cell, monkeypatch)
    calls=[]
    monkeypatch.setattr(cleanup, 'schedule', lambda root, **kw:calls.append((root,kw)))
    bad=root/'devexp-broken/artifacts/devexp-broken/cells'/('c'*64)/'frame-cleanup.json'
    bad.parent.mkdir(parents=True);bad.write_text('{')
    thread=cleanup.startup_sweep(root);thread.join(3)
    assert not thread.is_alive()
    assert calls==[(cell,dict(wait_parent=False))]
    assert cleanup.notices(bad.parent)


def test_startup_schedule_does_not_wait_for_server(run, monkeypatch):
    cell, _ = run;interrupt_cleanup(cell, monkeypatch)
    calls=[];monkeypatch.setattr(cleanup.subprocess,'Popen',lambda cmd,**kw:calls.append(cmd))
    cleanup.schedule(cell, wait_parent=False)
    assert calls[0][-1] == '0'


@pytest.mark.parametrize('nested_run', ['runs', 'daily-experiments'], indirect=True)
def test_idle_check_reports_in_progress_receipt(nested_run):
    root, cell, _ = nested_run
    write(cell/'frame-cleanup.json',dict(status='in_progress'))
    script=Path(__file__).resolve().parents[3]/'scripts/attentionbench/idle-check.sh'
    done=subprocess.run(['bash',str(script),str(root)],capture_output=True,text=True)
    assert done.returncode==1 and 'in_progress cleanup:' in done.stdout
    assert str(cell/'frame-cleanup.json') in done.stdout
    assert cell.parent.parent.name in done.stdout
    assert 'invalid escape sequence' not in done.stderr


@pytest.mark.parametrize('layout', [
    'frame-cleanup.json',
    'cells/'+'b'*64+'/frame-cleanup.json',
    'artifacts/devexp-one/frame-cleanup.json',
    'artifacts/devexp-one/cells/'+'b'*64+'/frame-cleanup.json',
])
@pytest.mark.parametrize('contents', ['{"status":"in_progress"}', '{', '[]'])
def test_idle_check_receipt_layouts_fail_closed(tmp_path, layout, contents):
    root=tmp_path/'daily-experiments'
    receipt=root/'devexp-one'/layout
    receipt.parent.mkdir(parents=True);receipt.write_text(contents)
    script=Path(__file__).resolve().parents[3]/'scripts/attentionbench/idle-check.sh'
    done=subprocess.run(['bash',str(script),str(root)],capture_output=True,text=True)
    assert done.returncode==1
    assert str(receipt) in done.stdout and 'devexp-one' in done.stdout
    assert ('in_progress cleanup:' if contents.startswith('{"status"') else 'cannot check cleanup:') in done.stdout
    assert 'invalid escape sequence' not in done.stderr


def test_live_cleanup_process_is_an_owner_even_before_lock(run):
    import sys
    cell, _ = run
    child=subprocess.Popen([sys.executable,'-B','-c','import sys; sys.stdin.read()',
                            'benchmarks.attention_harness.frame_cleanup',str(cell)],stdin=subprocess.PIPE)
    try:
        assert cleanup.state(cell)['active']
        with pytest.raises(ValueError,match='no live'):cleanup.apply(cell)
    finally:child.communicate(timeout=3)


@pytest.mark.parametrize('trash_mode', [False, True])
def test_resume_missing_only_and_second_interruption(run,tmp_path,monkeypatch,trash_mode):
    cell, ep = run
    planned = cleanup.plan(cell)
    before = interrupt_cleanup(cell,monkeypatch,trash=tmp_path/'trash' if trash_mode else None)
    # Preserve the original planned totals, as in a receipt checkpointed before
    # the interrupted run's final accounting.
    for saved, original in zip((before, *before['views']), (planned, *planned['views'])):
        for key in ('bytes_freed', 'frames_deleted', 'delete_count'):
            if key in original and not (trash_mode and key == 'bytes_freed'):
                saved[key] = original[key]
    prior = dict(at='2026-11-14T00:00:00+00:00', deleted_files=0,
                 already_missing=1, bytes_deleted=0, newly_skipped_views=0)
    before.update(resumed_at=[prior['at']], resumes=[prior])
    cleanup.atomic(cell/'frame-cleanup.json', before)
    # Simulate another abrupt exit after unlink but before progress is checkpointed.
    (ep/'recording_frames/000002.jpg').unlink()
    value=cleanup.apply(cell)
    assert value['status']=='completed'
    assert cleanup_totals(value) == cleanup_totals(before)
    assert value['resumed_at'] == [prior['at'], value['resumes'][1]['at']]
    assert value['resumes'] == [prior, dict(at=value['resumed_at'][1], deleted_files=0,
                                          already_missing=2, bytes_deleted=0, newly_skipped_views=0)]
    assert len(value['views'][0]['already_missing'])==2
    assert len(value['views'][0]['deleted_files'])==1
    assert cleanup.load(cell/'frame-cleanup.json') == value


def test_startup_sweep_thread_failure_is_notice_not_server_failure(run,monkeypatch):
    cell,_=run
    monkeypatch.setattr(cleanup.threading.Thread,'start',lambda self:(_ for _ in ()).throw(RuntimeError('thread unavailable')))
    assert cleanup.startup_sweep(cell) is None
    assert 'thread unavailable' in cleanup.notices(cell)[0]


def test_backfill_resumes_unlink_receipt_with_trash_option(run,tmp_path,monkeypatch):
    from scripts.attentionbench import frame_cleanup_backfill as backfill
    cell,ep=run;interrupt_cleanup(cell,monkeypatch)
    approved=tmp_path/'approved.json';write(approved,[cell.parent.parent.name])
    output=tmp_path/'report.json';trash=tmp_path/'unused-trash'
    monkeypatch.setattr(__import__('sys'),'argv',['backfill','--runs-root',str(tmp_path),'--apply',
        '--runs',str(approved),'--trash-dir',str(trash),'--output',str(output)])
    backfill.main()
    assert cleanup.load(cell/'frame-cleanup.json')['status']=='completed'
    assert not trash.exists() and not (ep/'recording_frames/000002.jpg').exists()


def test_both_ui_servers_schedule_startup_roots(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from benchmarks.attention_harness import ui_server,dev_ui_server
    calls=[];monkeypatch.setattr(cleanup,'startup_sweep',lambda *roots:calls.append(roots))
    monkeypatch.setattr(ui_server,'ThreadingHTTPServer',lambda *args:SimpleNamespace())
    monkeypatch.setattr(dev_ui_server,'ThreadingHTTPServer',lambda *args:SimpleNamespace())
    monkeypatch.setattr(dev_ui_server,'harden_process',lambda:None)
    ui_server.create_server(artifact_root=tmp_path,dev_v2_bridge=SimpleNamespace(root=tmp_path/'runs'),port=0)
    dev_ui_server.serve(SimpleNamespace(path=tmp_path/'ledger.sqlite3'),tmp_path,0)
    assert calls==[(tmp_path,tmp_path/'runs'),(tmp_path,)]


def test_real_startup_resume_completes_while_server_parent_is_alive(nested_run,monkeypatch):
    import sys
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):pytest.skip('ffmpeg/ffprobe unavailable')
    root,cell,ep=nested_run;interrupt_cleanup(cell,monkeypatch)
    (ep/'advisor_replay.mp4').unlink()
    subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=size=16x16:rate=5',
        '-frames:v','4','-c:v','libx264','-pix_fmt','yuv420p',str(ep/'advisor_replay.mp4')],check=True,capture_output=True)
    code='''import json,sys,time
from pathlib import Path
from benchmarks.attention_harness.frame_cleanup import startup_sweep
root=Path(sys.argv[1]);cell=Path(sys.argv[2]);startup_sweep(root)
end=time.monotonic()+5
while time.monotonic()<end:
 if json.loads((cell/'frame-cleanup.json').read_bytes()).get('status')=='completed':break
 time.sleep(.05)
assert json.loads((cell/'frame-cleanup.json').read_bytes())['status']=='completed'
'''
    done=subprocess.run([sys.executable,'-B','-c',code,str(root),str(cell)],capture_output=True,text=True,timeout=8)
    assert done.returncode==0,done.stdout+done.stderr
    assert not (ep/'recording_frames/000002.jpg').exists()
