"""Offline budget probe checks; no simulator, planner, or model is started."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openarm365_dev_v2.diag_budget import (SOURCE, MARKER, diagnostic_budget,
                                            task_limits, probe_reserve)
from openarm365_dev_v2 import runner as runner_module, dev_config
from benchmarks.attention_harness.diagnostics.grasp_chain_probe import (carry_policy_source,
    sequenced_policy_source)
from benchmarks.attention_harness.formal_runner_boundary import FormalRunRequest
from benchmarks.attention_harness.dev_ledger import DevV2Store, RESOURCES, encoded
from benchmarks.attention_harness.core.dev_store import DevConflict


def test_scale_and_default(monkeypatch):
    monkeypatch.delenv(SOURCE, raising=False)
    monkeypatch.delenv(MARKER, raising=False)
    assert diagnostic_budget() is None
    assert task_limits() == {'sdk_calls': 80, 'control_steps': 2400, 'attempt_seconds': 480}
    assert probe_reserve() == {'sdk_calls': 80, 'control_steps': 2400,
        'execution_seconds': 540, 'startup_cleanup_seconds': 60, 'wall_limit': 4260}
    monkeypatch.setenv(MARKER, '1')
    monkeypatch.setenv(SOURCE, '2.5')
    assert diagnostic_budget() == {'scale': 2.5, 'sdk_calls': 200,
        'control_steps': 6000, 'attempt_seconds': 1200, 'source': SOURCE}
    assert probe_reserve() == {'sdk_calls': 200, 'control_steps': 6000,
        'execution_seconds': 1350, 'startup_cleanup_seconds': 150, 'wall_limit': 10650}
    for bad in ('0', '9', '-1', 'nan', 'inf', 'x', ''):
        monkeypatch.setenv(SOURCE, bad)
        with pytest.raises(ValueError): diagnostic_budget()
    monkeypatch.delenv(MARKER)
    monkeypatch.setenv(SOURCE, '2')
    with pytest.raises(ValueError, match='requires'): diagnostic_budget()


def test_policy_default_digest_and_scaled_guards(monkeypatch):
    monkeypatch.delenv(SOURCE, raising=False)
    monkeypatch.delenv(MARKER, raising=False)
    args=((0, .25, 0), (.3007057995042732, .9537169507482269, 0, 0),
          {'position_world_m': [.49, -2.37, .94], 'z_range_world_m': [.92, .96]})
    source=sequenced_policy_source(*args, sequence='translate-first', rotation_step=.15)
    sink={'sink_pos_world_m': [.3, -1.8, .98], 'sink_rim_top_world_m': 1.02}
    default=carry_policy_source(source, sink, (.2, -.5, 0))
    assert hashlib.sha256(default.encode()).hexdigest() == 'caa267aefb3737423e517cf19dd21abff75dd0e2a53e0229c7a365e625a790f5'
    monkeypatch.setenv(SOURCE, '2')
    monkeypatch.setenv(MARKER, '1')
    scaled=carry_policy_source(source, sink, (.2, -.5, 0))
    assert "<= 160, 'SDK call budget exceeded'" in scaled
    assert "<= 80, 'SDK call budget exceeded'" not in scaled


def test_config_and_runner_with_fake_native(monkeypatch, tmp_path):
    monkeypatch.setattr(dev_config, 'verify_lock', lambda *a, **k: None)
    monkeypatch.delenv(SOURCE, raising=False)
    monkeypatch.delenv(MARKER, raising=False)
    lock={'path': 'fixture', 'sha256': 'fixture'}
    base=dev_config.make_dev_config('pick_place_counter_to_sink', 102, lock)
    assert (base['sdk_call_limit'], base['horizon'], base['attempt_wall_seconds']) == (80, 2400, 480)
    monkeypatch.setenv(SOURCE, '2')
    monkeypatch.setenv(MARKER, '1')
    config=dev_config.make_dev_config('pick_place_counter_to_sink', 102, lock)
    assert (config['sdk_call_limit'], config['horizon'], config['attempt_wall_seconds']) == (160, 4800, 960)
    assert dev_config.validate_dev_config(config) == config
    class Owner:
        def __init__(self, **kw): self.ledger=[];self.sent={};self.unresolved=[]
        def start(self): return {'initial_observation': {'robot': {}, 'perception': {'values': {}}}, 'capabilities': {}}
        def rpc(self, group, method, *a, **kw):
            return {'robot': {}, 'perception': {'values': {}}} if method=='observe' else {'evaluated': True, 'native_success': False}
        def stop(self, reason): return {'leader_reaped': True, 'process_group_gone': True, 'planner_process_groups_gone': True}
    seen=[]
    monkeypatch.setattr(runner_module, 'NativeOwner', Owner)
    monkeypatch.setattr(runner_module, 'probe_sandbox', lambda: {'controlled': True})
    monkeypatch.setattr(runner_module, 'build_sdk', lambda *a: None)
    monkeypatch.setattr(runner_module, 'finalize_recording', lambda *a: {})
    monkeypatch.setattr(runner_module, 'execute_formal_policy', lambda **kw: (seen.append(kw['max_sdk_calls']) or SimpleNamespace(status='completed', error=None, call_count=2)))
    policy=tmp_path/'policy.py';policy.write_text('from robot_sdk import sensors\nsensors.get_observation()\n')
    cp=tmp_path/'config.json';cp.write_text(json.dumps(config))
    root=tmp_path/'executions';root.mkdir()
    request=FormalRunRequest('robocasa365', config['task_id'], 102, policy,
        hashlib.sha256(policy.read_bytes()).hexdigest(), hashlib.sha256(cp.read_bytes()).hexdigest(),
        root, 1080, cp, run_id='run:diag', attempt_id='attempt:diag:0',
        attempt_deadline_seconds=960, sdk_call_limit=160)
    request.validate()
    runner=runner_module.OpenArm365DevRunnerV2(service_source_root='',sim_python='',sim_source_root='',task_source_root='',asset_manifest='')
    monkeypatch.setattr(runner, 'load_config', lambda req: config)
    assert runner.budgets(request, config) == (960, 160)
    result=runner.execute(request)
    assert seen == [160]
    for name in ('sandbox_receipt', 'native_result', 'trace'):
        row=json.loads(Path(result['artifacts'][name]['uri']).read_text())
        assert row['diagnostic_budget_override']==diagnostic_budget()
    assert result['diagnostic_budget_override']==diagnostic_budget()
    assert result['formal_eligible'] is False
    with pytest.raises(ValueError, match='budgets'):
        runner.budgets(SimpleNamespace(sdk_call_limit=161,effective_attempt_deadline_seconds=960), config)
    with pytest.raises(ValueError, match='budgets'):
        runner.budgets(SimpleNamespace(sdk_call_limit=160,effective_attempt_deadline_seconds=961), config)
    monkeypatch.delenv(SOURCE)
    monkeypatch.delenv(MARKER)
    base_path=tmp_path/'base-config.json';base_path.write_text(json.dumps(base))
    base_request=FormalRunRequest('robocasa365', base['task_id'], 102, policy,
        hashlib.sha256(policy.read_bytes()).hexdigest(), hashlib.sha256(base_path.read_bytes()).hexdigest(),
        root, 540, base_path, run_id='run:base', attempt_id='attempt:base:0',
        attempt_deadline_seconds=480, sdk_call_limit=80)
    monkeypatch.setattr(runner, 'load_config', lambda req: base)
    assert runner.budgets(base_request, base)==(480,80)
    base_result=runner.execute(base_request)
    assert 'diagnostic_budget_override' not in base_result
    assert 'diagnostic_budget_override' not in json.loads(Path(base_result['artifacts']['sandbox_receipt']['uri']).read_text())
    assert seen==[160,80]


def test_diagnostic_ledger_cannot_settle_normal_experiment(tmp_path):
    store=DevV2Store(tmp_path/'ledger.sqlite')
    job='cell:1:execute';cell='cell';exp='exp'
    with store.tx() as db:
        store.put(db,'experiments',exp,{'selection': {'mode': 'interactive'}})
        store.put(db,'cells',cell,{'experiment_id':exp,'used':{k:0 for k in RESOURCES}})
        db.execute('INSERT INTO v2_jobs VALUES (?,?,?,?,?,?)',
            (job,cell,1,'execute','claimed',encoded({'job_id':job,'cell':cell,'version':1,'kind':'execute','reservation':{'executions':1}})))
    receipt={'diagnostic':True,'diagnostic_budget_override':{'scale':2}}
    with pytest.raises(DevConflict, match='normal Dev ledger'):
        store.settle(job,{'executions':1},receipt)
    with store.tx() as db:
        store.put(db,'experiments',exp,{'selection': {'mode': 'diagnostic'}})
    store.settle(job,{'executions':1},receipt)
    row=store.cell(cell)['jobs'][0]
    assert row['state']=='settled' and row['receipt']['diagnostic'] is True
    assert sum(j['kind']=='execute' and j['state']=='settled' and not j.get('receipt',{}).get('diagnostic') for j in store.cell(cell)['jobs'])==0


def test_probe_writes_scaled_budget_without_dispatch(monkeypatch, tmp_path):
    from benchmarks.attention_harness.diagnostics import grasp_chain_probe
    from benchmarks.attention_harness import dev_experiment, dev_ledger
    monkeypatch.setenv(SOURCE, '2')
    monkeypatch.setenv(MARKER, '1')
    class Store:
        def __init__(self, path): self.path=path
    monkeypatch.setattr(dev_ledger, 'DevV2Store', Store)
    seen={}
    def create(store, root, selection):
        seen['selection']=selection
        return {'experiment_id':'diag-exp','manifest':{'sha256':'a'*64}}
    def manifest(exp):
        return {'goal_id':'diag-goal','goal_limits':seen['selection']['cell_budget'],
                'wall_limit':seen['selection']['wall_limit']}
    monkeypatch.setattr(dev_experiment, 'create', create)
    monkeypatch.setattr(dev_experiment, 'verify_manifest', manifest)
    monkeypatch.setattr(dev_experiment, 'run', lambda *a, **k: seen.update(dispatched=True))
    grasp_chain_probe.run(tmp_path/'probe',102,'from robot_sdk import sensors\n')
    budget=json.loads((tmp_path/'probe'/'budget.json').read_text())
    assert seen['selection']['mode']=='diagnostic'
    assert budget['limits']['sdk_calls']==160
    assert budget['limits']['control_steps']==4800
    assert budget['limits']['execution_seconds']==1080
    assert budget['wall_limit']==8520
    assert seen['dispatched'] is True


def test_executor_horizon_and_sandbox_meter_use_given_task_limits(monkeypatch):
    from openarm365_dev.executor import NativeExecutor
    from openarm365_dev.sandbox import _meter_sdk_call
    for ceiling in (2400, 4800):
        calls=[]
        obj=NativeExecutor.__new__(NativeExecutor)
        obj.env=SimpleNamespace(horizon=ceiling,step=lambda action:calls.append(action))
        obj.sequence=ceiling-1
        obj.check_live=lambda: None
        obj.action=lambda: [0]
        obj.frame_callback=None
        obj.control_step()
        assert obj.sequence==ceiling and calls==[[0]]
        with pytest.raises(RuntimeError,match='control-step budget'):
            obj.control_step()
    for ceiling in (80,160):
        assert _meter_sdk_call(ceiling-1,ceiling)==ceiling
        with pytest.raises(RuntimeError,match='SDK call limit'):
            _meter_sdk_call(ceiling,ceiling)


def test_probe_cli_rejects_bad_scale_without_reset(monkeypatch):
    from benchmarks.attention_harness.diagnostics.grasp_chain_probe import main
    monkeypatch.delenv(SOURCE,raising=False)
    monkeypatch.delenv(MARKER,raising=False)
    with pytest.raises(SystemExit) as exc:
        main(['--base-shift','0,.25,0','--budget-scale','nan','--dry-run'])
    assert exc.value.code==2
    assert SOURCE not in __import__('os').environ


def test_variants_forward_scale_in_dry_run(monkeypatch, tmp_path):
    from benchmarks.attention_harness.diagnostics.grasp_variants import main
    monkeypatch.delenv(SOURCE,raising=False)
    monkeypatch.delenv(MARKER,raising=False)
    out=tmp_path/'variants'
    assert main(['--variants','v:theta=55','--budget-scale','2','--dry-run','--out',str(out)])==0
    rows=json.loads((out/'results.json').read_text())
    assert rows[0]['diagnostic_budget_override']['sdk_calls']==160
    assert ['--budget-scale','2']==rows[0]['command'][-2:]
    assert "<= 155, 'SDK call budget exceeded'" in (out/'v-policy.py').read_text()


def test_request_ceiling_reaches_eightfold_only_in_probe(monkeypatch, tmp_path):
    policy=tmp_path/'policy.py';policy.write_text('from robot_sdk import sensors\n')
    config=tmp_path/'config.json';config.write_text('{}')
    root=tmp_path/'out';root.mkdir()
    def request(limit):
        return FormalRunRequest('robocasa365','pick_place_counter_to_sink',102,policy,
            hashlib.sha256(policy.read_bytes()).hexdigest(),hashlib.sha256(config.read_bytes()).hexdigest(),
            root,4320,config,run_id='run:diag',attempt_id='attempt:diag:0',
            attempt_deadline_seconds=3840,sdk_call_limit=limit)
    monkeypatch.delenv(SOURCE,raising=False)
    monkeypatch.delenv(MARKER,raising=False)
    with pytest.raises(ValueError,match='SDK call limit'):request(640).validate()
    monkeypatch.setenv(SOURCE,'8')
    monkeypatch.setenv(MARKER,'1')
    request(640).validate()
    with pytest.raises(ValueError,match='SDK call limit'):request(641).validate()
