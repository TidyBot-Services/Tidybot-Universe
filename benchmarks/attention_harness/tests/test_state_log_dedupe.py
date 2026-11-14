"""Old state logs: conservative inventory, guarded atomic dedupe, unchanged catalog hashes."""
import hashlib
import json
import os
from pathlib import Path

import pytest
from scripts.attentionbench import state_log_dedupe as dedupe
from benchmarks.attention_harness.dev_agent_workspace import read_beneath, shared_name


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    root=tmp_path/'devexp-old';agent=root/'cells/cell/agent'
    canonical=agent/'private/public/executions/e000001/state_log.json'
    raw=b'[{"physics_step":1,"robot":[0.1,0.2]}]\n';sha=hashlib.sha256(raw).hexdigest()
    copies=[canonical,agent/'ws/executions/e000001/state_log.json',agent/'private/snapshots/objects'/sha,
            agent/'private/eval/v0001/ws/state_log.json']
    for path in copies:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    manifest=agent/'private/snapshots/post-exec-v1.json'
    manifest.write_text(json.dumps(dict(files={'executions/e000001/state_log.json':dict(sha256=sha,bytes=len(raw))})))
    monkeypatch.setattr(dedupe,'state',lambda root:dict(terminal=True,paused=False,active=False))
    return root,copies,raw,sha


def inode(path):return path.stat().st_dev,path.stat().st_ino


def test_dry_run_counts_inodes_and_never_mutates(legacy, tmp_path):
    root,copies,raw,sha=legacy
    alias=copies[-1].parent.parent.parent/'v0002/ws/state_log.json';alias.parent.mkdir(parents=True);os.link(copies[-1],alias)
    before=[(inode(p),p.stat().st_mode,p.read_bytes()) for p in copies]
    assert dedupe.inventory_run(root)['bytes_reclaimable']==3*len(raw)
    assert [(inode(p),p.stat().st_mode,p.read_bytes()) for p in copies]==before
    assert not (root/'state-log-dedupe.json').exists()


def test_apply_preserves_all_bytes_and_memory_catalog_rehash(legacy):
    from attention_memory_service.dev_v2 import DevV2Evidence
    root,copies,raw,sha=legacy
    verifier=object.__new__(DevV2Evidence)
    catalog=dict(root=str(root),role='source',files={str(p.relative_to(root)):sha for p in copies})
    for name,expected in catalog['files'].items():verifier.verify_file_for_catalog(catalog,name,expected)
    receipt=dedupe.apply_run(root)
    assert receipt['status']=='completed' and len(receipt['files'])==3
    assert len({inode(p) for p in copies})==1
    assert all(p.stat().st_mode & 0o777==0o444 for p in copies)
    for name,expected in catalog['files'].items():
        verifier.verify_file_for_catalog(catalog,name,expected)
        assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected
    assert all(p.read_bytes()==raw for p in copies)
    assert dedupe.inventory_run(root)['bytes_reclaimable']==0
    assert dedupe.apply_run(root)['status']=='completed'


@pytest.mark.parametrize('field',['terminal','paused','active'])
def test_guard_refuses_and_batch_continues(legacy,tmp_path,monkeypatch,field):
    root,copies,raw,sha=legacy
    bad=tmp_path/'devexp-bad';bad.mkdir()
    state=dict(terminal=True,paused=False,active=False);state[field]=field!='terminal'
    monkeypatch.setattr(dedupe,'state',lambda path:state if path==bad else dict(terminal=True,paused=False,active=False))
    result=dedupe.apply_batch([bad,root])
    assert result[0]['status'].startswith('refused:') and result[1]['status']=='completed'
    assert not (bad/'state-log-dedupe.json').exists()


def test_different_bytes_and_symlinks_are_never_replaced(legacy,tmp_path):
    root,copies,raw,sha=legacy
    copies[-1].write_bytes(b'X'*len(raw))
    dedupe.apply_run(root)
    assert copies[-1].read_bytes()==b'X'*len(raw) and inode(copies[-1])!=inode(copies[0])
    copies[1].unlink();copies[1].symlink_to(copies[-1])
    with pytest.raises(ValueError,match='symlink'):dedupe.apply_run(root)
    assert copies[1].is_symlink()


def test_race_changes_duplicate_and_refuses_replace(legacy,monkeypatch):
    root,copies,raw,sha=legacy
    original=os.link
    def link(source,target,**kw):
        original(source,target,**kw)
        copies[1].write_bytes(b'changed during link')
    monkeypatch.setattr(dedupe.os,'link',link)
    with pytest.raises(ValueError,match='sha/inode changed'):dedupe.apply_run(root)
    assert copies[1].read_bytes()==b'changed during link'
    assert copies[0].read_bytes()==raw
    assert not list(root.rglob('.state-log-link-*'))
    assert json.loads((root/'state-log-dedupe.json').read_bytes())['status']=='error'


def test_inventory_does_not_claim_blocks_with_external_links(legacy,tmp_path):
    root,copies,raw,sha=legacy
    os.link(copies[1],tmp_path/'outside.json')
    assert dedupe.inventory_run(root)['bytes_reclaimable']==2*len(raw)


def test_nonshared_snapshot_alias_retains_private_object(legacy):
    root,copies,raw,sha=legacy
    manifest=copies[2].parent.parent/'post-exec-v1.json';value=json.loads(manifest.read_bytes())
    value['files']['task.json']=dict(sha256=sha,bytes=len(raw));manifest.write_text(json.dumps(value))
    dedupe.apply_run(root)
    assert inode(copies[2])!=inode(copies[0])
    assert read_beneath(copies[2].parent,sha)==raw


def test_cli_requires_approved_list_and_limits_apply(legacy,tmp_path,capsys):
    root,copies,raw,sha=legacy
    dedupe.main(['--runs-root',str(tmp_path)])
    assert json.loads(capsys.readouterr().out)['runs'][0]['bytes_reclaimable']==3*len(raw)
    with pytest.raises(SystemExit):dedupe.main(['--runs-root',str(tmp_path),'--apply'])
    approved=tmp_path/'approved.json';approved.write_text(json.dumps([root.name]))
    dedupe.main(['--runs-root',str(tmp_path),'--apply','--runs',str(approved)])
    assert json.loads(capsys.readouterr().out)['applied'][0]['status']=='completed'
