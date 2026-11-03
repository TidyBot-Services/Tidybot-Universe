"""Derived replay sidecars. Source recordings and evidence are always read-only."""
import argparse
import bisect
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import shutil
import sqlite3
import subprocess
import sys
import tempfile

import numpy as np

SCHEMA = 'attentionbench.replay-trim.v1'
VIEWS = ('overview','side','top','left_wrist','right_wrist')
PARAMS = dict(gap_s=1.,hold_s=1.,dedupe_tau=.0002,min_break_s=1.,
              analysis_width=320,analysis_height=180,pixel_threshold=10)
HOOK_TIMEOUT = 300


def _file(root, name):
    root=Path(root)
    path=root/name
    if any(p.is_symlink() for p in (root,*root.parents)):
        raise ValueError('symlink_execution')
    current=root
    for part in Path(name).parts:
        if part in ('..','/') or Path(name).is_absolute():raise ValueError('outside_execution')
        current=current/part
        if current.is_symlink():raise ValueError('symlink_media')
    if not path.is_file() or path.stat().st_size==0:raise ValueError('missing_or_empty_'+name)
    return path


def _sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1<<20),b''):h.update(chunk)
    return h.hexdigest()


def _run(args, *, check_timestamps=False, **kwargs):
    # Backfill and the hook are deliberately CPU-only and low priority.
    result=subprocess.run(['nice','-n','19',*args],capture_output=True,timeout=120,**kwargs)
    if result.returncode:raise ValueError(result.stderr.decode(errors='replace')[-1600:])
    if check_timestamps and (b'non monoton' in result.stderr.lower() or b'non-monoton' in result.stderr.lower()):
        raise ValueError('non_monotone_video_timestamps')
    return result.stdout


def _probe(path):
    value=json.loads(_run(['ffprobe','-v','error','-select_streams','v:0','-count_frames',
        '-show_entries','stream=nb_read_frames,duration','-of','json',str(path)]))['streams'][0]
    try:duration=float(value.get('duration',0))
    except (TypeError,ValueError):duration=0.
    return int(value['nb_read_frames']),duration if math.isfinite(duration) and duration>=0 else 0.


def _gray(path, params):
    w,h=params['analysis_width'],params['analysis_height']
    raw=_run(['ffmpeg','-nostdin','-v','error','-i',str(path),'-vsync','0',
        '-vf',f'scale={w}:{h},format=gray','-f','rawvideo','-pix_fmt','gray','pipe:1'])
    return np.frombuffer(raw,dtype=np.uint8).reshape(-1,h*w)


def _metadata(root):
    value=json.loads(_file(root,'camera_recording.json').read_bytes())
    if not isinstance(value,dict):raise ValueError('invalid_camera_recording')
    if not value.get('video_saved'):raise ValueError('video_not_saved')
    timing=value.get('replay_timing') or {}
    if not isinstance(timing,dict):raise ValueError('invalid_replay_timing')
    traces,original=timing.get('frame_trace_seconds'),timing.get('frame_video_seconds')
    if not isinstance(traces,list) or not isinstance(original,list) or not traces or len(traces)!=len(original):
        raise ValueError('invalid_replay_timing')
    for array in (traces,original):
        if any(type(x) not in (int,float) or not math.isfinite(x) or x<0 for x in array):
            raise ValueError('invalid_replay_timing')
        if any(b<a for a,b in zip(array,array[1:])):raise ValueError('non_monotone_replay_timing')
    manifest=value.get('frame_manifest_sha256')
    if not isinstance(manifest,str) or len(manifest)!=64 or any(c not in '0123456789abcdef' for c in manifest):
        raise ValueError('missing_frame_manifest_sha256')
    return value,list(traces),list(original)


def _interp(seconds, original, traces):
    index=max(0,min(len(original)-1,bisect.bisect_right(original,seconds)-1))
    if index==len(original)-1:return traces[index]+seconds-original[index]
    width=original[index+1]-original[index]
    return traces[index]+(seconds-original[index])*(traces[index+1]-traces[index])/width if width else traces[index]


def analyze(exec_dir, **params):
    params={**PARAMS,**params}
    if params.keys()!=PARAMS.keys():raise ValueError('unknown_trim_parameter')
    for key,value in params.items():
        if type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('invalid_trim_parameter')
        if key in ('analysis_width','analysis_height') and (type(value) is not int or value<1):raise ValueError('invalid_analysis_size')
    if params['hold_s']<1/60 or params['dedupe_tau']>1:raise ValueError('invalid_trim_parameter')
    root=Path(exec_dir);metadata,traces,original=_metadata(root)
    source=_file(root,'advisor_replay.mp4');pixels=_gray(source,params)
    count,video_end=_probe(source)
    if count!=len(pixels) or count not in (len(original),len(original)+1):raise ValueError('recording_frame_count_mismatch')
    if video_end and video_end<original[-1]:raise ValueError('video_shorter_than_timing')
    if count>len(original):
        # The optional terminal hold has no trace row. Extend both clocks by the
        # remaining video duration (zero when unknown), as prescribed by policy B.
        tail=max(0,video_end-original[-1]) if video_end else 0
        original.append(original[-1]+tail);traces.append(traces[-1]+tail)
    video_end=max(video_end,original[-1])
    kept=[0]
    for index in range(1,count):
        changed=np.count_nonzero(np.abs(pixels[index].astype(np.int16)-pixels[kept[-1]])>params['pixel_threshold'])/pixels.shape[1]
        if index==count-1 or changed>params['dedupe_tau']:kept.append(index)
    frames=[];durations=[];breaks=[];elapsed=0.
    for k,index in enumerate(kept):
        start=original[index];end=original[kept[k+1]] if k+1<len(kept) else video_end
        duration=max(1/60,min(end-start,params['hold_s']))
        if k==len(kept)-1:duration=max(1/30,duration)
        frames.append(dict(src_index=index,trace=traces[index],orig=start,trim=elapsed))
        durations.append(duration);elapsed+=duration
        skipped=end-start-duration
        if skipped>=params['min_break_s'] and skipped>0:
            # The skip follows the displayed hold: trim_at is t[k+1] (or end).
            wait=any(b-a>params['gap_s'] and b>start+duration and a<end
                     for a,b in zip(original,original[1:]))
            breaks.append(dict(orig_start=start+duration,orig_end=end,skipped_seconds=skipped,
                trace_start=_interp(start+duration,original,traces),trace_end=_interp(end,original,traces),
                trim_at=elapsed,kind='wait' if wait else 'still'))
    return dict(schema=SCHEMA,params=params,source=dict(video='advisor_replay.mp4',sha256=_sha(source),
        frames_manifest_sha256=metadata['frame_manifest_sha256'],frame_count=count,duration_seconds=video_end),
        original_duration_seconds=video_end,trimmed_duration_seconds=elapsed,
        skipped_seconds=max(0,video_end-elapsed),frames=frames,breaks=breaks,
        durations=durations,videos={},skipped_views={},tool='replay_trim/1')


def _encode(source, target, plan, temporary):
    frames=Path(temporary)/'decoded';frames.mkdir()
    _run(['ffmpeg','-nostdin','-v','error','-i',str(source),'-vsync','0',str(frames/'%06d.png')])
    decoded=sorted(frames.glob('*.png'))
    if len(decoded)!=plan['source']['frame_count']:raise ValueError('recording_frame_count_mismatch')
    selected=Path(temporary)/'selected';selected.mkdir()
    for k,frame in enumerate(plan['frames']):
        path=selected/f'{k:06d}.png';shutil.move(decoded[frame['src_index']],path)
        stamp=1_000_000_000_000_000_000+round(frame['trim']*1e9);os.utime(path,ns=(stamp,stamp))
    # image2 timestamps preserve VFR without appending a duplicate sentinel frame.
    # -r sets the final packet's hold; a fine encoder time base preserves all PTS.
    # No B frames: reordered DTS made MP4 stream duration underreport long holds.
    rate=f"{1/plan['durations'][-1]:.9f}"
    _run(['ffmpeg','-nostdin','-y','-v','error','-f','image2','-ts_from_file','2','-framerate',rate,
        '-i',str(selected/'%06d.png'),'-vsync','vfr','-c:v','libx264','-crf','20','-pix_fmt','yuv420p','-bf','0',
        '-enc_time_base','1:60000','-video_track_timescale','60000','-r',rate,'-movflags','+faststart',str(target)],check_timestamps=True)
    probe=json.loads(_run(['ffprobe','-v','error','-select_streams','v:0','-show_entries',
        'packet=pts_time,dts_time','-show_entries','stream=duration','-of','json',str(target)]))
    packets=probe['packets'];dts=[float(p['dts_time']) for p in packets]
    pts=sorted(float(p['pts_time']) for p in packets)
    if (len(packets)!=len(plan['frames']) or any(b<=a for a,b in zip(dts,dts[1:]))
            or any(abs(p-f['trim'])>1/30000 for p,f in zip(pts,plan['frames']))
            or abs(float(probe['streams'][0]['duration'])-plan['trimmed_duration_seconds'])>1/30):
        raise ValueError('trimmed_video_timing_mismatch')


def build(exec_dir, *, force=False):
    root=Path(exec_dir)
    previous=load_verified(root)
    if previous and previous['params']==PARAMS and not force:return previous
    plan=analyze(root)
    folder=root/'replay_trim'
    if folder.is_symlink():raise ValueError('symlink_trim_directory')
    folder.mkdir(exist_ok=True)
    # All writes in the execution tree are under replay_trim. Publish marker last.
    with tempfile.TemporaryDirectory(prefix='.build-',dir=folder) as staging:
        for view in VIEWS:
            name='advisor_replay.mp4' if view=='overview' else f'advisor_replay_{view}.mp4'
            try:
                source=_file(root,name)
                count,_=_probe(source)
                if count!=plan['source']['frame_count']:raise ValueError('recording_frame_count_mismatch')
            except (OSError,ValueError) as exc:
                if view=='overview':raise
                plan['skipped_views'][view]=str(exc);continue
            target=Path(staging)/(view+'.mp4')
            with tempfile.TemporaryDirectory(prefix='replay-trim-') as temporary:_encode(source,target,plan,temporary)
            plan['videos'][view]=dict(file='replay_trim/'+view+'.mp4',sha256=_sha(target))
        if _sha(_file(root,'advisor_replay.mp4'))!=plan['source']['sha256'] or _metadata(root)[0]['frame_manifest_sha256']!=plan['source']['frames_manifest_sha256']:
            raise ValueError('source_changed_during_build')
        del plan['durations']
        marker=Path(staging)/'segments.json'
        marker.write_text(json.dumps(plan,sort_keys=True,indent=2,allow_nan=False)+'\n')
        for view in plan['videos']:
            target=folder/(view+'.mp4')
            if target.is_symlink():raise ValueError('symlink_trim_video')
            (Path(staging)/(view+'.mp4')).replace(target)
        marker.replace(folder/'segments.json')
    return plan


def load_verified(exec_dir):
    try:
        root=Path(exec_dir);value=json.loads(_file(root,'replay_trim/segments.json').read_bytes())
        metadata,traces,original=_metadata(root)
        if value['schema']!=SCHEMA or value['tool']!='replay_trim/1':return None
        source=value['source']
        if any(not isinstance(value[k],dict) for k in ('source','params','videos','skipped_views')):return None
        if any(not isinstance(value[k],list) for k in ('frames','breaks')):return None
        if source['video']!='advisor_replay.mp4' or source['sha256']!=_sha(_file(root,'advisor_replay.mp4')):return None
        if source['frames_manifest_sha256']!=metadata['frame_manifest_sha256']:return None
        if set(value['params'])!=set(PARAMS) or not value['frames'] or 'overview' not in value['videos']:return None
        # Reject malformed mappings before exposing any of their numbers to the UI.
        numbers=[value[k] for k in ('original_duration_seconds','trimmed_duration_seconds','skipped_seconds')]
        previous=None
        for frame in value['frames']:
            index=frame['src_index']
            if type(index) is not int or index<0 or index>=source['frame_count']:return None
            numbers.extend(frame[k] for k in ('trace','orig','trim'))
            if previous and any(frame[k]<=previous[k] for k in ('src_index','trim')):return None
            if previous and any(frame[k]<previous[k] for k in ('trace','orig')):return None
            previous=frame
        for entry in value['breaks']:
            if entry['kind'] not in ('wait','still'):return None
            numbers.extend(entry[k] for k in ('orig_start','orig_end','skipped_seconds','trace_start','trace_end','trim_at'))
        numbers.extend(value['params'].values())
        if any(type(n) not in (int,float) or not math.isfinite(n) or n<0 for n in numbers):return None
        for view,video in value['videos'].items():
            if view not in VIEWS or video['file']!='replay_trim/'+view+'.mp4':return None
            if _sha(_file(root,video['file']))!=video['sha256']:return None
        if not isinstance(value['skipped_views'],dict):return None
        return value
    except (OSError,ValueError,KeyError,TypeError,IndexError,OverflowError):return None


def public_trim(exec_dir):
    value=load_verified(exec_dir)
    if value is None:return None
    def rounded(item):
        if isinstance(item,float):return round(item,3)
        if isinstance(item,list):return [rounded(x) for x in item]
        if isinstance(item,dict):return {k:rounded(v) for k,v in item.items()}
        return item
    # The execution's parent is its eid; use the projection's virtual media root.
    eid=Path(exec_dir).parent.name
    from .dev_agent_ui_projection import ROOT
    return rounded(dict(schema=SCHEMA,params={k:value['params'][k] for k in ('gap_s','hold_s')},
        **{k:value[k] for k in ('original_duration_seconds','trimmed_duration_seconds','skipped_seconds','breaks')},
        replay_timing=dict(frame_trace_seconds=[f['trace'] for f in value['frames']],
            frame_video_seconds=[f['trim'] for f in value['frames']],frame_orig_video_seconds=[f['orig'] for f in value['frames']]),
        videos={view:dict(uri=f"{ROOT}/replays/{eid}/{v['sha256']}/trim/{view}.mp4",sha256=v['sha256'])
                for view,v in value['videos'].items()}))


def _log(log, message):
    try:log(message)
    except Exception:pass


def build_safe(exec_dir, log):
    """Bound the complete builder, including decoding, and kill its process group."""
    if os.environ.get('ATTENTIONBENCH_REPLAY_TRIM','1')=='0':return
    process=None
    try:
        process=subprocess.Popen([sys.executable,'-m','benchmarks.attention_harness.replay_trim','build',str(Path(exec_dir).absolute())],
            cwd=Path(__file__).resolve().parents[2],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,start_new_session=True)
        try:_,error=process.communicate(timeout=HOOK_TIMEOUT)
        except BaseException:
            os.killpg(process.pid,signal.SIGKILL);process.communicate();raise
        if process.returncode:raise ValueError(error.decode(errors='replace')[-1600:])
    except BaseException as exc:_log(log,'replay_trim failed: '+type(exc).__name__+': '+str(exc))


def _terminal(run):
    path=_file(run,'ledger.sqlite3')
    # Immutable reads cannot create SQLite WAL/SHM files in a dry-run. A pending
    # WAL makes that snapshot incomplete, so conservatively refuse that run.
    wal=path.with_name(path.name+'-wal')
    if wal.exists() and wal.stat().st_size:raise ValueError('ledger_has_pending_wal')
    with sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True) as db:
        rows=[json.loads(row[0]) for row in db.execute('SELECT payload FROM v2_experiments')]
        if not rows or any(r.get('state') not in ('completed','failed','cancelled') for r in rows):return False
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('v2_jobs','v2_agent_jobs'):
            if table in tables and db.execute(f"SELECT 1 FROM {table} WHERE state IN ('claimed','needs_review','running') LIMIT 1").fetchone():return False
    return True


def backfill(args):
    root=Path(args.root).absolute();rows=[];stopped=False
    for run in sorted(root.iterdir()):
        if not run.is_dir() or run.is_symlink() or (args.run and not any(run.name.startswith(p) for p in args.run)):continue
        for receipt in sorted(run.glob('**/executions/e*/openarm365-dev-*/camera_recording.json')):
            episode=receipt.parent
            row=dict(run=run.name,eid=episode.parent.name,episode=str(episode.relative_to(root)),status='skipped',predicted_extra_bytes='unknown')
            try:
                if not _terminal(run):raise ValueError('run_not_terminal_or_jobs_active')
                if not args.force and load_verified(episode):raise ValueError('valid_sidecar_already_present')
                if args.execute and shutil.disk_usage('/').free<args.min_free_gb*1e9:
                    stopped=True;raise ValueError('disk_guard')
                plan=analyze(episode)
                row.update(original_duration_seconds=plan['original_duration_seconds'],predicted_trimmed_duration_seconds=plan['trimmed_duration_seconds'],
                    kept_frames=len(plan['frames']),breaks=len(plan['breaks']),skipped_seconds=plan['skipped_seconds'])
                if args.execute:
                    # Recheck after analysis before the first sidecar write.
                    if shutil.disk_usage('/').free<args.min_free_gb*1e9:
                        stopped=True;raise ValueError('disk_guard')
                    build(episode,force=args.force)
                row['status']='built' if args.execute else 'planned'
            except (OSError,ValueError,KeyError,sqlite3.Error,subprocess.SubprocessError) as exc:row['reason']=str(exc)
            rows.append(row);print(json.dumps(row,sort_keys=True),flush=True)
            if stopped:break
        if stopped:break
    totals=dict(executions=len(rows),planned=sum(r['status']=='planned' for r in rows),built=sum(r['status']=='built' for r in rows),
        skipped=sum(r['status']=='skipped' for r in rows),stopped=stopped,predicted_extra_bytes='unknown')
    for key in ('original_duration_seconds','predicted_trimmed_duration_seconds','kept_frames','breaks','skipped_seconds'):
        totals[key]=sum(r.get(key,0) for r in rows)
    report=dict(executions=rows,totals=totals);print(json.dumps(dict(totals=totals),sort_keys=True))
    if args.report:Path(args.report).write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    return 2 if stopped else 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    batch=commands.add_parser('backfill');batch.add_argument('--root',required=True);batch.add_argument('--run',action='append',default=[])
    mode=batch.add_mutually_exclusive_group();mode.add_argument('--dry-run',action='store_true');mode.add_argument('--execute',action='store_true')
    batch.add_argument('--force',action='store_true');batch.add_argument('--min-free-gb',type=float,default=130.);batch.add_argument('--report')
    single=commands.add_parser('build');single.add_argument('exec_dir');single.add_argument('--force',action='store_true')
    args=parser.parse_args(argv)
    if args.command=='build':build(args.exec_dir,force=args.force);return 0
    if not math.isfinite(args.min_free_gb) or args.min_free_gb<0:parser.error('invalid --min-free-gb')
    # An explicit report must not undermine the dry-run's read-only root contract.
    if args.report and Path(args.report).resolve().is_relative_to(Path(args.root).resolve()):parser.error('--report must be outside --root')
    return backfill(args)


if __name__=='__main__':sys.exit(main())
