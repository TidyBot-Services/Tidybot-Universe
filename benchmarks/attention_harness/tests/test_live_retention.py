import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openarm365_dev_v2.live_retention import close_execution,prune_live
from openarm365_dev_v2.owner import NativeOwnerV2
from openarm365_dev.owner import NativeOwner

def frames(root,latest=7):
    live=root/'live';live.mkdir()
    for i in range(8):(live/f'frame-{i:06d}.jpg').write_bytes(b'jpg')
    (live/'latest.json').write_text(json.dumps(dict(file=f'frame-{latest:06d}.jpg')))
    (live/'views').mkdir();(live/'views/frame-000000.jpg').write_bytes(b'view')
    (live/'phase.json').write_text('{}')
    return live

@pytest.mark.parametrize('ending',['success','failure','timeout','cancel','exception','cleanup_exception'])
def test_worker_finally_prunes_all_end_paths(tmp_path,ending):
    live=frames(tmp_path);cleanup={};closed=[]
    def close():
        if ending=='cleanup_exception':raise RuntimeError(ending)
        return {}
    try:
        try:
            if ending!='success':raise RuntimeError(ending)
        finally:close_execution(tmp_path,SimpleNamespace(close=close),None,SimpleNamespace(close=lambda:closed.append(True)),cleanup)
    except RuntimeError:pass
    assert closed==[True] and cleanup['live_pruning']['errors']==[]
    assert sorted(p.name for p in live.glob('frame-*.jpg'))==[f'frame-{i:06d}.jpg' for i in (5,6,7)]
    assert (live/'latest.json').exists() and (live/'views/frame-000000.jpg').exists() and (live/'phase.json').exists()

@pytest.mark.parametrize('reason',['normal_cleanup','operator_cancel','timeout','forced_kill'])
def test_owner_fallback_prunes_even_if_worker_never_cleans_up(tmp_path,monkeypatch,reason):
    live=frames(tmp_path)
    owner=object.__new__(NativeOwnerV2);owner.evidence=tmp_path;owner.cleanup={}
    monkeypatch.setattr(NativeOwner,'stop',lambda self,reason:self.cleanup)
    result=owner.stop(reason)
    assert len(list(live.glob('frame-*.jpg')))==3 and result['live_pruning']['errors']==[]

def test_latest_frame_is_kept_even_if_not_numerically_newest(tmp_path):
    live=frames(tmp_path,latest=1)
    assert prune_live(tmp_path)['errors']==[]
    assert {p.name for p in live.glob('frame-*.jpg')}=={'frame-000001.jpg','frame-000006.jpg','frame-000007.jpg'}

def test_pruning_errors_are_durable_visible_and_do_not_fail_cleanup(tmp_path,monkeypatch,caplog):
    live=frames(tmp_path);unlink=Path.unlink
    def fail(path,*a,**kw):
        if path.name=='frame-000000.jpg':raise PermissionError('denied')
        return unlink(path,*a,**kw)
    monkeypatch.setattr(Path,'unlink',fail)
    cleanup={};close_execution(tmp_path,None,None,None,cleanup)
    assert 'denied' in caplog.text and cleanup['live_pruning']['errors']
    assert json.loads((tmp_path/'live-pruning.json').read_bytes())['errors']
    assert (live/'frame-000007.jpg').exists()
    monkeypatch.setattr(Path,'unlink',unlink)
    assert prune_live(tmp_path)['errors']  # A later owner cleanup keeps the diagnostic.

def test_bad_latest_metadata_is_reported_without_deleting_frames(tmp_path,caplog):
    live=frames(tmp_path);(live/'latest.json').write_text('bad json')
    assert prune_live(tmp_path)['errors'] and 'Live frame pruning failed' in caplog.text
    assert len(list(live.glob('frame-*.jpg')))==8
