"""3D replay route: built once, for people only, never inside what Dev can read."""
import gzip, os, time
from pathlib import Path

import pytest

from benchmarks.attention_harness import dev_agent_view, free_view

EID = 'e000003'


def make_cell(tmp_path, sources=free_view.SOURCES, video=True):
    episode = tmp_path/'cell/agent/private/executions'/EID/'openarm365-dev-abc123'
    episode.mkdir(parents=True)
    for name in sources:(episode/name).write_text('{}')
    if video:(episode/'advisor_replay.mp4').write_bytes(b'video')
    (tmp_path/'cell/agent/ws').mkdir()
    (tmp_path/'cell/agent/private/public').mkdir()
    return dict(dev_mode='agent', artifact_dir=str(tmp_path/'cell')), episode


def exporter(calls, problem=None):
    def export(episode, out):
        calls.append(episode)
        if problem:return problem
        (out/'scene.json').write_text('{"format":"test"}')
        (out/'geom.bin').write_bytes(b'G'*5000)
        (out/'poses.bin').write_bytes(b'P'*3000)
    return export


@pytest.fixture
def calls(monkeypatch):
    made = []
    monkeypatch.setattr(free_view, '_export', exporter(made))
    monkeypatch.setattr(free_view, 'MIN_FREE_BYTES', 0)
    return made


def test_first_request_builds_once_and_later_ones_read(tmp_path, calls):
    cell, episode = make_cell(tmp_path)
    path, kind = dev_agent_view.artifact(cell, f'free-view/{EID}/geom.bin.gz')
    assert kind == 'application/octet-stream' and gzip.decompress(path.read_bytes()) == b'G'*5000
    assert gzip.decompress(dev_agent_view.artifact(cell, f'free-view/{EID}/poses.bin.gz')[0].read_bytes()) == b'P'*3000
    scene, kind = dev_agent_view.artifact(cell, f'free-view/{EID}/scene.json')
    assert kind == 'application/json' and scene.read_text() == '{"format":"test"}'
    assert calls == [episode]
    folder = episode/free_view.FOLDER
    assert sorted(p.name for p in folder.iterdir()) == ['geom.bin.gz', 'poses.bin.gz', 'scene.json']
    assert not folder.with_name(folder.name+'.tmp').exists()


def test_files_stay_outside_everything_dev_can_read(tmp_path, calls):
    cell, episode = make_cell(tmp_path)
    path, _ = dev_agent_view.artifact(cell, f'free-view/{EID}/scene.json')
    agent = Path(cell['artifact_dir'])/'agent'
    assert path.is_relative_to(agent/'private/executions')
    # Dev's workspace and the public copies mirrored into it get nothing.
    assert list((agent/'ws').rglob('*')) == [] and list((agent/'private/public').rglob('*')) == []
    # The public-copy route does not know these names, so they cannot be read as Dev evidence either.
    for name in ('scene.json', 'geom.bin.gz', 'poses.bin.gz'):
        assert not dev_agent_view._public_name(f'free-view/{EID}/{name}')
        assert not dev_agent_view._public_name(f'executions/{EID}/{free_view.FOLDER}/{name}')


@pytest.mark.parametrize('name', [
    'free-view/e000003/geom.bin', 'free-view/e000003/native-physics.jsonl', 'free-view/e000003/scene.xml', 'free-view/e000003/../scene.json',
    'free-view/../e000003/scene.json', 'free-view/e000003/sub/scene.json', 'free-view/x/scene.json', 'free-view/e000003/', 'free-view/e000003/scene.json/x'])
def test_only_the_three_files_have_names(tmp_path, calls, name):
    cell, _ = make_cell(tmp_path)
    with pytest.raises(FileNotFoundError):dev_agent_view.artifact(cell, name)
    assert calls == []


def test_unfinished_or_unknown_attempt_is_not_built(tmp_path, calls):
    cell, _ = make_cell(tmp_path, video=False)
    with pytest.raises(FileNotFoundError):dev_agent_view.artifact(cell, f'free-view/{EID}/scene.json')
    with pytest.raises(FileNotFoundError):dev_agent_view.artifact(cell, 'free-view/e000099/scene.json')
    with pytest.raises(FileNotFoundError):dev_agent_view.artifact(dict(cell, dev_mode='one_call'), f'free-view/{EID}/scene.json')
    assert calls == []


def test_missing_log_says_which_file(tmp_path, calls):
    cell, _ = make_cell(tmp_path, sources=('scene.xml', 'reset_receipt.json', 'rpc-receipts.jsonl'))
    with pytest.raises(FileNotFoundError, match='native-physics.jsonl'):dev_agent_view.artifact(cell, f'free-view/{EID}/scene.json')
    assert calls == []


def test_failed_build_is_reported_and_not_repeated_at_once(tmp_path, monkeypatch):
    made = []
    monkeypatch.setattr(free_view, '_export', exporter(made, 'ValueError: mesh file missing'))
    monkeypatch.setattr(free_view, 'MIN_FREE_BYTES', 0)
    cell, episode = make_cell(tmp_path)
    for _ in range(3):
        with pytest.raises(FileNotFoundError, match='mesh file missing'):dev_agent_view.artifact(cell, f'free-view/{EID}/scene.json')
    assert len(made) == 1 and not (episode/free_view.FOLDER).exists() and not (episode/(free_view.FOLDER+'.tmp')).exists()
    # After the wait a new try is made, and a good build clears the note.
    note = episode/(free_view.FOLDER+'.error.txt')
    old = time.time()-free_view.RETRY_SECONDS-5;os.utime(note, (old, old))
    monkeypatch.setattr(free_view, '_export', exporter(made))
    assert dev_agent_view.artifact(cell, f'free-view/{EID}/scene.json')[0].is_file()
    assert len(made) == 2 and not note.exists()


def test_low_disk_refuses_to_build(tmp_path, monkeypatch):
    made = []
    monkeypatch.setattr(free_view, '_export', exporter(made))
    monkeypatch.setattr(free_view, 'MIN_FREE_BYTES', 10**18)
    cell, _ = make_cell(tmp_path)
    with pytest.raises(FileNotFoundError, match='free disk'):dev_agent_view.artifact(cell, f'free-view/{EID}/scene.json')
    assert made == []


def test_a_linked_folder_is_refused(tmp_path, calls):
    cell, episode = make_cell(tmp_path)
    elsewhere = tmp_path/'elsewhere';elsewhere.mkdir();(elsewhere/'scene.json').write_text('{}')
    (episode/free_view.FOLDER).symlink_to(elsewhere)
    with pytest.raises(FileNotFoundError):dev_agent_view.artifact(cell, f'free-view/{EID}/scene.json')


def test_run_page_and_viewer_use_the_route():
    ui = Path(free_view.__file__).with_name('ui')
    assert '/agent/files/free-view/${m[2]}' in (ui/'live.js').read_text()
    page = (ui/'free_view.html').read_text()
    assert "get('geom.bin.gz'" in page and "get('poses.bin.gz'" in page and 'free-view\\/(e\\d+|live)$/.test(base)' in page
    assert '/agent/files/free-view/live' in (ui/'live.js').read_text() and "'/poses-' + frames + '.bin'" in page
    assert 'method:' not in page and 'XMLHttpRequest' not in page and 'WebSocket' not in page  # GET only


# ---------------------------------------------------------------- live
class Proc:
    def __init__(self):self.code = None
    def poll(self):return self.code


def follower(started):
    """Stands in for free_view_export.py --live: writes what it would write, into `out` only."""
    def start(episode, out):
        started.append((episode, out))
        (out/'scene.json').write_text('{"moving_bodies":["a","b"]}')
        (out/'geom.bin.gz').write_bytes(gzip.compress(b'G'*100))
        (out/'poses.bin').write_bytes(bytes(range(3*2*28)))
        (out/'status.json').write_text('{"frames":3}')
        return Proc()
    return start


@pytest.fixture
def running(tmp_path, monkeypatch):
    monkeypatch.setattr(free_view, 'LIVE_ROOT', tmp_path/'cache')
    monkeypatch.setattr(free_view, 'MIN_FREE_BYTES', 0)
    monkeypatch.setattr(free_view, '_FOLLOWERS', {})
    started = []
    monkeypatch.setattr(free_view, '_start_follower', follower(started))
    cell, episode = make_cell(tmp_path, video=False)
    return dict(cell, key='k'*64, state='running', active_episode=str(episode)), episode, started


def test_live_starts_one_follower_and_serves_new_frames_only(tmp_path, running):
    cell, episode, started = running
    path, kind = dev_agent_view.artifact(cell, 'free-view/live/scene.json')
    assert kind == 'application/json' and path.read_text() == '{"moving_bodies":["a","b"]}'
    assert dev_agent_view.artifact(cell, 'free-view/live/status.json')[0].read_text() == '{"frames":3}'
    assert dev_agent_view.artifact(cell, 'free-view/live/poses-0.bin')[0] == bytes(range(168))
    assert dev_agent_view.artifact(cell, 'free-view/live/poses-2.bin')[0] == bytes(range(112, 168))
    assert dev_agent_view.artifact(cell, 'free-view/live/poses-3.bin')[0] == b''
    assert len(started) == 1 and started[0][0] == episode
    # The live files sit in the cache, not in the run store, and are marked as watched.
    out = started[0][1]
    assert out.is_relative_to(tmp_path/'cache') and not out.is_relative_to(tmp_path/'cell') and (out/'wanted').is_file()
    assert list((tmp_path/'cell/agent/ws').rglob('*')) == []
    assert not dev_agent_view._public_name('free-view/live/scene.json')


def test_live_needs_a_running_attempt(tmp_path, running):
    cell, episode, started = running
    for other in (dict(cell, state='completed'), dict(cell, state='stopped'), dict(cell, active_episode=str(tmp_path))):
        with pytest.raises(FileNotFoundError):dev_agent_view.artifact(other, 'free-view/live/scene.json')
    (episode/'native_result.json').write_text('{}')  # ended, nothing left running for it
    with pytest.raises(FileNotFoundError, match='ended'):dev_agent_view.artifact(cell, 'free-view/live/scene.json')
    assert started == []


@pytest.mark.parametrize('name', ['free-view/live/poses.bin', 'free-view/live/poses-x.bin', 'free-view/live/wanted', 'free-view/live/../scene.json',
                                  'free-view/live/geom.bin', 'free-view/live/scene.json/x', 'free-view/live/poses-12345678.bin'])
def test_live_has_only_its_own_names(running, name):
    cell, _, started = running
    with pytest.raises(FileNotFoundError):dev_agent_view.artifact(cell, name)
    assert started == []


def test_live_followers_are_capped(tmp_path, running, monkeypatch):
    cell, _, started = running
    monkeypatch.setattr(free_view, '_FOLLOWERS', {tmp_path/str(i): Proc() for i in range(free_view.MAX_FOLLOWERS)})
    with pytest.raises(FileNotFoundError, match='busy'):dev_agent_view.artifact(cell, 'free-view/live/scene.json')
    assert started == []


# ---------------------------------------------------------------- 11-12 user reports
def test_live_3d_button_is_offered_on_every_camera_view():
    """Other camera views poll .../live?view=<name>; the live 3D button must match those too, not only Overview."""
    import re as _re
    js = (Path(__file__).resolve().parents[1] / 'ui' / 'live.js').read_text(encoding='utf-8')
    body = js[js.index('function liveFreeViewButton'):][:900]
    start = body.index('station.camera_monitor_v2&&/') + len('station.camera_monitor_v2&&/')
    rx = _re.compile(body[start:body.index('/.exec(station.camera_state_url')].replace('\\/', '/'))
    cell = '/api/dev-v2/experiments/devexp-' + 'a' * 24 + '/cells/' + 'b' * 64
    for url in (cell + '/live', cell + '/live?view=side', cell + '/live?view=left_wrist'):
        assert rx.match(url), url


def test_close_stays_above_the_loading_and_waiting_screen():
    """While the page shows LOADING / WAITING / NO 3D VIEW, the Close button must stay clickable."""
    import re as _re
    html = (Path(__file__).resolve().parents[1] / 'ui' / 'free_view.html').read_text(encoding='utf-8')
    z = lambda sel: int(_re.search(_re.escape(sel) + r'\{[^}]*z-index:(\d+)', html)[1])
    assert z('#close') > z('#loading')


def test_live_finds_the_running_try_of_an_agent_run(tmp_path, running):
    """Agent runs do not fill active_episode; the try in progress is the newest one without a public result."""
    cell, episode, started = running
    agent = dict(cell, active_episode=None)
    for name in ('scene.xml', 'reset_receipt.json'):(episode/name).write_text('{}')
    assert dev_agent_view.artifact(agent, 'free-view/live/status.json')[0].read_text() == '{"frames":3}'
    assert started[0][0] == episode
    # Once the try has its public result it is no longer the running one.
    result = tmp_path/'cell/agent/private/public/executions'/EID/'result.json'
    result.parent.mkdir(parents=True);result.write_text('{}')
    with pytest.raises(FileNotFoundError, match='No attempt is running'):dev_agent_view.artifact(agent, 'free-view/live/scene.json')


def test_live_waits_until_the_try_has_its_scene(tmp_path, running):
    cell, episode, started = running
    (episode/'scene.xml').unlink()
    with pytest.raises(FileNotFoundError, match='first step'):dev_agent_view.artifact(dict(cell, active_episode=None), 'free-view/live/scene.json')
    assert started == []
