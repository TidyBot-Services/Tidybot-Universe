"""Behavior applicability without deploying a planner or starting a simulator."""
import hashlib
import json
from pathlib import Path

import pytest

from curobo_v2 import planner_behavior as behavior
from attention_memory_service.applicability import (BEHAVIOR_KEY, DEV_LOCAL_KEY,
    profile_matches, require_profile_match)
from attention_memory_service.core.store import StateConflictError
from benchmarks.attention_harness.dev_planner_memory import planner_matches


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def deployment(tmp_path):
    root = tmp_path / 'snapshot/runtime/project'
    values = {
        'curobo_v2/session.py': "from . import client\ncommand=['python','-B','curobo_v2/worker.py']\n",
        'curobo_v2/client.py': "from scripts.runtime import limit\n",
        'scripts/runtime.py': 'limit=1\n',
        'scripts/unrelated.py': 'unrelated=1\n',
        'curobo_v2/native_session.py': '# native owner\n',
        'curobo_v2/worker.py': 'from curobo_service import planner\n',
        'curobo_v2/service/curobo_service/planner.py': "config='config/robot.yml'\n",
        'curobo_v2/unloaded.py': 'unloaded=1\n',
        'openarm365_dev/runner.py': 'from . import owner\nfrom .sandbox import limit\n',
        'openarm365_dev/sandbox.py': "from pathlib import Path\nworker=Path(__file__).with_name('policy_worker.py')\nlimit=1\n",
        'openarm365_dev/policy_worker.py': '# isolated policy worker\n',
        'openarm365_dev_v2/runner.py': 'from . import owner, agent_sdk\n',
        'openarm365_dev/owner.py': "command=['python','-m','openarm365_dev.worker']\n",
        'openarm365_dev/worker.py': 'from openarm365_dev.executor import move\n',
        'openarm365_dev/executor.py': 'move=1\n',
        'openarm365_dev_v2/owner.py': '# owner\n',
        'openarm365_dev_v2/agent_sdk.py': 'from openarm365_dev.executor import move\n',
        'config/robot.yml': 'model: robot.urdf\n',
        'config/robot.urdf': '<robot><mesh filename="assets/model.stl"/></robot>\n',
        'config/assets/model.stl': 'solid model\n',
        'ui/page.html': '<p>UI</p>',
        'docs/readme.md': 'docs',
        'curobo_v2/tests/test_runtime.py': 'test=1\n',
        'benchmarks/attention_harness/ui_server.py': 'ui=1\n',
    }
    for name, text in values.items():
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
    lock=dict(project_root=str(root), assets_root=str(root/'config/assets'),
        provenance=dict(base_deployment_lock_sha256='b'*64),
        robot_configs={str(root/'config/robot.yml'):digest((root/'config/robot.yml').read_bytes())})
    settings=dict(profile='r4-test',base_hold_drift_m=.003,
                  planner_deployment=dict(lock_path='/audit/runtime-lock.json',sha256='a'*64))
    return root,lock,settings


def measured(deployment):
    root,lock,settings=deployment
    # Represent a newly sealed whole-repo snapshot after each edit.
    lock['files']={str(p):digest(p.read_bytes()) for p in root.rglob('*') if p.is_file()}
    inventory={str(p.relative_to(root)):h for p,h in ((Path(p),h) for p,h in lock['files'].items())}
    value=behavior.manifest(lock,settings,client_root=root,client_files=inventory)
    return digest(behavior.canonical(value)),value


def scoped(sha,snapshot='a'*64):
    return dict(execution_profile='r4-test',planner_behavior_sha256=sha,planner_dev_local_sha256=snapshot)


def test_derivation_follows_imports_workers_scripts_and_resources(deployment):
    _,manifest=measured(deployment)
    files=manifest['files']
    for name in ('curobo_v2/client.py','curobo_v2/worker.py',
                 'curobo_v2/service/curobo_service/planner.py','scripts/runtime.py',
                 'openarm365_dev/worker.py','openarm365_dev/executor.py',
                 'openarm365_dev/policy_worker.py',
                 'config/robot.yml','config/robot.urdf','config/assets/model.stl'):
        assert 'client/'+name in files
    for name in ('scripts/unrelated.py','curobo_v2/unloaded.py','ui/page.html',
                 'docs/readme.md','curobo_v2/tests/test_runtime.py','benchmarks/attention_harness/ui_server.py'):
        assert all(not key.endswith('/'+name) for key in files)


@pytest.mark.parametrize('name',['ui/page.html','docs/readme.md','curobo_v2/tests/test_runtime.py',
    'benchmarks/attention_harness/ui_server.py'])
def test_unrelated_edit_preserves_digest_and_applicability(deployment,name):
    before,_=measured(deployment)
    path=deployment[0]/name;path.write_text(path.read_text()+'\n# edit\n')
    after,_=measured(deployment)
    assert before==after
    note,context=scoped(before),scoped(after,'c'*64)
    assert planner_matches(note,context) and profile_matches(note,context)
    require_profile_match(note,context,for_reuse=True)
    # The same digest never relaxes validation snapshot equality.
    with pytest.raises(StateConflictError,match=DEV_LOCAL_KEY):
        require_profile_match(note,context)


@pytest.mark.parametrize('name',['curobo_v2/client.py','openarm365_dev/executor.py','config/assets/model.stl'])
def test_runtime_edit_changes_digest_and_rejects_visibly(deployment,name):
    before,manifest=measured(deployment)
    assert 'client/'+name in manifest['files']
    path=deployment[0]/name;path.write_text(path.read_text()+'\n# behavior edit\n')
    after,_=measured(deployment)
    assert before!=after
    note,context=scoped(before),scoped(after,'c'*64)
    assert not planner_matches(note,context) and not profile_matches(note,context)
    with pytest.raises(StateConflictError,match=BEHAVIOR_KEY):
        require_profile_match(note,context,for_reuse=True)


def test_legacy_keeps_exact_snapshot_match(deployment):
    sha,_=measured(deployment)
    note=scoped(sha);note.pop(BEHAVIOR_KEY)
    assert planner_matches(note,scoped('d'*64)) and profile_matches(note,scoped('d'*64))
    other=scoped(sha,'c'*64)
    assert not planner_matches(note,other) and not profile_matches(note,other)
    with pytest.raises(StateConflictError,match=DEV_LOCAL_KEY):
        require_profile_match(note,other,for_reuse=True)


def test_settings_and_base_lock_are_bound_but_snapshot_pin_is_audit(deployment):
    before,_=measured(deployment)
    deployment[2]['planner_deployment']['sha256']='c'*64
    assert measured(deployment)[0]==before
    deployment[2]['base_hold_drift_m']=.004
    assert measured(deployment)[0]!=before
    deployment[2]['base_hold_drift_m']=.003
    deployment[1]['provenance']['base_deployment_lock_sha256']='d'*64
    assert measured(deployment)[0]!=before


def test_new_dependency_is_discovered_without_changing_a_list(deployment):
    root,_,_=deployment
    before,_=measured(deployment)
    (root/'curobo_v2/new_runtime.py').write_text('value=1\n')
    (root/'curobo_v2/client.py').write_text('from .new_runtime import value\n')
    after,manifest=measured(deployment)
    assert before!=after and 'client/curobo_v2/new_runtime.py' in manifest['files']


def test_from_config_authenticates_locks_and_measures_client_runtime(deployment,tmp_path,monkeypatch):
    from curobo_v2 import identity
    root,lock,settings=deployment
    _,value=measured(deployment)
    client=dict(source_root=str(root),files=lock['files'],model_sources={})
    path=tmp_path/'dev-lock.json';path.write_bytes(behavior.canonical(client))
    config=dict(execution_settings=settings,dev_lock=dict(path=str(path),sha256=digest(path.read_bytes())))
    monkeypatch.setattr(identity,'load_lock',lambda *a,**kw:(lock,'a'*64))
    assert behavior.from_config(config)==digest(behavior.canonical(value))
    settings['planner_deployment']['sha256']='b'*64
    with pytest.raises(ValueError,match='deployment lock mismatch'):behavior.from_config(config)
    settings['planner_deployment']['sha256']='a'*64
    config['dev_lock']['sha256']='b'*64
    with pytest.raises(ValueError,match='client lock drift'):behavior.from_config(config)


def test_snapshot_relocation_is_not_behavior(deployment,tmp_path):
    import shutil
    root,lock,settings=deployment
    config=root/'config/robot.yml'
    config.write_text('model: '+str(root.parent.parent)+'/runtime/project/config/robot.urdf\n')
    before,value=measured(deployment)
    other=tmp_path/'different-snapshot/runtime/project'
    shutil.copytree(root,other)
    (other/'config/robot.yml').write_text(config.read_text().replace(str(root.parent.parent),str(other.parent.parent)))
    relocated=json.loads(json.dumps(lock,ensure_ascii=False).replace(str(root),str(other)))
    after,_=measured((other,relocated,settings))
    assert after==before


def test_frozen_runtime_drift_is_not_accepted_as_a_new_identity(deployment):
    root,lock,settings=deployment
    measured(deployment)
    (root/'curobo_v2/client.py').write_text('changed=True\n')
    with pytest.raises(ValueError,match='Planner behavior source drift'):
        behavior.manifest(lock,settings)


def test_layered_consumer_uses_behavior_and_preserves_legacy_fallback(monkeypatch):
    from types import SimpleNamespace
    from benchmarks.attention_harness import dev_planner_memory as memory
    context=scoped('e'*64,'b'*64)
    context.update(task_id='task',seed=1)
    monkeypatch.setattr(memory,'context',lambda *a:context)
    monkeypatch.setattr(memory,'memory_scope',lambda *a:{'level':'scene'})
    notes=[dict(memory_id='new',version=1,guidance='tip',status='trusted',
                applicability=dict(context,planner_dev_local_sha256='a'*64),
                match_level='same_scene',trusted_here=True)]
    gateway=SimpleNamespace(store_id=lambda:'test',retrieve_entries=lambda *a,**kw:notes)
    reader=memory.DevMemory(gateway,dict(store_id='test',namespace='test'),{},layered=True)
    assert [n.memory_id for n in reader.notes()]==['new']
    notes[0]['applicability'].pop('planner_behavior_sha256')
    assert reader.notes()==[]
    notes[0]['applicability']['planner_dev_local_sha256']='b'*64
    assert [n.memory_id for n in reader.notes()]==['new']
    notes[0]['applicability']['planner_behavior_sha256']='f'*64
    assert reader.notes()==[]


def test_new_nonlayered_note_keeps_sdk_implementation_for_audit(monkeypatch):
    from types import SimpleNamespace
    from benchmarks.attention_harness import dev_planner_memory as memory
    context=dict(scoped('e'*64,'b'*64),sdk_sha256='b'*64,sdk_interface_sha256='d'*64)
    monkeypatch.setattr(memory,'context',lambda *a:context)
    monkeypatch.setattr(memory,'memory_scope',lambda *a:{'level':'scene'})
    applicability=dict(context,planner_dev_local_sha256='a'*64,sdk_sha256='a'*64)
    note=SimpleNamespace(memory_id='new',version=1,status='trusted',applicability=applicability)
    gateway=SimpleNamespace(store_id=lambda:'test',retrieve=lambda *a,**kw:[note])
    reader=memory.DevMemory(gateway,dict(store_id='test',namespace='test'),
                            dict(dev_sdk_sha256='d'*64))
    assert reader.eligible()==[note]
    applicability.pop('planner_behavior_sha256')
    assert reader.eligible()==[]
    applicability['planner_dev_local_sha256']='b'*64
    assert reader.eligible()==[]
    applicability['sdk_sha256']='b'*64
    assert reader.eligible()==[note]
