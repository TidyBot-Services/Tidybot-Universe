"""3D replay of a finished attempt, for people only (display only).

Route: cells/<cell>/agent/files/free-view/<eNNNNNN>/<scene.json|geom.bin.gz|poses.bin.gz>

The first request for an attempt builds the three files once (a few seconds, one
build at a time) with scripts/attentionbench/free_view_export.py under the sim
Python; later requests read them.  They live beside the attempt's own recording
(agent/private/executions/<e>/<episode>/free_view_v1), which is outside the Dev
workspace and goes away with the run.  Nothing here writes to a simulation, and
there is no route that accepts anything from the browser.
"""
import gzip, os, re, shutil, subprocess, threading, time
from pathlib import Path

NAME = re.compile(r'free-view/(e\d+)/(scene\.json|geom\.bin\.gz|poses\.bin\.gz)')
TYPES = {'scene.json': 'application/json', 'geom.bin.gz': 'application/octet-stream', 'poses.bin.gz': 'application/octet-stream'}
FOLDER = 'free_view_v1'
SOURCES = ('native-physics.jsonl', 'scene.xml', 'reset_receipt.json', 'rpc-receipts.jsonl')
EXPORT = Path(__file__).resolve().parents[2]/'scripts/attentionbench/free_view_export.py'
TIMEOUT = 180
RETRY_SECONDS = 600  # A failed build is remembered this long, so a broken attempt is not rebuilt on every click.
MIN_FREE_BYTES = int(os.environ.get('ATTENTIONBENCH_FREE_VIEW_MIN_FREE_GB', '50'))*1024**3
_LOCK = threading.Lock()


def _sim_python():
    from openarm365_dev_v2.dev_config import SIM_PYTHON
    return str(SIM_PYTHON)


def _export(episode, out):
    """Run the exporter. Returns None, or one line saying what went wrong."""
    try:
        run = subprocess.run([_sim_python(), '-I', str(EXPORT), str(episode), str(out)], capture_output=True, text=True, timeout=TIMEOUT,
                             env={'MUJOCO_GL': 'egl', 'PATH': '/usr/bin:/bin'})
    except subprocess.TimeoutExpired:
        return f'the build took longer than {TIMEOUT} s'
    except OSError as exc:
        return f'the build could not start ({exc.__class__.__name__})'
    if run.returncode == 0 and (out/'scene.json').is_file():return None
    return (run.stderr.strip().splitlines() or ['the build failed'])[-1][:300]


def build(episode, out, export=None):
    """Build the bundle of one execution folder into `out`. Returns `out`; raises FileNotFoundError with the reason."""
    episode, out = Path(episode), Path(out)
    if (out/'scene.json').is_file():return out
    note = out.with_name(out.name+'.error.txt')
    with _LOCK:
        if (out/'scene.json').is_file():return out
        if note.is_file() and not note.is_symlink() and time.time()-note.stat().st_mtime < RETRY_SECONDS:
            raise FileNotFoundError(note.read_text(encoding='utf-8'))
        missing = [n for n in SOURCES if not (episode/n).is_file()]
        if missing:
            raise FileNotFoundError('This attempt cannot be shown in 3D: it has no '+', '.join(missing)+' on this machine.')
        if out.is_symlink() or note.is_symlink():raise FileNotFoundError('3D view unavailable')
        if shutil.disk_usage(episode).free < MIN_FREE_BYTES:
            raise FileNotFoundError(f'3D view not built: the run host has less than {MIN_FREE_BYTES//1024**3} GB of free disk.')
        tmp = out.with_name(out.name+'.tmp')
        shutil.rmtree(tmp, ignore_errors=True);tmp.mkdir(parents=True)
        problem = (export or _export)(episode, tmp)
        if not problem:
            try:
                for name in ('geom.bin', 'poses.bin'):
                    with (tmp/name).open('rb') as source, gzip.open(tmp/(name+'.gz'), 'wb', compresslevel=6) as target:
                        shutil.copyfileobj(source, target, 1024*1024)
                    (tmp/name).unlink()
            except OSError as exc:
                problem = f'the built files could not be packed ({exc.__class__.__name__})'
        if problem:
            shutil.rmtree(tmp, ignore_errors=True)
            message = '3D view could not be built for this attempt: '+problem
            note.write_text(message, encoding='utf-8')
            raise FileNotFoundError(message)
        note.unlink(missing_ok=True)
        shutil.rmtree(out, ignore_errors=True)
        tmp.rename(out)
    return out


def bundle_file(cell, name):
    """(Path, content type) of one bundle file of a finished attempt of this cell."""
    match = NAME.fullmatch(name)
    if not match:raise FileNotFoundError('Not a 3D view file')
    from .dev_agent_ui_projection import episode_path
    # The attempt's own recording must exist: that is the same check, on the same folder, that its videos pass.
    episode = episode_path(cell, match[1], 'advisor_replay.mp4').parent
    path = build(episode, episode/FOLDER)/match[2]
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(episode.resolve()):
        raise FileNotFoundError('3D view unavailable')
    return path, TYPES[match[2]]
