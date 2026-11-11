"""3D replay of a finished attempt, for people only (display only).

Route: cells/<cell>/agent/files/free-view/<eNNNNNN>/<scene.json|geom.bin.gz|poses.bin.gz>
       cells/<cell>/agent/files/free-view/live/<scene.json|geom.bin.gz|status.json|poses-<from frame>.bin>

The first request for an attempt builds the three files once (a few seconds, one
build at a time) with scripts/attentionbench/free_view_export.py under the sim
Python; later requests read them.  They live beside the attempt's own recording
(agent/private/executions/<e>/<episode>/free_view_v1), which is outside the Dev
workspace and goes away with the run.  Nothing here writes to a simulation, and
there is no route that accepts anything from the browser.

Live (the attempt that is running now): the first request starts one follower process for that attempt
(free_view_export.py --live).  It reads the attempt's logs as they grow and writes into a cache folder outside
the run store (ATTENTIONBENCH_FREE_VIEW_LIVE_DIR).  Every request marks the attempt as watched; the follower stops
a minute after the last request, or shortly after the attempt ends, and removes its folder.  Doors and drawers
stay where they started until the attempt ends (their final position is only known then).
"""
import gzip, json, os, re, shutil, subprocess, threading, time
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
LIVE = re.compile(r'free-view/live/(scene\.json|geom\.bin\.gz|status\.json|poses-(\d{1,7})\.bin)')
LIVE_TYPES = {'scene.json': 'application/json', 'status.json': 'application/json', 'geom.bin.gz': 'application/octet-stream'}
LIVE_ROOT = Path(os.environ.get('ATTENTIONBENCH_FREE_VIEW_LIVE_DIR') or Path.home()/'.cache/attentionbench/free-view-live')
LIVE_WAIT = 40       # seconds a first request waits for the follower to write the scene
MAX_FOLLOWERS = 4    # at most this many attempts are followed at once on one host
_FOLLOWERS = {}      # live folder -> Popen


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
    if name.startswith('free-view/live/'):return live_file(cell, name)
    match = NAME.fullmatch(name)
    if not match:raise FileNotFoundError('Not a 3D view file')
    from .dev_agent_ui_projection import episode_path
    # The attempt's own recording must exist: that is the same check, on the same folder, that its videos pass.
    episode = episode_path(cell, match[1], 'advisor_replay.mp4').parent
    path = build(episode, episode/FOLDER)/match[2]
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(episode.resolve()):
        raise FileNotFoundError('3D view unavailable')
    return path, TYPES[match[2]]


def _start_follower(episode, out):
    return subprocess.Popen([_sim_python(), '-I', str(EXPORT), '--live', str(episode), str(out)], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                            env={'MUJOCO_GL': 'egl', 'PATH': '/usr/bin:/bin'})


def live_folder(episode, out, start=None, wait=LIVE_WAIT, sleep=time.sleep):
    """Make sure one follower runs for this attempt; mark it watched; wait for its scene. Returns `out`."""
    with _LOCK:
        for folder, proc in list(_FOLLOWERS.items()):
            if proc.poll() is not None:_FOLLOWERS.pop(folder)
        if out not in _FOLLOWERS and not (episode/'native_result.json').is_file():
            if len(_FOLLOWERS) >= MAX_FOLLOWERS:
                raise FileNotFoundError('Live 3D is busy on this host (too many attempts are being watched). Try again later.')
            LIVE_ROOT.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(LIVE_ROOT).free < MIN_FREE_BYTES:
                raise FileNotFoundError(f'Live 3D not started: the run host has less than {MIN_FREE_BYTES//1024**3} GB of free disk.')
            out.mkdir(parents=True, exist_ok=True)
            _FOLLOWERS[out] = (start or _start_follower)(episode, out)
    if out.is_dir() and not out.is_symlink():
        (out/'wanted').touch()
    waited = 0.0
    while not (out/'scene.json').is_file():
        proc = _FOLLOWERS.get(out)
        if waited >= wait or proc is None or proc.poll() is not None:
            raise FileNotFoundError('This attempt has ended.' if (episode/'native_result.json').is_file()
                                    else 'The robot has not taken its first step yet.' if proc is not None and proc.poll() is None
                                    else 'Live 3D could not start for this attempt.')
        sleep(0.25);waited += 0.25
    return out


def live_file(cell, name, start=None):
    """Content and type of one live file of the attempt this cell is running now (display only)."""
    match = LIVE.fullmatch(name)
    if not match:raise FileNotFoundError('Not a 3D view file')
    if cell.get('state') != 'running' or not cell.get('active_episode'):
        raise FileNotFoundError('No attempt is running right now.')
    episode, root = Path(cell['active_episode']).resolve(), Path(cell['artifact_dir']).resolve()
    if not episode.is_relative_to(root) or not episode.is_dir() or not re.fullmatch(r'e\d+', episode.parent.name):
        raise FileNotFoundError('3D view unavailable')
    out = LIVE_ROOT/cell['key']/f'{episode.parent.name}-{episode.name}'
    live_folder(episode, out, start)
    if match[2] is not None:
        # poses.bin from frame <n> on; the page asks only for frames it does not have yet.
        try:
            data = (out/'poses.bin').read_bytes()
            bodies = len(json.loads((out/'scene.json').read_bytes())['moving_bodies'])
        except (OSError, ValueError, KeyError) as exc:
            raise FileNotFoundError('3D view unavailable') from exc
        return data[int(match[2])*bodies*28:], 'application/octet-stream'
    path = out/match[1]
    if path.is_symlink() or not path.is_file():raise FileNotFoundError('3D view unavailable')
    return path, LIVE_TYPES[match[1]]
