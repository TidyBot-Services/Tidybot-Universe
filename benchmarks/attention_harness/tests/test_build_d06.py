import json
from pathlib import Path
import subprocess
import pytest
from scripts import build_d06 as b


@pytest.fixture
def tree(tmp_path, monkeypatch):
    repo = tmp_path/'repo'; repo.mkdir()
    subprocess.run(['git','init','-q',str(repo)],check=True)
    parent = tmp_path/'deployment-05'; project = parent/'runtime/project'; project.mkdir(parents=True)
    for name, raw in {'curobo_v2/module.py':'old', 'curobo_v2/same.py':'same', **{n:str(parent)+'/runtime/project' for n in b.RELOCATED_YAMLS}}.items():
        p = project/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(raw)
        p = repo/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text('source-yaml' if name in b.RELOCATED_YAMLS else raw)
    (repo/'curobo_v2/module.py').write_text('new')
    (repo/'curobo_v2/new.py').write_text('new module')
    subprocess.run(['git','add','.'],cwd=repo,check=True)
    lock = dict(schema='attentionbench.curobo-v2.runtime.v1',generation=2,
                project_root=str(project),service_root=str(project/'service'),assets_root=str(project/'assets'),
                library_revision=b.OFFICIAL_COMMIT,official_only=True,
                robot_configs={str(project/b.RELOCATED_YAMLS[0]): b.sha(project/b.RELOCATED_YAMLS[0])},task_sources={},
                files={str(p):b.sha(p) for p in b.real_files(project)},
                provenance=dict(parent_lock_sha256=b.OFFICIAL_SHA,parent_lock_path='/official/runtime-lock.json',official_provenance={'unchanged':True}))
    b.write_json(parent/'runtime-lock.json', lock); b.write_json(parent/'adapter-changes.json', {})
    monkeypatch.setattr(b, 'PARENT_SHA', b.sha(parent/'runtime-lock.json'))
    out = tmp_path/'deployment-06'
    yield repo,parent,out
    # Pytest must be able to clean the intentionally immutable output.
    if out.exists():
        for p in [out,*out.rglob('*')]: p.chmod(p.stat().st_mode | 0o700)


def build(tree, **kwargs):
    repo,parent,out = tree
    return b.build(out,parent=parent,repo=repo,expected_parent_sha=b.sha(parent/'runtime-lock.json'),**kwargs)


def test_build_manifest_readonly_verify_and_registration(tree):
    repo,parent,out = tree
    registration = build(tree)
    manifest = json.loads((out/'source-manifest.json').read_bytes())
    assert set(manifest) == {'curobo_v2/module.py','curobo_v2/new.py'}
    assert b.verify_against(out,repo)['verified_files'] == 2
    for p in [out,*out.rglob('*')]: assert p.stat().st_mode & 0o222 == 0
    for name in b.RELOCATED_YAMLS:
        assert (out/'runtime/project'/name).read_bytes() == (parent/'runtime/project'/name).read_bytes().replace(str(parent).encode(),str(out).encode())
    entry = next(iter(registration.values()))
    assert entry['allowed_tiers'] == ['dev'] and entry['parent_sha256'] == b.OFFICIAL_SHA
    with pytest.raises(FileExistsError): build(tree)
    registry = repo/'curobo_v2/trusted_deployments.json'
    raw = b'{\n  "existing" : { "preserved" : true }\n}\n'; registry.write_bytes(raw)
    receipt = repo/'receipt.json'
    record = b.register(out,registry=registry,receipt=receipt)
    assert registry.read_bytes().startswith(raw[:raw.rfind(b'\n}')])
    assert record['after_sha256'] == b.sha(registry)
    assert json.loads(receipt.read_bytes()) == record
    assert json.loads(registry.read_bytes())['existing'] == {'preserved':True}
    (repo/'curobo_v2/module.py').write_text('tampered')
    with pytest.raises(ValueError,match='manifest mismatch'): b.verify_against(out,repo)


def test_manifest_mismatch_refused_before_output(tree):
    with pytest.raises(ValueError,match='manifest'): build(tree,manifest={'curobo_v2/module.py':'wrong'})
    assert not tree[2].exists()


def test_parent_digest_and_symlink_refused(tree):
    repo,parent,out=tree
    (parent/'runtime/project/curobo_v2/same.py').write_text('tamper')
    with pytest.raises(ValueError,match='inventory'): build(tree)
    assert not out.exists()


def test_missing_and_linked_source_refused(tree):
    repo,parent,out=tree
    p=repo/'curobo_v2/module.py'; p.unlink(); p.symlink_to(repo/'curobo_v2/same.py')
    with pytest.raises(ValueError,match='Unsafe source'): build(tree)
    assert not out.exists()
