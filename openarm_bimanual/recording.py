"""Preserve real sample timing across four independently encoded views."""
from pathlib import Path
import io
import hashlib
import json
import subprocess
import threading
import time
from .display import atomic, camera_specs


def sampling_metrics(times):
    gaps=[b-a for a,b in zip(times,times[1:])]
    span=times[-1]-times[0] if len(times)>1 else 0.
    return {'frame_count':len(times),'capture_span_seconds':span,
        'actual_capture_fps':(len(times)-1)/span if span>0 else None,
        'max_gap_seconds':max(gaps) if gaps else None}


class MultiViewRecording:
    def __init__(self, episode, service_output, *, run_dir=None,run_id=None,attempt_id=None,arm='both'):
        self.episode_dir=Path(episode);self.source=Path(service_output)/'display'
        self.run_dir=Path(run_dir) if run_dir else None
        self.run_id=run_id;self.attempt_id=attempt_id;self.arm=arm
        self._trace_origin=None;self.started=time.monotonic();self.ended=None
        self.done=threading.Event();self.error=None;self.last_sequence=None;self.required_final_step=None;self.required_epoch=None

    def bind_trace_origin(self,value):
        if self._trace_origin is None:self._trace_origin=value

    def __enter__(self):
        self.thread=threading.Thread(target=self._publish,name='display-preview-handoff',daemon=True)
        self.thread.start();return self

    def _publish(self):
        from benchmarks.attention_harness.live_preview import save_preview
        from benchmarks.attention_harness.public_station import save_public_frame
        from PIL import Image
        while not self.done.is_set():
            try:
                row=json.loads((self.source/'latest.json').read_text())
                if row.get('state')=='disconnected':
                    self.error=row.get('error','Display process disconnected')
                    if self.run_dir and self.run_id and self.attempt_id:
                        from benchmarks.attention_harness.live_preview import mark_preview_offline
                        for key in [None, *camera_specs(self.arm)]:
                            mark_preview_offline(self.run_dir,self.run_id,self.attempt_id,self.error,key)
                    self.done.wait(.1);continue
                if row.get('state')!='connected' or row['sequence']==self.last_sequence:
                    self.done.wait(.05);continue
                for key,view in row['views'].items():
                    data=(self.source/view['file']).read_bytes()
                    if self.run_dir and self.run_id and self.attempt_id:
                        save_preview(self.run_dir,run_id=self.run_id,attempt_id=self.attempt_id,
                            frame=data,sequence=row['sequence'],captured_at=row['captured_at'],width=1920,height=1080,view=key)
                        if key=='external':
                            save_preview(self.run_dir,run_id=self.run_id,attempt_id=self.attempt_id,
                                frame=data,sequence=row['sequence'],captured_at=row['captured_at'],width=1920,height=1080)
                            save_public_frame(self.run_dir,suite='robocasa',frame=data)
                    if key=='external' and not (self.episode_dir/'camera_initial.png').exists():
                        image=Image.open(io.BytesIO(data));image.save(self.episode_dir/'camera_initial.png')
                if self.run_dir and self.attempt_id:
                    index=self.attempt_id.split(':')[-1]
                    atomic(self.run_dir/f'public_control-{index}.json',json.dumps({'run_id':self.run_id,'attempt_id':self.attempt_id,'control':row.get('control')}).encode())
                    views={k:{'name':v['name'],'width':1920,'height':1080} for k,v in row['views'].items()}
                    atomic(self.run_dir/f'public_views-{index}.json',json.dumps({'run_id':self.run_id,
                        'attempt_id':self.attempt_id,'views':views}).encode())
                self.last_sequence=row['sequence']
            except FileNotFoundError:pass
            except Exception as exc:self.error=f'{type(exc).__name__}: {exc}'
            self.done.wait(.05)

    def await_physical_step(self, step, epoch, timeout=3.):
        self.required_final_step=step;self.required_epoch=epoch
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            try:
                row=json.loads((self.source/'latest.json').read_bytes())
                if row.get('state')=='disconnected':
                    self.error=row.get('error','Display disconnected before final action capture');return False
                if row.get('epoch')==epoch and row.get('physical_step',-1)>=step:return True
            except (OSError,ValueError):pass
            self.done.wait(.05)
        self.error='Final completed action was not captured before the recording deadline'
        return False

    def __exit__(self,*_):
        self.ended=time.monotonic();self.done.set();self.thread.join(3)
        if self.run_dir and self.attempt_id:
            from benchmarks.attention_harness.live_preview import finish_preview
            for view in [None, *camera_specs(self.arm)]:
                finish_preview(self.run_dir,self.run_id,self.attempt_id,view)

    def finalize(self):
        from PIL import Image
        rows=[]
        try:
            rows=[json.loads(line) for line in (self.source/'samples.jsonl').read_text().splitlines()]
            rows=[row for row in rows if self.started<=row['source_monotonic']<=(self.ended or time.monotonic())]
        except (OSError,ValueError) as exc:self.error=str(exc)
        times=[row['source_monotonic'] for row in rows]
        specs=camera_specs(self.arm)
        receipt={'schema_version':'attentionbench.camera-recording.v1','source':'isolated_render_of_actual_physical_snapshots',
            'source_width':1920,'source_height':1080,'requested_fps':10,'frame_count':len(rows),
            'sampled_seconds':times[-1]-times[0] if len(times)>1 else 0,
            'initial_frame_saved':(self.episode_dir/'camera_initial.png').exists(),'final_frame_saved':False,
            'video_saved':False,'truncated':False,'error':self.error,'views':{},
            **sampling_metrics(times),'timestamp_basis':'physical_snapshot_monotonic',
            'render_process_physical_steps':0,'shared_physical_steps':[row['physical_step'] for row in rows],
            'epoch':[row['epoch'] for row in rows],
            'resource_samples_uri':str(self.source/'samples.jsonl')}
        if rows:
            origin=times[0];durations=[b-a for a,b in zip(times,times[1:])]+[.1]
            # ffmpeg image concat uses 25 Hz time base. Record the actual
            # common quantization separately from raw physical timestamps.
            video_times=[round((t-origin)*25)/25 for t in times]
            receipt['replay_timing']={'basis':'relative','event_clock':'sdk_completion',
                'frame_trace_seconds':[t-(self._trace_origin or origin) for t in times],
                'frame_video_seconds':video_times}
            controls={'schema':'attentionbench.bimanual-control-recording.v1','run_id':self.run_id,'attempt_id':self.attempt_id,
                'samples':[{'video_seconds':v,'trace_seconds':t-(self._trace_origin or origin),'control':row['control']}
                           for row,t,v in zip(rows,times,video_times)]}
            from .replay import validate_control_recording
            validate_control_recording(controls,run_id=self.run_id,attempt_id=self.attempt_id)
            control_file=self.episode_dir/'bimanual_control.json'
            control_file.write_text(json.dumps(controls,separators=(',',':'))+'\n')
            receipt['control_recording']={'file':control_file.name,'sha256':hashlib.sha256(control_file.read_bytes()).hexdigest(),
                'schema':controls['schema'],'sample_count':len(rows)}
            def encode_view(item):
                key,spec=item
                target=self.episode_dir/('advisor_replay.mp4' if key=='external' else f'advisor_replay_{key}.mp4')
                listing=self.episode_dir/f'frames_{key}.txt'
                lines=[]
                for row,duration in zip(rows,durations):
                    file=(self.source/row['views'][key]['file']).resolve()
                    if "'" in str(file):raise ValueError('Recording path contains an unsupported quote')
                    lines.extend([f"file '{file}'",f'duration {duration:.9f}'])
                lines.append(lines[-2]);listing.write_text('\n'.join(lines)+'\n')
                try:
                    encoded=subprocess.run(['ffmpeg','-y','-loglevel','error','-f','concat','-safe','0','-i',str(listing),
                    '-vsync','vfr','-an','-c:v','libx264','-threads','2','-preset','veryfast','-crf','18',
                    '-pix_fmt','yuv420p','-movflags','+faststart',str(target)],capture_output=True,timeout=120)
                except (OSError,subprocess.TimeoutExpired) as exc:
                    encoded=subprocess.CompletedProcess([],1,stderr=str(exc).encode())
                ok=encoded.returncode==0 and target.is_file() and target.stat().st_size>0
                view={'name':spec['name'],'parent':spec['parent'],'file':target.name,
                    'source_width':1920,'source_height':1080,'video_saved':ok,
                    'error':None if ok else encoded.stderr.decode(errors='replace')[:1000]}
                return key,view
            # Evidence encoding starts after owned physics has stopped. Four
            # independent two-thread encoders retain each native view and clock.
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=4) as encoders:
                for key,view in encoders.map(encode_view,specs.items()):
                    receipt['views'][key]=view
                    if not view['video_saved']:self.error=view['error']
            receipt['video_saved']=all(view['video_saved'] for view in receipt['views'].values())
            Image.open(self.source/rows[-1]['views']['external']['file']).save(self.episode_dir/'camera_final.png')
            receipt['final_frame_saved']=True
        receipt['error']=self.error
        complete=bool(rows and (self.required_final_step is None or rows[-1]['physical_step']>=self.required_final_step and rows[-1]['epoch']==self.required_epoch))
        receipt['coverage']={'complete':complete,'required_final_action_step':self.required_final_step,'required_epoch':self.required_epoch,'last_physical_step':rows[-1]['physical_step'] if rows else None}
        receipt['truncated']=bool(self.error or not complete)
        (self.episode_dir/'camera_recording.json').write_text(json.dumps(receipt,indent=2)+'\n')
