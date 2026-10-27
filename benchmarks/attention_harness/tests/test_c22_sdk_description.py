"""C22 offline contract, sealing, applicability and UI regressions; no simulator."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace as NS

import numpy as np
import pytest

from benchmarks.attention_harness import dev_tasks as sdk
from benchmarks.attention_harness import dev_planner_memory as memory
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment
from benchmarks.attention_harness.tests.c22_fixtures import source_only_lock
from openarm365_dev_v2 import dev_config as d


def baseline(monkeypatch):
    path = 'benchmarks/attention_harness/dev_tasks.py'
    module = ModuleType('c22_baseline')
    module.__file__ = str(Path(path).resolve())
    module.__package__ = 'benchmarks.attention_harness'
    monkeypatch.setitem(sys.modules, module.__name__, module)
    source = subprocess.check_output(['git', 'show', '1b74aae:'+path], text=True)
    exec(compile(source, path, 'exec'), module.__dict__)
    return module


def config(profile='r4-20261027', **kw):
    return dict(budget_tier='dev', runner_boundary='dev_loop', sdk_call_limit=320,
                horizon=9600, attempt_wall_seconds=1920,
                **({'execution_settings': {'profile':profile}} if profile else {}), **kw)


@pytest.mark.parametrize('profile,tier,boundary', [
    ('r3-20261025','dev','dev_loop'), (None,'legacy','dev_loop'),
    (None,'legacy','formal'), ('r4-20261027','legacy','dev_loop'),
    ('r4-20261027','dev','formal')])
def test_other_descriptions_byte_identical(monkeypatch, profile, tier, boundary):
    old = baseline(monkeypatch)
    c = config(profile); c.update(budget_tier=tier, runner_boundary=boundary)
    assert sdk.SDK == old.SDK and sdk.SDK_PROMPT.encode() == old.SDK_PROMPT.encode()
    assert sdk.dev_sdk_prompt(c).encode() == old.dev_sdk_prompt(c).encode()
    assert json.dumps(sdk.dev_sdk(c)).encode() == json.dumps(old.dev_sdk(c)).encode()
    assert sdk.dev_sdk_prompt().encode() == old.dev_sdk_prompt().encode()


def test_r4_changes_only_base_text_and_metadata(monkeypatch):
    c = config(); old = baseline(monkeypatch)
    before = old.dev_sdk_prompt(c)
    after = sdk.dev_sdk_prompt(c)
    assert after == before.replace('right_gripper, bimanual.', 'right_gripper, bimanual, base.', 1).replace('Base stationary.', sdk.BASE_PROMPT)
    assert 'Base stationary.' not in after
    for phrase in ('base.move_delta(dx, dy, dyaw)', 'current base/mount X/Y',
                   'sqrt(dx*dx+dy*dy) <=0.20m', 'abs(dyaw) <=0.30rad',
                   'BasePathRejected: Base path clearance', 'does not expose base pose keys'):
        assert phrase in after
    assert sdk.dev_sdk(c)['groups'] == old.dev_sdk(c)['groups'] + ['base']
    assert {k:v for k,v in sdk.dev_sdk(c).items() if k not in ('groups','base')} == {k:v for k,v in old.dev_sdk(c).items() if k != 'groups'}
    assert sdk.get_task('open_cabinet').public(c)['sdk'] == sdk.dev_sdk(c)
    from benchmarks.attention_harness.dev_help import AdvisorProvider
    packet = json.loads(AdvisorProvider().messages('open_cabinet','from robot_sdk import base',{},config=c)[-1]['content'])
    assert packet['task']['sdk'] == sdk.dev_sdk(c)


def test_runtime_signature_frame_limits_and_policy():
    from openarm365_dev.policy_validation import validate_generated_policy
    from openarm365_dev.base_motion import validate_delta, base_joint_delta
    from curobo_v2.client import PlanningRejected
    validate_generated_policy('from robot_sdk import base\nbase.move_delta(0.0, 0.125, 0.0)', config=config())
    assert np.allclose(validate_delta(.12,.16,.3), [.12,.16,.3])
    assert np.allclose(base_joint_delta([.1,0,.2], [0,0,1+np.pi/2], [0,0,1]), [0,.1,.2])
    for delta in ((.15,.15,0),(.201,0,0),(0,0,.301),(0,0,-.301)):
        with pytest.raises(PlanningRejected, match='per SDK call'):validate_delta(*delta)
    for delta in ((float('nan'),0,0),(0,float('inf'),0),('bad',0,0)):
        with pytest.raises(PlanningRejected, match='finite numeric'):validate_delta(*delta)


def test_frozen_seal_and_memory_digest():
    from attention_memory_service.applicability import interface_digest, sdk_matches, profile_matches
    before, after = config('r3-20261025'), config()
    for c in (before,after):
        seal = sdk.dev_sdk_seal(c); c.update(seal)
        sdk.verify_dev_sdk_seal(c)
        identity = dict(prompt=sdk.dev_sdk_prompt(c),contract=sdk.dev_sdk(c))
        assert c['dev_sdk_sha256'] == interface_digest(identity) == memory.sdk_interface_sha256(identity)
        assert c['dev_sdk_prompt_sha256'] == hashlib.sha256(identity['prompt'].encode()).hexdigest()
        assert c['dev_sdk_metadata_sha256'] == hashlib.sha256(json.dumps(identity['contract'],ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()
    assert before['dev_sdk_sha256'] != after['dev_sdk_sha256']
    assert before['dev_sdk_metadata_sha256'] != after['dev_sdk_metadata_sha256']
    a = dict(execution_profile='r3-20261025', sdk_interface_sha256=before['dev_sdk_sha256'])
    b = dict(execution_profile='r4-20261027', sdk_interface_sha256=after['dev_sdk_sha256'])
    assert not profile_matches(a,b) and not sdk_matches(a,b)
    assert not sdk_matches(dict(a,execution_profile=b['execution_profile']),b)
    for key in sdk.SDK_SEAL_KEYS:
        bad = copy.deepcopy(after); bad.pop(key)
        with pytest.raises(ValueError,match='seal mismatch'):sdk.verify_dev_sdk_seal(bad)
    after['attempt_wall_seconds'] -= 1
    with pytest.raises(ValueError,match='seal mismatch'):sdk.verify_dev_sdk_seal(after)


@pytest.mark.parametrize('profile', ['r3','r4','legacy'])
def test_only_r4_configs_gain_seals(monkeypatch, r3_deployment, r4_deployment, profile):
    monkeypatch.setattr(d,'verify_lock',lambda *a,**k:None)
    settings = {'r3':r3_deployment,'r4':r4_deployment,'legacy':None}[profile]
    # Exercise the public constructor without materializing the actual worktree.
    monkeypatch.setattr(d,'new_execution_settings',lambda **kw:settings)
    source = dict(kind='dev-local',source_root=str(d.ROOT))
    if profile == 'r3':
        # R3 construction is a frozen config path, not today's dev-local default.
        c = d._make_dev_config('open_cabinet',102,{'path':'fixture','sha256':'fixture'},execution_settings=settings)
    else:
        c = d.make_dev_config('open_cabinet',102,{'path':'fixture','sha256':'fixture'},
            execution_settings=settings,budget_tier='legacy' if profile=='legacy' else 'dev',
            **({'planner_source':source} if profile=='r4' else {}))
    assert d.validate_dev_config(c) is c
    if profile != 'r4':
        assert not any(k in c for k in sdk.SDK_SEAL_KEYS)
        return
    assert all(k in c for k in sdk.SDK_SEAL_KEYS)
    bad = copy.deepcopy(c);bad['dev_sdk_prompt_sha256']='f'*64
    with pytest.raises(ValueError,match='seal mismatch'):d.validate_dev_config(bad)
    for key in sdk.SDK_SEAL_KEYS:c.pop(key)
    with pytest.raises(ValueError,match='seal mismatch'):d.validate_dev_config(c)


def test_r4_memory_implementation_and_interface_changes(monkeypatch, tmp_path, r4_deployment, source_only_lock):
    from benchmarks.attention_harness.tests.test_memory_provisional import make, populated, SETTINGS
    from benchmarks.attention_harness.core.dev_store import DevConflict
    _, _, c, _, _ = make(tmp_path, 'autonomous', memory_authority=SETTINGS, reproducible_reset=True)
    assert c['execution_settings']['profile'] == 'r4-20261027'
    gateway, here = populated(tmp_path, c)
    original = memory.sdk_identity
    monkeypatch.setattr(memory, 'sdk_identity', lambda cfg: {**original(cfg), 'implementation': {'sdk.py': '0' * 64}})
    rebuilt = memory.DevMemory(gateway, SETTINGS, c, layered=True)
    assert rebuilt.context['sdk_sha256'] != here['sdk_sha256']
    assert rebuilt.context['sdk_interface_sha256'] == here['sdk_interface_sha256']
    assert [n.memory_id for n in rebuilt.selected()] == ['seed-here', 'p-new', 'p-old']
    # A different, valid sealed interface drops old notes through applicability.
    changed = copy.deepcopy(c)
    changed['attempt_wall_seconds'] -= 1
    changed.update(sdk.dev_sdk_seal(changed))
    assert d.validate_dev_config(changed) is changed
    client = memory.DevMemory(gateway, SETTINGS, changed, layered=True)
    assert client.context['sdk_interface_sha256'] != here['sdk_interface_sha256']
    assert client.selected() == []
    # Changing the interface behind the original seal is config drift, not a new run.
    monkeypatch.setattr(memory, 'sdk_identity', lambda cfg: {**original(cfg), 'prompt': 'changed SDK text'})
    with pytest.raises(DevConflict, match='differs from sealed config'):
        memory.DevMemory(gateway, SETTINGS, c, layered=True)


def test_manifest_history_and_memory_context(monkeypatch,tmp_path,r4_deployment):
    from benchmarks.attention_harness import dev_experiment as experiments
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    from openarm365_dev_v2 import scene_contract
    monkeypatch.setattr(d,'verify_lock',lambda *a,**kw:None)
    monkeypatch.setattr(experiments,'verify_lock',lambda *a,**kw:None)
    def fake_lock(path,**kw):
        path.write_text('{}')
        return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(experiments,'make_lock',fake_lock)
    store = DevV2Store(tmp_path/'store.sqlite3')
    result = experiments.create(store,tmp_path/'artifacts',dict(request_key='c22-sealed',tasks=['open_cabinet'],
        seeds=[102],policies=['autonomous'],mode='interactive',budget_tier='dev',
        target_depth=1,target_valid_executions=1,max_versions=1,help_budget=0))
    manifest = experiments.verify_manifest(result)
    c = manifest['configs']['open_cabinet:102']
    for key in sdk.SDK_SEAL_KEYS:
        assert c[key] == manifest[key] == result[key] == experiments.list_experiments(store)[0][key]
    c['scene_contract'] = {'uri':'fake','sha256':'a'*64}
    monkeypatch.setattr(scene_contract,'registered_contract_ref',lambda:c['scene_contract'])
    monkeypatch.setattr(memory,'sdk_identity',lambda c:dict(prompt=sdk.dev_sdk_prompt(c),contract=sdk.dev_sdk(c),implementation={}))
    ctx = memory.context(c,'c22')
    assert ctx['sdk_interface_sha256'] == c['dev_sdk_sha256']
    assert ctx['execution_profile'] == 'r4-20261027'
    # Even a gateway claiming an old note is trusted must not cross the SDK seal.
    good = dict(memory_id='new',version=1,guidance='bounded motion',status='trusted',applicability=ctx,
                match_level='same_scene',trusted_here=True)
    old = dict(good,memory_id='old',applicability=dict(ctx,sdk_interface_sha256='0'*64))
    gateway = NS(store_id=lambda:'fake',retrieve_entries=lambda *a,**kw:[old,good],
                 retrieve=lambda *a,**kw:[NS(**old),NS(**good)])
    for layered in (True,False):
        client = memory.DevMemory(gateway,dict(namespace='c22',store_id='fake'),c,layered=layered)
        assert [n.memory_id for n in (client.notes() if layered else client.eligible())] == ['new']


def test_summary_never_pools_sdk_descriptions():
    from benchmarks.attention_harness.tests.test_r3_memory_results import row
    from benchmarks.attention_harness.summarize_experiment import summarize,markdown
    cells = [dict(row('old',False,'r4-20261027'),dev_sdk_sha256='a'*64,dev_sdk_description='base stationary'),
             dict(row('new',True,'r4-20261027'),dev_sdk_sha256='b'*64,dev_sdk_description='base enabled')]
    result = summarize(dict(experiment_id='c22',manifest={},cells=cells))
    assert len(result['profiles']) == 2 and 'by_policy' not in result
    assert {g['dev_sdk_description']:g['by_policy']['autonomous']['native_success_rate'] for g in result['profiles']} == {'base stationary':0.,'base enabled':1.}
    text = markdown(result)
    assert 'SDK description: base enabled' in text and 'SDK description: base stationary' in text
    cells.append(row('unsealed', False, 'r4-20261027'))
    result = summarize(dict(experiment_id='c22',manifest={},cells=cells))
    assert len(result['profiles']) == 3
    assert sum('dev_sdk_sha256' not in group for group in result['profiles']) == 1


@pytest.mark.parametrize('profiles', [('legacy',), ('r3-20261025',), ('legacy', 'r3-20261025')])
def test_unsealed_summary_bytes_unchanged(profiles):
    from benchmarks.attention_harness.tests.test_r3_memory_results import row
    from benchmarks.attention_harness import summarize_experiment as summary
    path = 'benchmarks/attention_harness/summarize_experiment.py'
    old = ModuleType('c22_old_summary'); old.__package__ = 'benchmarks.attention_harness'
    source = subprocess.check_output(['git', 'show', '1b74aae:'+path], text=True)
    exec(compile(source, path, 'exec'), old.__dict__)
    exp = dict(experiment_id='frozen', manifest={},
               cells=[row(str(i), bool(i), profile) for i, profile in enumerate(profiles)])
    before, after = old.summarize(exp), summary.summarize(exp)
    assert json.dumps(after).encode() == json.dumps(before).encode()
    assert summary.markdown(after).encode() == old.markdown(before).encode()


def test_ui_labels():
    subprocess.run(['node','--test',str(Path(__file__).with_suffix('.cjs'))],check=True)
