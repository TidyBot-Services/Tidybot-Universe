"""Export public RGB only. Recorder/extra_views render pixels without overlays.

StepRecorder writes flipped RGB through PIL, ExtraViews saves renderer RGB; neither
path draws scores/text. Re-encoding here drops EXIF, comments and source filenames.
Simulation time is control sequence / frozen control_freq, not wall/video PTS.
"""
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import tempfile
from PIL import Image
from .dev_agent_workspace import read_beneath

VIEWS = ('overview', 'side', 'top', 'left_wrist', 'right_wrist')


def recording_frames(episode, control_freq):
    root = Path(episode)
    if type(control_freq) not in (int, float) or not math.isfinite(control_freq) or control_freq <= 0:
        raise ValueError('invalid_recording_frequency')
    for view in VIEWS:
        folder = root / ('recording_frames' if view == 'overview' else 'recording_views/'+view)
        journal = folder / 'frames.jsonl'
        video = root / ('advisor_replay.mp4' if view == 'overview' else f'advisor_replay_{view}.mp4')
        if not journal.is_file():
            if not video.is_file(): continue
            receipt = json.loads(read_beneath(root, 'camera_recording.json', 16*1024*1024))
            if receipt.get('initial_frame_saved') is not True: raise ValueError('missing_simulation_time_mapping')
            count=receipt['frame_count']
            if view != 'overview':
                views=json.loads(read_beneath(root,'camera_views.json',16*1024*1024))
                count=views['views'][view]['frame_count']
            rows = [dict(physical_sequence=i) for i in range(count)]
        else:
            rows = [json.loads(line) for line in read_beneath(folder, 'frames.jsonl', 32*1024*1024).splitlines()]
        missing = any(not (folder/row.get('file','missing')).is_file() for row in rows)
        with tempfile.TemporaryDirectory(prefix='agent-frames-') as temporary:
            decoded = []
            if missing:
                if not video.is_file() or video.is_symlink(): raise ValueError('recording_frame_missing')
                subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(video),'-vsync','0',
                                str(Path(temporary)/'%06d.png')],check=True,capture_output=True,timeout=60)
                decoded = sorted(Path(temporary).glob('*.png'))
                if len(decoded) not in (len(rows), len(rows)+1): raise ValueError('recording_frame_count_mismatch')
                # The encoder may add one terminal hold frame at the same simulation state.
                if len(decoded)>len(rows): rows.append(dict(rows[-1]))
            for index, row in enumerate(rows):
                seq = row['physical_sequence']
                if type(seq) is not int or seq < 0: raise ValueError('invalid_frame_sequence')
                if decoded: raw = decoded[index].read_bytes()
                else:
                    raw = read_beneath(folder, row['file'], 32*1024*1024)
                    if hashlib.sha256(raw).hexdigest() != row['sha256']: raise ValueError('recording_digest_mismatch')
                with Image.open(io.BytesIO(raw)) as picture:
                    clean = Image.frombytes('RGB', picture.size, picture.convert('RGB').tobytes())
                    output = io.BytesIO(); clean.save(output, format='JPEG', quality=92, subsampling=0)
                seconds = seq/control_freq
                yield dict(filename=f'frames/{view}-{index:06d}-sim-{seconds:.6f}s.jpg',
                    view=view,index=index,sim_seconds=seconds,physical_sequence=seq,
                    trace_seconds=row.get('trace_seconds'),data=output.getvalue())
