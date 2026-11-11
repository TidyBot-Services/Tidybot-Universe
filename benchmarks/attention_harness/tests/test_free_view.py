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
    assert "get('geom.bin.gz'" in page and "get('poses.bin.gz'" in page and 'free-view\\/e\\d+$/.test(base)' in page
    assert 'method:' not in page and 'XMLHttpRequest' not in page and 'WebSocket' not in page  # GET only
