"""Catalog-to-attempt binding gaps, using production policy identities."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import ui_launch
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.task_dag import freeze_graph, load_manifest, write_manifest
from benchmarks.attention_harness.tests.test_task_dag import setup

REPO = Path(__file__).resolve().parents[3]
CATALOG = REPO / 'benchmarks/attention_harness/protocol/v2/skill_graphs/live_run_catalog.json'


def bound_profile(tmp_path, suite='robosuite'):
    task = 'cube_lift' if suite == 'robosuite' else 'counter_to_sink'
    code = tmp_path / 'policy.py'; code.write_text('pass\n')
    config = tmp_path / 'config.json'
    approved = {'suite': suite, 'task_id': task, 'seed': 101}
    if suite == 'robocasa':
        approved['schema_version'] = 'attentionbench.robocasa-formal-config.v3'
    config.write_text(json.dumps(approved))
    sha = hashlib.sha256(code.read_bytes()).hexdigest()
    graph = tmp_path / 'graph.json'
    graph.write_text(json.dumps({'graph_id': 'approved-task', 'entries': [
        {'name': task, 'description': 'whole approved task', 'status': 'done', 'dependencies': [],
         'attentionbench': {'suite': suite, 'task': task, 'approved_policy_sha256': sha}}]}))
    profile = {'id': 'bound', 'suite': suite, 'task': task, 'execution_target': suite+'_sim',
        'code': str(code), 'config': str(config), 'approved_policy_sha256': sha,
        'approved_config_sha256': hashlib.sha256(config.read_bytes()).hexdigest(),
        'skill_graph_file': str(graph), 'skill_graph_sha256': hashlib.sha256(graph.read_bytes()).hexdigest(),
        'skill_root': task}
    for field in ui_launch._ROOT_FLAGS[suite]: profile[field] = str(tmp_path)
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps({'require_skill_graph': True, 'profiles': [profile]}))
    return catalog, profile, graph


def selection(profile):
    return {'profile_id': profile['id'], 'seed': 101, 'attention_policy': 'reactive_help',
        'execution_target': profile['execution_target'], 'assistance_mode': 'live_human_first',
        'max_attempts': 2, 'assistance_credits': 0, 'token_limit': 0,
        'human_deadline_seconds': 60, 'overall_deadline_seconds': 120}


def test_live_task_catalog_uses_single_approved_1080p_robosuite_profile():
    profiles = ui_launch.load_catalog(CATALOG)
    robosuite = [p for p in profiles if p['suite'] == 'robosuite']
    assert len(robosuite) == 1
    assert (robosuite[0]['camera_width'], robosuite[0]['camera_height']) == (1920, 1080)
    approved = json.loads(Path(robosuite[0]['config']).read_text())
    assert (approved['camera_width'], approved['camera_height']) == (1920, 1080)
    assert hashlib.sha256(Path(robosuite[0]['config']).read_bytes()).hexdigest() == robosuite[0]['approved_config_sha256']
    assert len({(p['task'], p['seed']) for p in robosuite}) == len(robosuite)


def test_actual_catalog_binds_current_approved_policy_and_only_real_root():
    all_profiles = ui_launch.load_catalog(CATALOG)
    profiles = [p for p in all_profiles if not p.get("base_capability")]
    expected = {
        'live-human-robosuite-cube-lift-seed101-guidance-v1-1-1080p': ('robosuite', 'cube_lift', 'cube-lift-guidance-v1-1', '405f752968f73e0e0541b5a51e0cb201069f07c2dcfb3f26a5d0cb45deb767f2'),
        'live-human-robocasa-counter-to-sink-seed101-guidance-v1-1': ('robocasa', 'counter_to_sink', 'counter-to-sink-guidance-v1-1', 'c016bed6d2a85eb2cd1a972299a2f38394a63780fc537f4b19e83991a6c015d7'),
    }
    # Original approved policy pins are stable; independently published OpenArm
    # engineering revisions retain their own code/config/graph locks.
    original=[p for p in profiles if p['id'] in expected]
    additional=[p for p in profiles if p['id'] not in expected]
    assert len(original)==2 and {p['id'] for p in original}==set(expected)
    assert len(profiles)==4 and len(additional)==2
    assert {tuple(sorted(p['robot'].items())) for p in additional}=={
        tuple(sorted({'model':'OpenArm','execution_arm':side}.items())) for side in ['left','right']}
    assert len({p['approved_policy_sha256'] for p in additional})==1
    assert json.loads(CATALOG.read_text())['require_skill_graph'] is True
    for p in profiles:
        dag = freeze_graph(Path(p['skill_graph_file']), suite=p['suite'], task_id=p['task'],
            root_skill=p['skill_root'], expected_sha256=p['skill_graph_sha256'],
            policy_sha256=p['approved_policy_sha256'])
        if p['id'] in expected:
            suite, task, root, policy_sha = expected[p['id']]
        else:
            side=p['robot']['execution_arm']
            assert p['id'].startswith(f'live-human-robocasa-counter-to-sink-seed101-openarm-{side}-')
            suite,task,root,policy_sha='robocasa','counter_to_sink',f'openarm-counter-to-sink-{side}',p['approved_policy_sha256']
        assert hashlib.sha256(Path(p['code']).read_bytes()).hexdigest()==policy_sha
        assert (p['suite'],p['task'],p['seed'],p['approved_policy_sha256']) == (suite,task,101,policy_sha)
        assert len(dag['nodes']) == 1 and dag['nodes'][0]['dependencies'] == []
        assert dag['root_skill'] == root
        assert hashlib.sha256(Path(p['config']).read_bytes()).hexdigest()==p['approved_config_sha256']
        if 'openarm' in p['id']:
            assert p['robot']=={'model':'OpenArm','execution_arm':'left' if 'left' in p['id'] else 'right'}


@pytest.mark.parametrize('fault', ['absent', 'partial', 'missing_file', 'task', 'policy', 'source_tamper'])
def test_strict_catalog_rejects_binding_gap_before_launch(tmp_path, fault):
    catalog, p, graph = bound_profile(tmp_path)
    if fault == 'absent':
        for key in ('skill_graph_file', 'skill_graph_sha256', 'skill_root'): p.pop(key)
    elif fault == 'partial': p.pop('skill_root')
    elif fault == 'missing_file': graph.unlink()
    else:
        raw = json.loads(graph.read_text())
        if fault == 'task': raw['entries'][0]['attentionbench']['task'] = 'cube_stack'
        elif fault == 'policy': raw['entries'][0]['attentionbench']['approved_policy_sha256'] = 'b'*64
        else: raw['entries'][0]['description'] = 'tampered'
        graph.write_text(json.dumps(raw))
        if fault != 'source_tamper': p['skill_graph_sha256'] = hashlib.sha256(graph.read_bytes()).hexdigest()
    catalog.write_text(json.dumps({'require_skill_graph': True, 'profiles': [p]}))
    with pytest.raises((ValueError, OSError)): ui_launch.load_catalog(catalog)


def test_required_binding_is_rechecked_after_catalog_load(tmp_path, monkeypatch):
    catalog, _, _ = bound_profile(tmp_path)
    profiles = ui_launch.load_catalog(catalog)
    for key in ('skill_graph_file', 'skill_graph_sha256', 'skill_root'): profiles[0].pop(key)
    monkeypatch.setattr(ui_launch, 'inspect_formal_entry', lambda **k: ({'sha256': 'lock'}, None))
    store = AttentionStore(tmp_path/'store.sqlite3'); calls = []
    with pytest.raises(ValueError, match='Skill DAG'):
        ui_launch.launch_formal_run(selection(profiles[0]), profiles=profiles, store=store,
            artifact_root=tmp_path/'artifacts', popen=lambda *a, **k: calls.append(a) or SimpleNamespace(pid=123))
    assert not calls and not store.events()


@pytest.mark.parametrize('suite', ['robosuite', 'robocasa'])
def test_both_suites_freeze_before_spawn_and_dispatch_each_attempt(tmp_path, monkeypatch, suite):
    catalog, _, graph = bound_profile(tmp_path, suite)
    profiles = ui_launch.load_catalog(catalog); p = profiles[0]
    monkeypatch.setattr(ui_launch, 'inspect_formal_entry', lambda **k: ({'sha256': 'lock'}, None))
    captured = []
    def spawn(command, **kwargs):
        graph.write_text('[]')  # edit after freeze but before worker reads the manifest
        manifest = Path(command[command.index('--skill-dag')+1]); digest = command[command.index('--skill-dag-sha256')+1]
        captured.append(load_manifest(manifest, digest, suite=suite, task_id=p['task'], policy_sha256=p['approved_policy_sha256']))
        return SimpleNamespace(pid=123)
    launch_store = AttentionStore(tmp_path/'launch.sqlite3')
    ui_launch.launch_formal_run(selection(p), profiles=profiles, store=launch_store,
        artifact_root=tmp_path/'artifacts', popen=spawn)
    assert len(captured) == 1 and not any(e['event_type'] == 'run.created' for e in launch_store.events())
    from dataclasses import replace
    from benchmarks.attention_harness.tests.test_attention_store import records
    run = replace(records()[0], suite=suite, task_id=p['task'])
    launch_store.create_run(run); launch_store.bind_task_dag(run.run_id, captured[0])
    launch_store.record_executor_dispatch(run.run_id, index=0, started_at=10)
    launch_store.record_executor_dispatch(run.run_id, index=1, started_at=20)
    snapshots = AttentionStore(launch_store.path).attempt_dag_snapshots(run.run_id)
    assert len(snapshots) == 2
    assert {v['index'] for v in snapshots.values()} == {0, 1}
    assert all(v['dag'] == captured[0] for v in snapshots.values())
    before = launch_store.events()
    with pytest.raises(ValueError, match='locked digest'):
        ui_launch.launch_formal_run(selection(p), profiles=profiles, store=launch_store,
            artifact_root=tmp_path/'artifacts', popen=spawn)
    assert launch_store.events() == before and len(captured) == 1


def test_dispatch_and_snapshot_rollback_together_on_storage_failure(tmp_path):
    catalog, p, graph = bound_profile(tmp_path)
    dag = freeze_graph(graph, suite=p['suite'], task_id=p['task'], root_skill=p['skill_root'],
        expected_sha256=p['skill_graph_sha256'], policy_sha256=p['approved_policy_sha256'])
    store, run = setup(tmp_path); store.bind_task_dag(run.run_id, dag)
    with store._connect() as c:
        c.execute("CREATE TRIGGER fail_snapshot BEFORE INSERT ON events WHEN NEW.event_type='task_dag.snapshot' BEGIN SELECT RAISE(ABORT, 'disk fault'); END")
    before = store.events()
    with pytest.raises(sqlite3.IntegrityError, match='disk fault'):
        store.record_executor_dispatch(run.run_id, index=0, started_at=10)
    assert store.events() == before and store.attempt_dag_snapshots(run.run_id) == {}
    with store._connect() as c: c.execute('DROP TRIGGER fail_snapshot')
    store.record_executor_dispatch(run.run_id, index=0, started_at=10)
    assert len(store.attempt_dag_snapshots(run.run_id)) == 1


def test_manifest_publish_failure_never_exposes_partial_final_file(tmp_path, monkeypatch):
    import os
    _, p, graph = bound_profile(tmp_path)
    dag = freeze_graph(graph, suite=p['suite'], task_id=p['task'], root_skill=p['skill_root'],
        expected_sha256=p['skill_graph_sha256'], policy_sha256=p['approved_policy_sha256'])
    directory = tmp_path/'manifests'; publications = []
    def publish(source, dest):
        assert json.loads(Path(source).read_text()) == dag
        assert not Path(dest).exists()
        publications.append(dest)
        raise OSError('publication interrupted')
    monkeypatch.setattr(os, 'link', publish)
    with pytest.raises(OSError, match='publication interrupted'): write_manifest(dag, directory)
    assert len(publications) == 1 and not list(directory.iterdir())

@pytest.mark.parametrize('native_success', [False, True])
def test_root_execution_completion_is_independent_from_native_goal(tmp_path, native_success):
    from benchmarks.attention_harness.task_dag import dag_projection
    catalog, p, graph = bound_profile(tmp_path)
    dag = freeze_graph(graph, suite=p['suite'], task_id=p['task'], root_skill=p['skill_root'],
        expected_sha256=p['skill_graph_sha256'], policy_sha256=p['approved_policy_sha256'])
    store, run = setup(tmp_path); store.bind_task_dag(run.run_id, dag)
    store.record_executor_dispatch(run.run_id, index=0, started_at=10)
    aid = f"attempt:{run.run_id.removeprefix('run:')}:0"
    attempts = [{'attempt_id': aid, 'index': 0, 'status': 'succeeded' if native_success else 'failed'}]
    traces = {'t': {'trace_id': 't', 'attempt_id': aid, 'events': [
        {'event_id': 'execution-finished', 'event_type': 'execution.finished',
         'status': 'completed' if native_success else 'failed',
         'result': {'execution_status': 'completed', 'timed_out': False}}]}}
    node = dag_projection(store, run.run_id, attempts, traces,
        {aid: {'native_success': native_success, 'evaluated': True}})[aid]['dag']['nodes'][0]
    assert node['development_status'] == 'done'
    assert node['execution_status'] == 'executed'
    assert node['goal_status'] == ('succeeded' if native_success else 'failed')
    assert node['event_ids'] == []  # whole-execution result is not a subskill marker


def test_current_robocasa_definition_uses_approved_policy_and_same_catalog_contract(tmp_path):
    graph = REPO / 'benchmarks/attention_harness/protocol/v2/skill_graphs/robocasa_counter_to_sink.json'
    base = REPO / 'benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_guidance_v1_1_dev_2026-10-01'
    code = base/'base/robocasa_counter_to_sink/policy.py'; config = base/'configs/robocasa_counter_to_sink_seed101.json'
    profile = {'id':'robocasa-current','suite':'robocasa','task':'counter_to_sink','execution_target':'robocasa_sim',
        'code':str(code),'approved_policy_sha256':hashlib.sha256(code.read_bytes()).hexdigest(),
        'config':str(config),'approved_config_sha256':hashlib.sha256(config.read_bytes()).hexdigest(),
        'skill_graph_file':str(graph),'skill_graph_sha256':hashlib.sha256(graph.read_bytes()).hexdigest(),
        'skill_root':'counter-to-sink-guidance-v1-1'}
    # Local paths are fixture roots; no RoboCasa service/robot/model is spawned.
    for field in ui_launch._ROOT_FLAGS['robocasa']: profile[field] = str(tmp_path)
    catalog = tmp_path/'catalog.json';catalog.write_text(json.dumps({'require_skill_graph':True,'profiles':[profile]}))
    selected = ui_launch.load_catalog(catalog)[0]
    dag = freeze_graph(graph, suite=selected['suite'], task_id=selected['task'], root_skill=selected['skill_root'],
        expected_sha256=selected['skill_graph_sha256'], policy_sha256=selected['approved_policy_sha256'])
    assert dag['policy_sha256'] == 'c016bed6d2a85eb2cd1a972299a2f38394a63780fc537f4b19e83991a6c015d7'
    assert len(dag['nodes']) == 1 and dag['nodes'][0]['dependencies'] == []
