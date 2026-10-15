from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from benchmarks.attention_harness.core.store import AttentionStore, StateConflictError
from benchmarks.attention_harness.tests.test_attention_store import records


def graph(tmp_path, *, suffix="", deps=None):
    from benchmarks.attention_harness.task_dag import freeze_graph
    path = tmp_path / ("graph" + suffix + ".json")
    entries = [
        {"name": "detect", "display_name": "定位", "status": "done", "description": suffix, "dependencies": []},
        {"name": "open", "display_name": "张开", "status": "done", "dependencies": []},
        {"name": "grasp", "display_name": "抓取", "status": "done", "dependencies": deps or ["detect", "open"],
         "attentionbench": {"suite": "robosuite", "task": "cube_lift", "approved_policy_sha256": "a" * 64}},
        {"name": "unrelated", "status": "running", "dependencies": []},
    ]
    path.write_text(json.dumps(entries))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, entries, freeze_graph(path, suite="robosuite", task_id="cube_lift",
        root_skill="grasp", expected_sha256=digest, policy_sha256="a" * 64)


def setup(tmp_path):
    store = AttentionStore(tmp_path / "store.sqlite3")
    run, attempt, trace, request = records()
    run = replace(run, suite="robosuite", task_id="cube_lift")
    store.create_run(run)
    return store, run


def test_freeze_real_dependency_closure_preserves_parallel_edges(tmp_path):
    _, _, dag = graph(tmp_path)
    assert [n["node_id"] for n in dag["nodes"]] == ["detect", "open", "grasp"]
    assert dag["nodes"][-1]["dependencies"] == ["detect", "open"]
    assert dag["nodes"][0]["development_status"] == "done"
    assert "execution_status" not in dag["nodes"][0]


@pytest.mark.parametrize("fault", ["digest", "task", "policy", "cycle", "missing", "duplicate"])
def test_reject_misbound_or_invalid_graph(tmp_path, fault):
    from benchmarks.attention_harness.task_dag import freeze_graph
    path, entries, _ = graph(tmp_path)
    kwargs = dict(suite="robosuite", task_id="cube_lift", root_skill="grasp", policy_sha256="a" * 64)
    if fault == "task": kwargs["task_id"] = "cube_stack"
    if fault == "policy": kwargs["policy_sha256"] = "b" * 64
    if fault == "cycle": entries[0]["dependencies"] = ["grasp"]
    if fault == "missing": entries[2]["dependencies"] = ["absent"]
    if fault == "duplicate": entries.append(entries[0])
    path.write_text(json.dumps(entries))
    kwargs["expected_sha256"] = "0" * 64 if fault == "digest" else hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError): freeze_graph(path, **kwargs)


def test_snapshot_is_attempt_bound_immutable_and_survives_restart_and_graph_edit(tmp_path):
    store, run = setup(tmp_path)
    _, _, dag = graph(tmp_path)
    store.bind_task_dag(run.run_id, dag)
    store.record_executor_dispatch(run.run_id, index=0, started_at=10)
    first = store.attempt_dag_snapshots(run.run_id)
    _, _, changed = graph(tmp_path, suffix="changed")
    store.bind_task_dag(run.run_id, changed)
    store.record_executor_dispatch(run.run_id, index=1, started_at=20)
    restored = AttentionStore(store.path).attempt_dag_snapshots(run.run_id)
    id0 = f"attempt:{run.run_id.removeprefix('run:')}:0"
    id1 = f"attempt:{run.run_id.removeprefix('run:')}:1"
    assert restored[id0] == first[id0]
    assert restored[id0]["dag"]["revision"] != restored[id1]["dag"]["revision"]
    store.record_executor_dispatch(run.run_id, index=0, started_at=10)  # must not recapture new DAG
    assert store.attempt_dag_snapshots(run.run_id)[id0] == first[id0]


def test_missing_graph_is_not_replaced_with_global_graph_or_fake_skills(tmp_path):
    store, run = setup(tmp_path)
    store.record_executor_dispatch(run.run_id, index=0, started_at=10)
    assert store.attempt_dag_snapshots(run.run_id) == {}
    _, _, dag = graph(tmp_path)
    store.bind_task_dag(run.run_id, dag)
    store.record_executor_dispatch(run.run_id, index=0, started_at=10)
    assert store.attempt_dag_snapshots(run.run_id) == {}  # historic dispatch remains missing


def test_projection_uses_execution_evidence_not_development_done(tmp_path):
    from benchmarks.attention_harness.task_dag import dag_projection
    store, run = setup(tmp_path)
    _, _, dag = graph(tmp_path)
    store.bind_task_dag(run.run_id, dag)
    store.record_executor_dispatch(run.run_id, index=0, started_at=10)
    attempt_id = f"attempt:{run.run_id.removeprefix('run:')}:0"
    attempts = [{"attempt_id": attempt_id, "index": 0, "status": "running", "status_source": "executor_dispatch"}]
    projected = dag_projection(store, run.run_id, attempts, {}, {})[attempt_id]
    nodes = {n["node_id"]: n for n in projected["dag"]["nodes"]}
    assert nodes["grasp"]["execution_status"] == "running"
    assert nodes["detect"]["execution_status"] == "unknown"
    assert all(n["goal_status"] == "unconfirmed" for n in nodes.values())
    trace = {"trace_id": "t", "attempt_id": attempt_id, "events": [
        {"event_id": "s1", "event_type": "skill.finished", "arguments": {"skill_id": "detect"}, "status": "completed"},
        {"event_id": "s2", "event_type": "sdk.gripper_command", "arguments": {"skill_id": "grasp"}, "operation": "close", "status": "completed"}]}
    attempts[0]["status"] = "failed"
    projected = dag_projection(store, run.run_id, attempts, {"t": trace}, {attempt_id: {"evaluated": True, "native_success": False}})[attempt_id]
    nodes = {n["node_id"]: n for n in projected["dag"]["nodes"]}
    assert nodes["detect"]["execution_status"] == "executed" and nodes["detect"]["goal_status"] == "unconfirmed"
    assert nodes["grasp"]["goal_status"] == "failed"
    assert nodes["grasp"]["event_ids"] == []  # gripper command is not a skill completion proof


def test_dashboard_shows_real_dispatch_before_attempt_persisted_and_frozen_dag(tmp_path):
    from benchmarks.attention_harness.ui_server import dashboard_snapshot
    store, run = setup(tmp_path)
    _, _, dag = graph(tmp_path)
    store.bind_task_dag(run.run_id, dag)
    store.record_task_plan(run.run_id, max_attempts=3)
    store.record_executor_dispatch(run.run_id, index=0, started_at=10)
    snap = dashboard_snapshot(store, run.run_id)
    assert snap["attempts"][0]["status_source"] == "executor_dispatch"
    assert snap["attempts"][0]["status"] == "running"
    assert snap["task_plan"]["max_attempts"] == 3
    assert snap["attempt_dags"][snap["attempts"][0]["attempt_id"]]["status"] == "bound"
    store.record_executor_completed(run.run_id, index=0, ended_at=20, sdk_motion_commands=0)
    snap = dashboard_snapshot(store, run.run_id)
    assert snap["attempts"][0]["status"] == "unknown"  # completion without verdict


def test_task_plan_cannot_change_locked_maximum(tmp_path):
    store, run = setup(tmp_path)
    store.record_task_plan(run.run_id, max_attempts=3)
    with pytest.raises(StateConflictError): store.record_task_plan(run.run_id, max_attempts=4)


def test_manifest_command_freezes_source_and_rejects_policy_and_file_tampering(tmp_path):
    from benchmarks.attention_harness.task_dag import graph_command_args, load_manifest
    path, entries, dag = graph(tmp_path)
    config = {'suite': 'robosuite', 'task': 'cube_lift', 'skill_graph_file': str(path),
              'skill_graph_sha256': dag['revision'], 'skill_root': 'grasp', 'approved_policy_sha256': 'a' * 64}
    args = graph_command_args(config, repo_root=tmp_path, artifact_root=tmp_path / 'artifacts')
    manifest = type(path)(args[1])
    entries[0]['description'] = 'new development state'
    path.write_text(json.dumps(entries))
    loaded = load_manifest(manifest, args[3], suite='robosuite', task_id='cube_lift', policy_sha256='a' * 64)
    assert loaded == dag
    with pytest.raises(ValueError): load_manifest(manifest, args[3], suite='robosuite', task_id='cube_lift', policy_sha256='b' * 64)
    manifest.write_text('{}')
    with pytest.raises(ValueError): load_manifest(manifest, args[3], suite='robosuite', task_id='cube_lift')


def test_scheduler_records_plan_and_each_dag_from_real_isolated_dispatch(tmp_path):
    from benchmarks.attention_harness.tests.test_robosuite_memory import FakeClient, _adapter
    from benchmarks.attention_harness.sim_gt_attention_run import run_robosuite_attention
    _, _, dag = graph(tmp_path)
    result = run_robosuite_attention(task_id='cube_lift', seed=101, artifact_root=tmp_path / 'run',
        policy_id='autonomous', robot_policy=lambda sdk, context: None,
        adapter_factory=lambda task, camera: _adapter(task, camera, client=FakeClient()),
        store_path=tmp_path / 'scheduler.sqlite3', max_attempts=2, assistance_credits=0, skill_dag=dag)
    run_id = 'run:' + type(tmp_path)(result['artifact_dir']).name
    store = AttentionStore(tmp_path / 'scheduler.sqlite3')
    assert store.task_plan(run_id)['max_attempts'] == 2
    snapshots = store.attempt_dag_snapshots(run_id)
    assert len(snapshots) == 2
    assert all(s['dag'] == dag for s in snapshots.values())


def test_malformed_or_foreign_skill_events_cannot_change_bound_node(tmp_path):
    from benchmarks.attention_harness.task_dag import dag_projection
    store, run = setup(tmp_path)
    _, _, dag = graph(tmp_path)
    store.bind_task_dag(run.run_id, dag)
    store.record_executor_dispatch(run.run_id, index=0, started_at=10)
    attempt_id = f"attempt:{run.run_id.removeprefix('run:')}:0"
    attempts = [{'attempt_id': attempt_id, 'index': 0, 'status': 'running'}]
    events = [None, {'event_type':'skill.finished','arguments':['detect'],'event_id':'bad'},
              {'event_type':'skill.finished','arguments':{'skill_id':'detect'}},
              {'event_type':'skill.finished','arguments':{'skill_id':'other'},'event_id':'other'}]
    trace = {'trace_id':'bound','attempt_id':attempt_id,'events':events}
    foreign = {'trace_id':'foreign','attempt_id':'other-attempt','events':[
        {'event_type':'skill.finished','arguments':{'skill_id':'detect'},'event_id':'foreign'}]}
    projected = dag_projection(store, run.run_id, attempts, {'bound':trace,'foreign':foreign}, {})
    detect = next(n for n in projected[attempt_id]['dag']['nodes'] if n['node_id']=='detect')
    assert detect['execution_status']=='unknown' and detect['event_ids']==[]


def test_http_restart_restores_bound_dag_plan_and_preserves_business_state(tmp_path):
    import threading
    from urllib.request import urlopen
    from benchmarks.attention_harness.ui_server import create_server
    store, run = setup(tmp_path)
    before = store.get_run(run.run_id), store.budget_status(run.run_id)
    _, _, dag = graph(tmp_path)
    store.bind_task_dag(run.run_id, dag)
    store.record_task_plan(run.run_id, max_attempts=3)
    store.record_executor_dispatch(run.run_id, index=0, started_at=10)
    snapshots = []
    for _ in range(2):
        restored = AttentionStore(store.path)
        server = create_server(store=restored, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with urlopen(f'http://127.0.0.1:{server.server_port}/api/runs/{run.run_id}', timeout=5) as response:
                snapshots.append(json.load(response))
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2); restored.close()
    assert snapshots[0]['task_plan']==snapshots[1]['task_plan']
    assert snapshots[0]['attempt_dags']==snapshots[1]['attempt_dags']
    assert snapshots[1]['attempts'][0]['status']=='running'
    assert before==(store.get_run(run.run_id), store.budget_status(run.run_id))


def test_ui_catalog_launch_freezes_exact_dag_before_process_and_never_creates_run(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from benchmarks.attention_harness import ui_launch
    from benchmarks.attention_harness.task_dag import load_manifest
    code=tmp_path/'policy.py'; code.write_text('pass\n')
    config=tmp_path/'config.json'; config.write_text(json.dumps({'suite':'robosuite','task_id':'cube_lift','seed':101}))
    path, entries, _ = graph(tmp_path)
    policy_sha=hashlib.sha256(code.read_bytes()).hexdigest()
    entries[2]['attentionbench']['approved_policy_sha256']=policy_sha; path.write_text(json.dumps(entries))
    profile={'id':'bound','suite':'robosuite','task':'cube_lift','seed':101,
        'execution_target':'robosuite_sim','service_source_root':str(tmp_path),
        'code':str(code),'config':str(config),'approved_policy_sha256':policy_sha,
        'approved_config_sha256':hashlib.sha256(config.read_bytes()).hexdigest(),
        'skill_graph_file':str(path),'skill_graph_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'skill_root':'grasp'}
    catalog=tmp_path/'catalog.json'; catalog.write_text(json.dumps({'profiles':[profile]}))
    profiles=ui_launch.load_catalog(catalog)
    monkeypatch.setattr(ui_launch,'inspect_formal_entry',lambda **kwargs:({'sha256':'locked'},None))
    calls=[]
    def popen(command, **kwargs):
        calls.append(command)
        manifest=type(path)(command[command.index('--skill-dag')+1])
        digest=command[command.index('--skill-dag-sha256')+1]
        assert load_manifest(manifest,digest,suite='robosuite',task_id='cube_lift',policy_sha256=policy_sha)
        return SimpleNamespace(pid=999999)
    store=AttentionStore(tmp_path/'launch.sqlite3')
    selection={'profile_id':'bound','seed':101,'attention_policy':'reactive_help',
        'execution_target':'robosuite_sim','assistance_mode':'live_human_first',
        'max_attempts':3,'assistance_credits':1,'token_limit':30000,
        'human_deadline_seconds':600,'overall_deadline_seconds':300}
    launch=ui_launch.launch_formal_run(selection,profiles=profiles,store=store,artifact_root=tmp_path/'artifacts',popen=popen)
    assert launch['locked']['skill_dag']['manifest_sha256']==calls[0][calls[0].index('--skill-dag-sha256')+1]
    assert len(calls)==1 and not any(e['event_type']=='run.created' for e in store.events())
    path.write_text('[]')
    with pytest.raises(ValueError,match='locked digest'):
        ui_launch.launch_formal_run(selection,profiles=profiles,store=store,artifact_root=tmp_path/'artifacts',popen=popen)
    assert len(calls)==1 and not any(e['event_type']=='run.created' for e in store.events())


@pytest.mark.parametrize('boundary',['trusted_dev','generated_sandbox','formal'])
def test_bridge_command_passes_locked_dag_for_each_executor_boundary(tmp_path,boundary):
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'skill-agent-setup/claude-code'))
    from attentionbench_bridge import build_attention_command
    from benchmarks.attention_harness.task_dag import load_manifest
    policy=tmp_path/'benchmarks/attention_harness/protocol/v2/policies/p.py'; policy.parent.mkdir(parents=True)
    policy.write_text('from robot_sdk import sensors\nsensors.find_objects()\n')
    cli=tmp_path/'benchmarks/attention_harness/sim_gt_attention_cli.py';cli.touch()
    formal=tmp_path/'benchmarks/attention_harness/protocol/v2/formal.json';formal.write_text('{}')
    path,entries,_=graph(tmp_path)
    policy_sha=hashlib.sha256(policy.read_bytes()).hexdigest()
    entries[2]['attentionbench']['approved_policy_sha256']=policy_sha;path.write_text(json.dumps(entries))
    config={'suite':'robosuite','task':'cube_lift','seed':101,'attention_policy':'reactive_help',
        'runner_boundary':boundary,'approved_trusted_policy':True,'robot_policy':'lab_policy:run',
        'allow_generated_sandbox':True,'generated_policy_file':str(policy.relative_to(tmp_path)),
        'formal_config_file':str(formal.relative_to(tmp_path)), 'service_source_root':str(tmp_path),
        'approved_generated_policy':True,'approved_policy_sha256':policy_sha,
        'approved_config_sha256':hashlib.sha256(formal.read_bytes()).hexdigest(),
        'skill_graph_file':str(path),'skill_graph_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'skill_root':'grasp'}
    command=build_attention_command(config,repo_root=tmp_path)
    manifest=Path(command[command.index('--skill-dag')+1]);digest=command[command.index('--skill-dag-sha256')+1]
    dag=load_manifest(manifest,digest,suite='robosuite',task_id='cube_lift',policy_sha256=policy_sha)
    assert dag['root_skill']=='grasp'
    assert '--robot-policy' in command if boundary=='trusted_dev' else '--robot-policy' not in command


@pytest.mark.parametrize('formal',[False,True])
def test_cli_rejects_misbound_manifest_before_any_runner_or_service(tmp_path,monkeypatch,formal):
    import sys
    from benchmarks.attention_harness import sim_gt_attention_cli,formal_attention_cli
    from benchmarks.attention_harness.task_dag import write_manifest
    _,_,dag=graph(tmp_path);manifest,digest=write_manifest(dag,tmp_path/'manifests')
    command=['cli','--suite','robosuite','--task','cube_stack','--seed','101','--attention-policy','reactive_help',
        '--artifact-root',str(tmp_path/'artifacts'),'--skill-dag',str(manifest),'--skill-dag-sha256',digest]
    if formal:
        command+=['--code','missing.py','--config','missing.json','--approved-policy-sha256','a'*64,'--approved-config-sha256','b'*64]
    else: command+=['--robot-policy','missing:run']
    monkeypatch.setattr(sys,'argv',command)
    with pytest.raises(SystemExit) as error: (formal_attention_cli if formal else sim_gt_attention_cli).main()
    assert error.value.code==2 and not (tmp_path/'artifacts').exists()
