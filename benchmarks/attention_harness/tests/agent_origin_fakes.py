"""Synthetic native artifacts; production origin exporter and service verifiers."""
import json
from pathlib import Path
from types import SimpleNamespace

from benchmarks.attention_harness.dev_ledger import DevV2Store, encoded
from benchmarks.attention_harness.dev_agent_ledger import AgentLedger
from benchmarks.attention_harness.dev_agent_workspace import Workspace
from benchmarks.attention_harness.tests.autovalidate_fakes import SceneFixture, STATE, OBS
from benchmarks.attention_harness.tests.test_dev_agent_memory_phase1 import formal, help_interval, stage
from benchmarks.attention_harness.tests.test_memory_provisional import REJECTED, TIMEOUT
from benchmarks.attention_harness import dev_planner_memory as memory, memory_auto_import
from benchmarks.attention_harness.memory_validation_provision import Provisioner
from attention_memory_service.memory_service import MemoryService


def build(tmp_path, monkeypatch, *, validation=False, models=None, policy_version=None, policy='reactive_help'):
    native = SceneFixture(tmp_path)
    if validation:
        from attention_memory_service.dev_v2 import digest
        source_root=tmp_path/'sources';source_root.mkdir()
        lock=json.loads(Path(native.lock['uri']).read_bytes());renamed={}
        for name in lock['files']:
            target=source_root/Path(name).relative_to(native.root);target.parent.mkdir(parents=True,exist_ok=True)
            Path(name).rename(target);renamed[name]=str(target)
        lock.update(files={renamed[k]:v for k,v in lock['files'].items()},source_root=str(source_root),
            source_sets={str(source_root/'openarm365_dev_v2'):[str(source_root/'openarm365_dev_v2/sdk.py')]})
        lock['planner_lock']['path']=renamed[lock['planner_lock']['path']]
        lock['model_sources']={renamed[k]:v for k,v in lock['model_sources'].items()}
        native.sdk['robot_model_identity']=dict(lock['model_sources'])
        native.sdk_sha=digest(json.dumps(native.sdk,sort_keys=True,ensure_ascii=False,allow_nan=False).encode())
        native.lock=native.write(Path(native.lock['uri']),lock)
    native.config_overrides = dict(dev_mode='agent', budget_tier='dev', runner_boundary='dev_loop',
                                  dev_lock=dict(path=native.lock['uri'], sha256=native.lock['sha256']))
    if validation:native.config_overrides.update(attempt_wall_seconds=1920,sdk_call_limit=320,horizon=9600)
    base = native.make('cell', failure=True)
    root = native.root/'cell'
    (root/'ledger.sqlite').unlink()
    store = DevV2Store(root/'ledger.sqlite')
    config = base['config']
    if policy_version is not None:config['agent_policy_version']=policy_version
    config_ref = native.write(root/'config.json', config)
    manifest = json.loads(Path(base['descriptor']['manifest']['uri']).read_bytes())
    manifest.update(dev_mode='agent', selection={})
    if validation:
        manifest['demos']={'pick':{}}
        manifest.update(models or dict(dev_model='gpt-6-astra',dev_reasoning_effort='high',eval_model='gpt-6.1-sol'))
    manifest['cells'][0]['policy_id'] = policy
    if policy_version is not None:
        assert validation
        manifest['agent_policy_version']=policy_version
        manifest['selection']['agent_policy_version']=policy_version
        for row in (*manifest['configs'].values(),*manifest['cells']):row['agent_policy_version']=policy_version
    mref = native.write(root/'manifest.json', manifest)
    with store.tx() as db:
        db.execute('DELETE FROM v2_versions'); db.execute('DELETE FROM v2_jobs')
        store.put(db, 'experiments', 'cell', dict(manifest=mref, selection={}))
        store.put(db, 'cells', 'cell', dict(key='cell', experiment_id='cell', dev_mode='agent', policy_id=policy,
            artifact_dir=str(root), task_id=config['task_id'], seed=config['seed'],
            **({'agent_policy_version':policy_version} if policy_version is not None else {})))
    ws = Workspace(root/'agent').prepare({'task_prompt': 'Move cup'}, config)
    ledger = AgentLedger(store.path, 'cell')
    f = SimpleNamespace(root=root, store=store, ws=ws, ledger=ledger, config=config)
    context = dict(base['context'], seed=config['seed'], scene_contract_ref=native.contract)
    monkeypatch.setattr(memory, 'context', lambda *a, **kw: dict(context,namespace=a[1] if len(a)>1 else context['namespace']))
    monkeypatch.setattr(memory, 'sdk_identity', lambda *a: native.sdk)
    from openarm365_dev_v2 import scene_contract
    monkeypatch.setattr(scene_contract, 'registered_contract_ref', lambda: native.contract)
    for n, public in ((1, REJECTED), (2, TIMEOUT)):
        if n == 2:help_interval(f, 1)
        directory = formal(f, n, public)
        (directory/'version.json').chmod(0o644);(directory/'public-feedback.json').chmod(0o644)
        record = json.loads((directory/'version.json').read_bytes())
        code=f'from robot_sdk import left_arm\ndef policy(sdk):\n    left_arm.move_delta(0, 0, .01)\n    # formal {n}\n'
        Path(record['source']['uri']).chmod(0o644);(directory/'policy.py').chmod(0o644)
        record['source']=native.write(Path(record['source']['uri']),code)
        native.write(directory/'policy.py',code)
        record.update(manifest=mref, config_sha256=config_ref['sha256'], help=native.write(directory/'outgoing.json', {}))
        native.write(directory/'version.json', record)
        binding = dict(run_id='run:cell', attempt_id=f'attempt:cell:{n-1}', policy_sha256=record['source']['sha256'],
                       config_sha256=config_ref['sha256'])
        trace = dict(binding, status='failed', error=public['public_error'],
            sdk_events=[dict(source=e['source'], operation=e['operation'], status=e['status']) for e in public['action_trace']])
        trace_ref = native.write(directory/'trace.json', trace)
        result = dict(run_id=binding['run_id'], attempt_id=binding['attempt_id'], status='failed', evaluated=True, native_success=False)
        result_ref = native.write(directory/'native.json', result)
        safety = native.write(directory/'safety.json', dict(run_id=binding['run_id'], attempt_id=binding['attempt_id'],
            source='native_per_physics_step_monitor', monitor_available=True, unsafe_attempts=0, violations=[]))
        sandbox = native.write(directory/'sandbox.json', dict(run_id=binding['run_id'], attempt_id=binding['attempt_id'],
            sdk_call_budget=dict(dispatched=1), control_steps=2, elapsed_seconds=.1))
        bundle = dict(binding, schema_version='attentionbench.formal-runner-result.v1', channel='dev_agent',
            formal_eligible=False, dev_version=n, policy_id=policy, config_lock=config_ref,
            task_id=config['task_id'], seed=config['seed'], suite='robocasa365', status='failed', native_success=False,
            native_evaluator=result, infrastructure_failure=False, outcome_unknown=False, runner_boundary='dev_loop',
            entry_lock=None, public_base_yaw_visible=False, artifact_dir=str(directory),
            scene_condition=base['bundle']['scene_condition'], sandbox=base['bundle']['sandbox'],
            artifacts=dict(trace=trace_ref, native_result=result_ref, safety=safety, sandbox_receipt=sandbox))
        bref = native.write(directory/'bundle.json', bundle)
        native.write(directory/'scene-condition.json',dict(state=STATE, validation=bundle['scene_condition']))
        from attention_memory_service.dev_v2 import public_trace_projection
        native.write(directory/'public-feedback.json', public_trace_projection(trace))
        agent_job=next(j for j in ledger.jobs() if j['id']==f'request:r{n}')
        with store.tx() as db:
            db.execute('UPDATE v2_versions SET payload=? WHERE cell=? AND version=?', (encoded(record), 'cell', n))
            job=json.loads(db.execute('SELECT payload FROM v2_jobs WHERE version=?', (n,)).fetchone()[0])
            job.update(job_id=f'cell:{n}:execute', actual=dict(executions=1,sdk_calls=1,control_steps=2,execution_seconds=.1))
            job['receipt'].update(bundle=bref,trace=trace_ref,native_result=result_ref,execution_status='failed',
                execution_usage=agent_job['execution_usage'],public_feedback=native.write(directory/'feedback.json',public_trace_projection(trace)))
            db.execute('UPDATE v2_jobs SET id=?,payload=? WHERE version=?',(job['job_id'],encoded(job),n))
    proposal_ref=stage(f,2)
    assert proposal_ref
    proposal=json.loads(Path(proposal_ref['uri']).read_bytes())
    service=MemoryService(tmp_path/'service.sqlite',dev_v2_registration_roots=dict(executor=[str(native.root)],
        validator=[str(native.validator)],source=[str(source_root)] if validation else [],contract=[str(tmp_path/'contracts')]))
    payload=memory_auto_import.provisional_payload(proposal,'offline-agent-origin')
    counts=memory_auto_import.run([store.path],client=service,namespace='offline-agent-origin',out=lambda value:None)
    assert counts==dict(created=1,outcomes=0,refused=0,deferred=0,planned=0)
    provision=Provisioner(dict(experiments_root=str(native.root),state_root=str(tmp_path/'worker')),service)
    return SimpleNamespace(**vars(f),native=native,service=service,proposal=proposal,provision=provision,
        note=dict(memory_id=payload['memory_id'],source_summary=dict(experiment_id='cell',cell='cell',version=1)),context=context)


def replay(f, task, seed, config, *, source, directory, cell, version):
    from copy import deepcopy
    directory.mkdir(parents=True)
    bundle=deepcopy(json.loads((f.root/'v1/bundle.json').read_bytes()))
    bundle['policy_source']=f.native.write(directory/'policy.py',source)
    bundle['policy_sha256']=bundle['policy_source']['sha256']
    bundle['config_lock']=f.native.write(directory/'config.json',config)
    bundle['config_sha256']=bundle['config_lock']['sha256']
    for label, ref in bundle['artifacts'].items():
        value=json.loads(Path(ref['uri']).read_bytes())
        if 'config_sha256' in value:value['config_sha256']=bundle['config_sha256']
        if 'policy_sha256' in value:value['policy_sha256']=bundle['policy_sha256']
        bundle['artifacts'][label]=f.native.write(directory/(label+'.json'),value)
    return OBS,STATE,f.native.write(directory/'bundle.json',bundle)
