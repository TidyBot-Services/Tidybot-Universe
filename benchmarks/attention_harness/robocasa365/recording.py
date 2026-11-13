"""Durable public RGB from actual simulator control steps; no simulation calls."""
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image

from ..ffmpeg_hold import final_hold

# 11-13 (user approved 5 fps replay): new OpenArm365 dev_v2 executions render every 4th control step plus
# every action boundary. Old contexts carry no frame_stride and keep the original one-frame-per-step rule.
FRAME_STRIDE=4


def _write(path, value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,sort_keys=True,allow_nan=False)+'\n')
    temporary.replace(path)


class StepRecorder:
    def __init__(self, episode_dir, context):
        self.episode_dir=Path(episode_dir)
        self.directory=self.episode_dir/'recording_frames'
        self.directory.mkdir(exist_ok=True)
        self.context=context
        self.last_sequence=-1
        self.episode_id=None
        self.count=0
        self.error=None

    def record_error(self, exc):
        self.error=type(exc).__name__+': '+str(exc)
        try:
            _write(self.directory/'error.json',dict(error=self.error,frame_count=self.count))
        except OSError:
            pass  # Camera faults must never change an action receipt.

    def capture(self, observation, episode_id, sequence, *,
                source='official_simulator_public_rgb_after_each_control_step'):
        try:
            if sequence==self.last_sequence and episode_id==self.episode_id:
                return
            if type(sequence) is not int or sequence<0 or sequence<=self.last_sequence:
                raise ValueError('recording sequence regressed')
            if self.episode_id is not None and episode_id!=self.episode_id:
                raise ValueError('recording episode changed')
            raw=np.asarray(observation[self.context['camera_name']+'_image'])
            if raw.dtype!=np.uint8 or raw.ndim!=3 or raw.shape[2]!=3 or not all(0<s<=2048 for s in raw.shape[:2]):
                raise ValueError('recording requires native public uint8 RGB')
            if self.count>=5000:
                raise ValueError('recording frame limit reached')
            captured=time.time()
            seconds=time.monotonic()-self.context['started_monotonic']
            rgb=raw[::-1].copy()  # MuJoCo public images have bottom-up rows.
            name=f'{self.count:06d}.jpg'
            frame=self.directory/name
            temporary=self.directory/(name+'.tmp')
            Image.fromarray(rgb).save(temporary,format='JPEG',quality=95)
            temporary.replace(frame)
            row={k:self.context[k] for k in ('run_id','attempt_id','camera_name')}
            row.update(file=name,sha256=hashlib.sha256(frame.read_bytes()).hexdigest(),
                episode_id=episode_id,physical_sequence=sequence,captured_at=captured,
                trace_seconds=seconds,width=raw.shape[1],height=raw.shape[0],source=source)
            with (self.directory/'frames.jsonl').open('a') as stream:
                stream.write(json.dumps(row,sort_keys=True,allow_nan=False)+'\n')
                stream.flush();os.fsync(stream.fileno())
            self.count+=1
            self.last_sequence,self.episode_id=sequence,episode_id
            if self.context.get('preview_dir'):
                from ..live_preview import save_preview
                from ..public_station import publish_public_station
                directory=Path(self.context['preview_dir'])
                save_preview(directory,run_id=row['run_id'],attempt_id=row['attempt_id'],
                    frame=frame.read_bytes(),sequence=self.count,captured_at=captured,
                    width=row['width'],height=row['height'])
                if self.count==1:
                    publish_public_station(run_dir=directory,run_id=row['run_id'],attempt_id=row['attempt_id'],
                        suite='robocasa365',origin=self.context['origin'],camera_name=row['camera_name'],
                        camera_transport='robocasa365_preview')
        except Exception as exc:
            self.record_error(exc)


def finalize_recording(episode_dir, context, execution_status):
    episode=Path(episode_dir);directory=episode/'recording_frames'
    receipt=dict(schema_version='attentionbench.camera-recording.v1',
        source='official_simulator_public_rgb_after_each_control_step',
        run_id=context['run_id'],attempt_id=context['attempt_id'],camera_name=context['camera_name'],
        control_interface_status=context.get('control_interface_status','not_recorded'),
        execution_status=execution_status,status='missing',frame_count=0,video_saved=False,
        sampled_seconds=0,requested_fps=None,truncated=False,error=None,
        timestamp_basis='monotonic_wall_clock',encoded_timebase_fps=1000,
        terminal_hold_seconds=.04,initial_frame_saved=False,final_frame_saved=False)
    try:
        journal=directory/'frames.jsonl'
        if not journal.is_file():
            raise ValueError('No control-step RGB was recorded; the selected service may predate execution recording')
        lines=journal.read_text().splitlines(keepends=True)
        rows=[]
        journal_error=None
        for index,line in enumerate(lines):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                if index!=len(lines)-1 or line.endswith('\n'):
                    raise
                journal_error='Process ended during an incomplete final frame journal write; durable earlier frames retained'
        receipt['frame_count']=len(rows)
        stride=context.get('frame_stride',1)
        if type(stride) is not int or not 1<=stride<=8:
            raise ValueError('recording frame_stride must be an integer 1..8')
        if stride!=1:receipt['frame_stride']=stride
        if len(rows)<2:
            raise ValueError('Fewer than two real control-step frames were saved; static evidence is not an execution video')
        if len(rows)>5000:
            raise ValueError('recording frame limit exceeded')
        for index,row in enumerate(rows):
            if any(row[key]!=context[key] for key in ('run_id','attempt_id','camera_name')):
                raise ValueError('recording frame identity mismatch')
            if row['file']!=f'{index:06d}.jpg' or hashlib.sha256((directory/row['file']).read_bytes()).hexdigest()!=row['sha256']:
                raise ValueError('recording frame hash mismatch')
            gap_steps=row['physical_sequence']-rows[index-1]['physical_sequence'] if index else 1
            if index and (not 1<=gap_steps<=stride or
                row['trace_seconds']<=rows[index-1]['trace_seconds'] or row['episode_id']!=rows[0]['episode_id']):
                raise ValueError('recording control-step sequence or clock gap')
            if (row['width'],row['height'],row.get('source')) != (rows[0]['width'],rows[0]['height'],rows[0].get('source')):
                raise ValueError('recording resolution or source changed during attempt')
        times=[r['trace_seconds'] for r in rows];video_times=[t-times[0] for t in times]
        gap=[b-a for a,b in zip(times,times[1:])]
        receipt.update(source_width=rows[0]['width'],source_height=rows[0]['height'],
            source=rows[0].get('source',receipt['source']),
            sampled_seconds=times[-1]-times[0],initial_frame_saved=rows[0]['physical_sequence']==0,
            final_frame_saved=execution_status=='completed',max_gap_seconds=max(gap))
        if not shutil.which('ffmpeg'):
            raise RuntimeError('ffmpeg unavailable; original frames and timestamps retained')
        # image2 uses file timestamps at nanosecond precision, including on ffmpeg 4.
        # Encode disposable copies so original evidence metadata is untouched.
        encoding=directory/'encoding'
        encoding.mkdir(exist_ok=True)
        for index,row in enumerate([*rows,rows[-1]]):
            frame=encoding/f'{index:06d}.jpg'
            shutil.copyfile(directory/row['file'],frame)
            seconds=video_times[index] if index<len(rows) else video_times[-1]+.04
            stamp=1_000_000_000_000_000_000+round(seconds*1_000_000_000)
            os.utime(frame,ns=(stamp,stamp))
        temporary=episode/'replay.encoding.mp4'
        command=['ffmpeg','-y','-hide_banner','-loglevel','error','-f','image2',
            '-ts_from_file','2','-framerate','25','-i',str(encoding/'%06d.jpg'),
            '-vsync','vfr','-c:v','libx264','-pix_fmt','yuv420p',
            '-enc_time_base','1:1000','-video_track_timescale','1000',*final_hold(.04),
            '-movflags','+faststart',str(temporary)]
        process=subprocess.run(command,capture_output=True,timeout=20)
        if process.returncode:
            raise RuntimeError('video encoding failed: '+process.stderr.decode(errors='replace')[-1200:])
        probe=subprocess.run(['ffprobe','-v','error','-show_entries','frame=best_effort_timestamp_time',
            '-show_entries','stream=duration','-of','json',str(temporary)],capture_output=True,timeout=5,check=True)
        encoded=json.loads(probe.stdout)
        pts=[float(row['best_effort_timestamp_time']) for row in encoded['frames']]
        if len(pts)!=len(rows)+1 or any(abs(a-b)>.002 for a,b in zip(pts,video_times)):
            raise ValueError('encoded video lost a frame or differs from capture timestamps')
        video_times=pts[:-1]
        temporary.replace(episode/'advisor_replay.mp4')
        error_path=directory/'error.json'
        capture_error=json.loads(error_path.read_text())['error'] if error_path.is_file() else journal_error
        receipt.update(status='partial' if capture_error else 'saved',video_saved=True,error=capture_error,
            source_width=rows[0]['width'],source_height=rows[0]['height'],sampled_seconds=video_times[-1],
            duration_seconds=float(encoded['streams'][0]['duration']),actual_capture_fps=(len(rows)-1)/(times[-1]-times[0]),
            terminal_hold_seconds=float(encoded['streams'][0]['duration'])-video_times[-1],
            max_gap_seconds=max(gap),initial_frame_saved=rows[0]['physical_sequence']==0,
            final_frame_saved=execution_status=='completed',
            frame_manifest_sha256=hashlib.sha256(journal.read_bytes()).hexdigest(),
            replay_timing=dict(basis='relative',event_clock='sdk_completion',
                frame_trace_seconds=times,frame_video_seconds=video_times))
        if stride!=1:
            # Strided recordings cannot be re-derived as range(frame_count) once frame files are pruned.
            receipt['replay_timing']['frame_physical_sequences']=[r['physical_sequence'] for r in rows]
        if receipt['source']=='official_simulator_display_rgb_after_each_control_step':
            # Human static evidence uses the same real HD captures as the replay.
            # Preserve the SDK's original policy images under explicit filenames.
            try:
                for label,row,available in [('initial',rows[0],receipt['initial_frame_saved']),
                                            ('final',rows[-1],receipt['final_frame_saved'])]:
                    if not available:
                        continue
                    target=episode/f'camera_{label}.png'
                    policy=episode/f'policy_camera_{label}.png'
                    if target.exists() and not policy.exists():
                        shutil.copyfile(target,policy)
                    temporary=episode/f'camera_{label}.png.tmp'
                    with Image.open(directory/row['file']) as frame:
                        frame.save(temporary,format='PNG')
                    temporary.replace(target)
            except Exception as exc:
                receipt.update(status='partial',error='Human static evidence: '+type(exc).__name__+': '+str(exc))
    except Exception as exc:
        receipt.update(status='missing' if receipt['frame_count']<2 else 'failed',
                       error=type(exc).__name__+': '+str(exc))
    shutil.rmtree(directory/'encoding',ignore_errors=True)
    _write(episode/'camera_recording.json',receipt)
    if context.get('preview_dir'):
        from ..live_preview import finish_preview
        try:finish_preview(Path(context['preview_dir']),context['run_id'],context['attempt_id'])
        except Exception:pass
    return receipt
