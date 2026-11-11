"""Extra camera angles: display-only recording, live publication and replay encoding. No simulator is started."""
import hashlib,json,subprocess,time,copy
from pathlib import Path
import numpy as np
import pytest
from openarm365_dev_v2 import extra_views as xv
from benchmarks.attention_harness.dev_live_monitor import live,response

class Cam:
    def __init__(self):self.type=None;self.lookat=np.zeros(3);self.azimuth=self.elevation=self.distance=0.0
class Ctx:
    def __init__(self):self.cam=Cam()
class Sim:
    """Records every render call; pixel values encode the call number so frames differ."""
    def __init__(self,fail_on=None):self.calls=[];self._render_context_offscreen=Ctx();self.fail_on=fail_on
    def render(self,width,height,camera_name=None,depth=False):
        cam=self._render_context_offscreen.cam
        label=camera_name or f'free:az{cam.azimuth:.0f}'
        if self.fail_on and self.fail_on in label:raise RuntimeError('render exploded')
        self.calls.append(dict(label=label,type=cam.type,lookat=cam.lookat.copy(),size=(width,height)))
        return np.full((height,width,3),len(self.calls)%251,dtype=np.uint8)
class Env:
    def __init__(self,**kw):self.sim=Sim(**kw)

def make(tmp_path,**kw):
    ev=tmp_path/'episode';ev.mkdir()
    env=Env(**kw);views=xv.ExtraViews(env,ev,{'dev_version':2},'e'*32,[0.3,-1.8,1.0])
    return env,views,ev

def test_each_step_renders_every_view_and_writes_recording_and_live_frames(tmp_path):
    env,views,ev=make(tmp_path)
    for seq in range(5):views.capture(seq)
    labels=[c['label'] for c in env.sim.calls]
    assert labels[:4]==['free:az90','free:az90','gripper0_right_eye_in_hand','gripper0_left_eye_in_hand'] and len(labels)==20  # left_wrist, right_wrist: task arms (11-12)
    assert 'robot0_agentview_left' not in labels                       # the original recording's camera is never touched
    assert all(c['size']==(xv.WIDTH,xv.HEIGHT) for c in env.sim.calls)
    for key in xv.VIEWS:
        rows=[json.loads(l) for l in (ev/'recording_views'/key/'frames.jsonl').read_text().splitlines()]
        assert [r['physical_sequence'] for r in rows]==list(range(5)) and [r['file'] for r in rows]==[f'{i:06d}.jpg' for i in range(5)]
        for r in rows:assert hashlib.sha256((ev/'recording_views'/key/r['file']).read_bytes()).hexdigest()==r['sha256']
        meta=json.loads((ev/'live/views'/key/'latest.json').read_text())
        assert meta['sequence']==4 and meta['version']==2 and meta['episode_id']=='e'*32 and meta['view']==key
        assert hashlib.sha256((ev/'live/views'/key/meta['file']).read_bytes()).hexdigest()==meta['sha256']
        assert len(list((ev/'live/views'/key).glob('frame-*.jpg')))==xv.LIVE_KEEP        # bounded disk use
    assert set(json.loads((ev/'live/views.json').read_text())['views'])=={'external','side','top','left_wrist','right_wrist'}

def test_free_cameras_look_at_the_target_without_changing_the_scene(tmp_path):
    env,views,ev=make(tmp_path);views.capture(0)
    free=[c for c in env.sim.calls if c['label'].startswith('free')]
    assert len(free)==2 and all(np.allclose(c['lookat'],[0.3,-1.8,1.0]) for c in free)
    import mujoco
    assert all(c['type']==mujoco.mjtCamera.mjCAMERA_FREE for c in free)

def test_one_failing_view_never_disables_the_others_or_raises(tmp_path):
    env,views,ev=make(tmp_path,fail_on='left_eye')   # the camera behind right_wrist (task arm, 11-12)
    for seq in range(3):views.capture(seq)                                  # must not raise
    assert views.counts=={'side':3,'top':3,'left_wrist':3,'right_wrist':0}
    errors=json.loads((ev/'recording_views/error.json').read_text())['errors']
    assert set(errors)=={'right_wrist'} and 'render exploded' in errors['right_wrist']

def test_sequence_regression_and_missing_render_context_are_contained(tmp_path):
    env,views,ev=make(tmp_path);views.capture(3);views.capture(3);views.capture(2)
    assert views.counts['side']==1
    class NoCtx:
        class sim:pass
    ev2=tmp_path/'e2';ev2.mkdir();broken=xv.ExtraViews(NoCtx,ev2,{},'f'*32,[0,0,0]);broken.capture(0)   # no offscreen context
    assert broken.active=={} and 'all' in broken.errors

def main_recording(ev,count,step=.05):
    """The original recording's journal and video, which the extra views are aligned to."""
    d=ev/'recording_frames';d.mkdir()
    rows=[dict(physical_sequence=i,trace_seconds=10+i*step,file=f'{i:06d}.jpg') for i in range(count)]
    (d/'frames.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows));(ev/'advisor_replay.mp4').write_bytes(b'main video')
    return rows

def frame_count(video):
    out=subprocess.run(['ffprobe','-v','error','-count_frames','-select_streams','v:0','-show_entries','stream=nb_read_frames','-of','json',str(video)],capture_output=True,check=True)
    return int(json.loads(out.stdout)['streams'][0]['nb_read_frames'])

def test_finalize_encodes_each_view_on_the_main_recording_timeline(tmp_path):
    env,views,ev=make(tmp_path)
    for seq in range(6):views.capture(seq)
    main_recording(ev,6)
    manifest=xv.finalize_extra_views(ev)
    assert json.loads((ev/'camera_views.json').read_text())==manifest and manifest['source']=='display_only_not_policy_input'
    assert manifest['views']['external']['video']=='advisor_replay.mp4' and manifest['views']['external']['width']==1920
    for key in xv.VIEWS:
        entry=manifest['views'][key]
        assert entry['status']=='saved' and entry['frame_count']==6 and entry['video']==f'advisor_replay_{key}.mp4' and entry['error'] is None
        assert frame_count(ev/entry['video'])==7                              # 6 control steps + the terminal hold, like the original
    assert not list(ev.glob('*.encoding*'))                                   # scratch copies are removed

def test_finalize_keeps_irregular_control_step_times_on_every_ffmpeg(tmp_path):
    """One frame per control step at the original trace times (ffmpeg 4.4 and 6.1 give the same PTS)."""
    env,views,ev=make(tmp_path)
    for seq in range(5):views.capture(seq)
    times=[10.0,10.05,10.11,10.2,10.21]
    d=ev/'recording_frames';d.mkdir()
    (d/'frames.jsonl').write_text(''.join(json.dumps(dict(physical_sequence=i,trace_seconds=t))+'\n' for i,t in enumerate(times)))
    (ev/'advisor_replay.mp4').write_bytes(b'main video')
    manifest=xv.finalize_extra_views(ev)['views']
    for key in xv.VIEWS:
        assert manifest[key]['status']=='saved',manifest[key]
        out=subprocess.run(['ffprobe','-v','error','-show_entries','frame=best_effort_timestamp_time','-of','json',
                            str(ev/manifest[key]['video'])],capture_output=True,check=True)
        pts=[float(f['best_effort_timestamp_time']) for f in json.loads(out.stdout)['frames']]
        assert pts==pytest.approx([t-10 for t in times]+[.25],abs=.002)        # + the 40 ms terminal hold

def test_encoder_uses_the_portable_final_hold_and_keeps_the_ffmpeg_error(tmp_path,monkeypatch):
    """ffmpeg 6.1 refuses -r beside -vsync vfr; the command takes final_hold's options and a failure keeps its tail."""
    env,views,ev=make(tmp_path)
    for seq in range(3):views.capture(seq)
    main_recording(ev,3)
    monkeypatch.setattr(xv,'final_hold',lambda s:['-bf','0','-bsf:v',f'setts=duration={s:.9f}/TB'])
    commands=[];stderr=('x'*2000+' Error opening output file /a/very/long/path/advisor_replay_side.encoding.mp4.'
                         ' Error opening output files: Invalid argument').encode()
    def run(command,**kw):
        commands.append(command);return subprocess.CompletedProcess(command,1,stdout=b'',stderr=stderr)
    monkeypatch.setattr(xv.subprocess,'run',run)
    manifest=xv.finalize_extra_views(ev)['views']
    command=commands[0]
    assert '-r' not in command and 'setts=duration=0.040000000/TB' in command and command[command.index('-vsync')+1]=='vfr'
    error=manifest['side']['error']
    assert manifest['side']['status']=='failed' and error.endswith('Invalid argument') and len(error)>=1200

def test_finalize_reports_missing_corrupt_and_unaligned_views_without_raising(tmp_path):
    env,views,ev=make(tmp_path)
    for seq in range(4):views.capture(seq)
    main_recording(ev,4)
    (ev/'recording_views/top/000001.jpg').write_bytes(b'tampered')            # hash no longer matches
    import shutil;shutil.rmtree(ev/'recording_views/left_wrist')              # never recorded
    (ev/'recording_views/side/frames.jsonl').write_text('')                    # nothing on the timeline
    manifest=xv.finalize_extra_views(ev)['views']
    assert manifest['top']['status']=='failed' and 'hash mismatch' in manifest['top']['error'] and manifest['top']['video'] is None
    assert manifest['left_wrist']['status']=='missing' and manifest['side']['status']=='missing'
    assert manifest['right_wrist']['status']=='saved'                          # one bad view never spoils the rest
    empty=tmp_path/'empty';empty.mkdir()
    out=xv.finalize_extra_views(empty)                                         # a run that failed before any frame
    assert out['views']['external']['status']=='missing' and all(out['views'][k]['status']=='missing' for k in xv.VIEWS)

# ---- live monitor: the view parameter ------------------------------------------------------------------------------
E='devexp-'+'a'*24;K='b'*64
class Store:
    def __init__(self,c):self.c=c
    def cell(self,key):return copy.deepcopy(self.c)

@pytest.fixture
def running(tmp_path):
    root=tmp_path/'cell';ep=root/'v1/native';(ep/'live').mkdir(parents=True)
    EP='c'*32
    main=ep/'live/frame-000007.jpg';main.write_bytes(b'main-view-frame')
    (ep/'live/latest.json').write_text(json.dumps(dict(episode_id=EP,version=1,sequence=7,wall_time=time.time(),file=main.name,sha256=hashlib.sha256(main.read_bytes()).hexdigest())))
    side=ep/'live/views/side';side.mkdir(parents=True);frame=side/'frame-000009.jpg';frame.write_bytes(b'side-view-frame')
    (side/'latest.json').write_text(json.dumps(dict(episode_id=EP,version=1,sequence=9,wall_time=time.time(),file=frame.name,sha256=hashlib.sha256(frame.read_bytes()).hexdigest(),view='side')))
    c=dict(experiment_id=E,key=K,state='running',active_episode=str(ep),artifact_dir=str(root),active_version=1,jobs=[])
    return Store(c),ep

def test_live_serves_the_selected_view_and_defaults_to_the_original(running):
    s,ep=running
    assert live(s,E,K)[1]['sequence']==7 and live(s,E,K,'external')[1]['sequence']==7
    _,meta,frame=live(s,E,K,'side');assert meta['sequence']==9 and frame.name=='frame-000009.jpg'
    body,mime,status,headers=response(s,E,K,image=True,query={'view':['side']})
    assert status==200 and body==b'side-view-frame' and headers['X-Frame-Sequence']=='9'
    assert response(s,E,K,image=True,query={'view':['side'],'seq':['7']})[2]==409           # identity is that view's own
    assert response(s,E,K,image=True,query={'view':['side'],'seq':['9']})[2]==200
    assert response(s,E,K,image=True)[0]==b'main-view-frame'

@pytest.mark.parametrize('view',['top','../../etc','chest','Side'])
def test_unknown_or_unpublished_views_are_not_found_and_cannot_escape(running,view):
    s,ep=running
    body,_,status,_=response(s,E,K,query={'view':[view]})
    assert status in (404,) or body.get('state')=='waiting_first_frame'
    assert response(s,E,K,image=True,query={'view':[view]})[2]!=200
