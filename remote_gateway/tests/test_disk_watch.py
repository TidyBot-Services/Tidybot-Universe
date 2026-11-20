from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from remote_gateway.deploy import disk_watch as watch


@pytest.mark.parametrize('free,low,stop', [(131,False,False),(130,False,False),(129,True,False),(60,True,False),(59,True,True)])
def test_thresholds_and_stop_latch(tmp_path, free, low, stop):
    usage = lambda path: SimpleNamespace(free=free*watch.GIB)
    value=watch.check(tmp_path,usage=usage)
    assert value['low'] is low and value['stop'] is stop
    assert (tmp_path/'disk-low.json').exists() is low
    assert (tmp_path/'stop-new-runs').exists() is stop
    if low:assert json.loads((tmp_path/'disk-low.json').read_text())['free_bytes']==free*watch.GIB
    watch.check(tmp_path,usage=lambda path:SimpleNamespace(free=200*watch.GIB))
    assert not (tmp_path/'disk-low.json').exists()
    assert (tmp_path/'stop-new-runs').exists() is stop


def test_dry_run_no_writes_and_configured_paths(tmp_path,monkeypatch):
    data=tmp_path/'missing'
    value=watch.check(data,disk=tmp_path,usage=lambda p:SimpleNamespace(free=0),dry_run=True)
    assert value['stop'] and not data.exists()
    monkeypatch.setenv('ATTENTIONBENCH_DATA',str(data))
    assert watch.default_data()==data
    with pytest.raises(ValueError):watch.check(data,low_bytes=1,stop_bytes=2)


def test_units_are_user_units_and_half_hour():
    folder=Path(watch.__file__).parent/'systemd'
    assert 'OnUnitActiveSec=30min' in (folder/'ab-disk-watch.timer').read_text()
    assert 'User=' not in (folder/'ab-disk-watch.service').read_text()
