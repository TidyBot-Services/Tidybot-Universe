"""Controlled fixtures only; no human decision, paid provider or native run."""
import asyncio
import copy
import hashlib
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from benchmarks.attention_harness import dev_planner as planner
from benchmarks.attention_harness import dev_planner_memory as memory
from benchmarks.attention_harness import dev_v2_loop as loop
from benchmarks.attention_harness.dev_configuration import normalize_configuration, derive_cell_budget
from benchmarks.attention_harness.dev_experiment import create, run, snapshot, cancel, acquire, switch
from benchmarks.attention_harness.dev_ledger import DevV2Store, RESOURCES
from benchmarks.attention_harness.dev_loop import process_identity, immutable, sha
from benchmarks.attention_harness.core.dev_store import DevConflict
from openarm365_dev_v2.tests.fakes import Client, Runner
from openarm365_dev_v2.tests.test_iteration import HintClient

SOURCE = 'from robot_sdk import left_arm\nleft_arm.move_delta(0,0,0.01)\n'
SENSOR = 'from robot_sdk import sensors\nobservation=sensors.get_observation()\n'


def setup(tmp_path, **kw):
    selection = dict(request_key='threefeature-test', tasks=['open_cabinet'], seeds=[113],
        policies=[planner.POLICY], mode='interactive', target_depth=1,
        target_valid_executions=1, max_versions=1, help_budget=1,
        planner_features={'human_wait_seconds': 1})
    selection.update(kw)
    s = DevV2Store(tmp_path / 'ledger.sqlite')
    e = create(s, tmp_path / 'artifacts', selection)
    c = e['cells'][0]
    owner = process_identity()
    root = Path(e['artifact_dir']) / 'cells' / c['key']
    root.mkdir(parents=True)
    m = json.loads(Path(e['manifest']['uri']).read_bytes())
    cfg = m['configs']['open_cabinet:113']
    immutable(root / 'config.json', cfg)
    s.update_cell(c['key'], state='running', owner=owner, artifact_dir=str(root))
    return s, e, s.cell(c['key']), cfg, owner, root


def add_version(s, c, root, source=SOURCE, version=1):
    d = root / f'v{version}'
    d.mkdir(exist_ok=True)
    ref = immutable(d / 'policy.py', source)
    row = dict(version=version, source=ref, formal_eligible=False, valid=True, validation_error=None)
    s.version(c['key'], row)
    return row, d


def approval(s, c, cfg, version=1, source=SOURCE, timeout=60):
    return planner.PlannerControl(s).request(c['key'], version, 'approval', planner.binding(c['key'], version, source, cfg), timeout)


def controlled_reply(s, r, approved=True):
    return planner.PlannerControl(s).reply(r['request_id'], scope=r['binding'], approved=approved,
                                          actor='controlled_test_subject', response_id='controlled-test-decision')


def test_configuration_contract_and_budget():
    sel = dict(planner_features={'human_wait_seconds': 7}, policies=[planner.POLICY], help_budget=1)
    p = normalize_configuration(sel)
    assert derive_cell_budget(p)['help_wait_seconds'] == 120 + 5 * 7
    for value in [0, 601, True, float('nan')]:
        with pytest.raises(ValueError):
            planner.configuration({'human_wait_seconds': value})
    for url in ['http://remote:80', 'https://person:secret@example.com', 'https://example.com?key=secret']:
        with pytest.raises(ValueError):
            planner.configuration({'memory': dict(base_url=url, store_id='x', namespace='test')})
    with pytest.raises(ValueError):
        normalize_configuration({**sel, 'policies': ['autonomous']})


def test_sensor_source_needs_no_approval(tmp_path):
    s, e, c, cfg, owner, root = setup(tmp_path)
    assert planner.binding(c['key'], 1, SENSOR, cfg) is None
    assert planner.wait_for_approval(s, c['key'], 1, SENSOR, cfg, owner, root, c['planner_features'], {}) is None
    assert not s.cell(c['key'])['jobs']
    with pytest.raises(DevConflict):
        switch(s, c['key'], 'autonomous')


def test_exact_action_only_once_duplicate_and_restart(tmp_path):
    s, e, c, cfg, owner, root = setup(tmp_path)
    add_version(s, c, root)
    ctl = planner.PlannerControl(s)
    r = approval(s, c, cfg)
    wrong=copy.deepcopy(r['binding']);wrong['action_version']=True
    with pytest.raises(DevConflict):
        ctl.reply(r['request_id'],scope=wrong,approved=True,actor='controlled_test_subject',response_id='bad-boolean-version')
    approved = controlled_reply(s, r)
    assert approved == controlled_reply(s, r)
    reopened = planner.PlannerControl(DevV2Store(s.path)).get(r['request_id'])
    assert reopened['state'] == 'approved' and reopened['deadline_epoch'] == r['deadline_epoch']
    job = s.reserve(c['key'], 1, 'execute', {'executions': 1})
    with pytest.raises(DevConflict, match='requires human approval'):
        s.claim(job['job_id'], owner)
    s.claim(job['job_id'], owner, approval_id=r['request_id'])
    assert ctl.get(r['request_id'])['state'] == 'consumed'
    assert controlled_reply(s, r)['state'] == 'consumed'
    with pytest.raises(DevConflict):
        s.claim(job['job_id'], owner, approval_id=r['request_id'])
    assert s.export_ledger()['goal']['reserved']['executions'] == 1


@pytest.mark.parametrize('mutate', ['code', 'version', 'config', 'scope'])
def test_changed_binding_never_dispatches(tmp_path, mutate):
    s, e, c, cfg, owner, root = setup(tmp_path)
    row, _ = add_version(s, c, root)
    r = approval(s, c, cfg)
    controlled_reply(s, r)
    if mutate == 'code':
        Path(row['source']['uri']).chmod(0o644)
        Path(row['source']['uri']).write_text(SOURCE + 'print(1)\n')
    elif mutate == 'version':
        add_version(s, c, root, SOURCE, 2)
    elif mutate == 'config':
        p = root / 'config.json'; p.chmod(0o644)
        changed = copy.deepcopy(cfg); changed['seed'] = 114
        p.write_text(json.dumps(changed))
    else:
        bad = copy.deepcopy(r['binding']); bad['scope']['sdk_calls'] += 1
        with pytest.raises(DevConflict):
            planner.PlannerControl(s).reply(r['request_id'], scope=bad, approved=True, actor='test', response_id='different')
        return
    j = s.reserve(c['key'], 1, 'execute', {'executions': 1})
    with pytest.raises(DevConflict):
        s.claim(j['job_id'], owner, approval_id=r['request_id'])
    assert not s.cell(c['key'])['jobs'][0].get('claimed_at')


@pytest.mark.parametrize('end', ['rejected', 'timed_out', 'cancelled'])
def test_wait_terminal_cost_no_native_and_actual_work(tmp_path, end):
    s, e, c, cfg, owner, root = setup(tmp_path)
    _, d = add_version(s, c, root)
    error = []
    def wait():
        try:
            planner.wait_for_approval(s, c['key'], 1, SOURCE, cfg, owner, d, c['planner_features'],
                {'action_trace': [{'source': 'left_arm', 'operation': 'move_delta', 'status': 'failed'}]})
        except planner.PlannerActionBlocked as exc:
            error.append(str(exc))
    thread = threading.Thread(target=wait); thread.start()
    ctl = planner.PlannerControl(s)
    limit = time.monotonic() + 5
    while time.monotonic() < limit:
        state = ctl.snapshot(c['key'])
        if state['requests'] and state['work'] and state['work'][0]['state']=='completed':
            break
        time.sleep(.01)
    r = state['requests'][0]
    if end == 'rejected':
        controlled_reply(s, r, False)
    elif end == 'cancelled':
        cancel(s, e['experiment_id'])
    thread.join(5)
    assert not thread.is_alive() and error == ['motion approval ' + end]
    snap = ctl.snapshot(c['key'])
    assert snap['requests'][0]['state'] == end
    work = snap['work'][0]
    assert work['state'] == 'completed' and work['provider_calls'] == work['sdk_calls'] == 0
    evidence = json.loads(Path(work['result']['uri']).read_bytes())
    assert evidence['public_trace_analysis']['failed_operations'][0]['operation'] == 'move_delta'
    assert not evidence['response_dependency']
    assert s.export_ledger()['recompute_matches']
    assert s.export_ledger()['goal']['used']['executions'] == 0
    assert not any(s.export_ledger()['goal']['reserved'].values())
    if end != 'rejected':
        with pytest.raises(DevConflict, match='late'):
            controlled_reply(s, r)


def test_advisor_overlaps_preparation_cancel_rejects_known_paid_reply(tmp_path):
    s, e, c, cfg, owner, root = setup(tmp_path)
    class ControlledSlowAdvisor:
        def chat(self, **kw):
            # Wait for an observable real preparation output while this test
            # provider is in flight. No guessed sleep proves the causal order.
            ctl = planner.PlannerControl(s)
            limit = time.monotonic() + 3
            while time.monotonic() < limit:
                work = ctl.snapshot(c['key'])['work']
                if work and work[0]['state'] == 'completed':
                    break
                time.sleep(.01)
            assert work[0]['ended_at'] >= work[0]['started_at']
            assert Path(work[0]['result']['uri']).is_file()
            cancel(s, e['experiment_id'])
            from benchmarks.attention_harness.parcc_client import ParccResponse
            return ParccResponse('controlled-test', 'Use small deltas.', None, .01, 1,
                                 dict(total_tokens=10, prompt_tokens=5, completion_tokens=5))
    messages = loop.AdvisorProvider().messages('open_cabinet', SOURCE, {})
    with pytest.raises(planner.PlannerActionBlocked):
        loop.provider_call(s, c['key'], 1, 'advisor', messages, owner, root, ControlledSlowAdvisor())
    assert s.cell(c['key'])['jobs'][0]['state'] == 'settled'
    assert s.cell(c['key'])['used']['provider_tokens'] == 10
    assert planner.PlannerControl(s).snapshot(c['key'])['requests'][0]['adopted'] is False
    assert not any(t.name == 'planner-public-preparation' for t in threading.enumerate())
    with pytest.raises(planner.PlannerActionBlocked):
        loop.provider_call(s, c['key'], 1, 'advisor', messages, owner, root, ControlledSlowAdvisor())
    assert s.cell(c['key'])['used']['provider_tokens'] == 10


def test_advisor_accepted_reply_recovery_no_redispatch(tmp_path):
    s, e, c, cfg, owner, root = setup(tmp_path)
    client = HintClient()
    messages = loop.AdvisorProvider().messages('open_cabinet', SOURCE, {})
    first = loop.provider_call(s, c['key'], 1, 'advisor', messages, owner, root, client)
    again = loop.provider_call(s, c['key'], 1, 'advisor', messages, owner, root, client)
    assert first == again and len(client.inputs) == 1
    rows = planner.PlannerControl(s).snapshot(c['key'])
    assert rows['requests'][0]['accepted'] is True and rows['requests'][0]['adopted'] is None and len(rows['work']) == 1


def test_unknown_provider_never_retries_or_refunds(tmp_path):
    s, e, c, cfg, owner, root = setup(tmp_path)
    class Lost:
        calls = 0
        def chat(self, **kw):
            self.calls += 1
            raise TimeoutError('controlled unknown response')
    cl = Lost()
    msgs = loop.AdvisorProvider().messages('open_cabinet', SOURCE, {})
    with pytest.raises(TimeoutError):
        loop.provider_call(s, c['key'], 1, 'advisor', msgs, owner, root, cl)
    with pytest.raises(DevConflict):
        loop.provider_call(s, c['key'], 1, 'advisor', msgs, owner, root, cl)
    assert cl.calls == 1
    ledger = s.export_ledger()
    assert ledger['goal']['reserved']['advisor_calls'] == 1
    assert ledger['goal']['used']['provider_tokens'] == 0


class TestOnlyGateway:
    __test__ = False
    def __init__(self, cfg):
        self.ctx = memory.context(cfg, 'controlled-test-only')
        self.record = SimpleNamespace(memory_id='controlled-memory', version=3, status='trusted',
            applicability=self.ctx, guidance='Use bounded world deltas.')
        self.grants = {}; self.off = False
    def store_id(self): return 'controlled-test-authority'
    def retrieve(self, context, now): return [] if self.off else [self.record]
    def authorize_dev_use(self, **kw):
        r = dict(grant_id='test-grant', memory_id=kw['memory_id'], memory_version=self.record.version,
                 development_id=kw['development_id'], source_policy_sha256=kw['source_policy_sha256'],
                 context=kw['context'], guidance=self.record.guidance, source_trace_id='test-public-trace', evidence_refs=['test-public-evidence'])
        self.grants[r['grant_id']] = r
        return r
    def get_dev_use(self, grant): return self.grants[grant]


def test_memory_exact_scope_grants_revisions_and_revocation(tmp_path):
    s, e, c, cfg, owner, root = setup(tmp_path)
    gw = TestOnlyGateway(cfg)
    settings = dict(base_url='http://127.0.0.1:1', store_id=gw.store_id(), namespace='controlled-test-only')
    service = memory.DevMemory(gw, settings, cfg)
    packet = service.packet(development_id='test:v1', source=SOURCE)
    assert packet['entries'][0]['version'] == 3
    service.recheck(packet)
    messages = loop.model_messages(cfg, SOURCE, {}, {'memory': packet}, {})
    assert json.loads(messages[1]['content'])['attention_control']['memory']['entries'][0]['grant_id'] == 'test-grant'
    for bad in ['robot', 'task_id', 'sdk_sha256', 'scene_id', 'namespace', 'object_set_id', 'controller', 'camera_config_id', 'task_variant_id']:
        old = gw.record.applicability
        gw.record.applicability = {**gw.ctx, bad: 'different'}
        assert service.eligible() == []
        gw.record.applicability = old
    gw.record.version += 1
    with pytest.raises(PermissionError): service.recheck(packet)
    gw.record.version -= 1
    gw.off = True
    with pytest.raises(PermissionError): service.recheck(packet)
    gw.off = False
    gw.record.status = 'candidate'
    assert not service.eligible()
    with pytest.raises(PermissionError): memory.DevMemory(gw, {**settings, 'store_id': 'other'}, cfg)


def test_local_work_is_once_and_digest_checked(tmp_path):
    s, e, c, cfg, owner, root = setup(tmp_path)
    add_version(s, c, root)
    r = approval(s, c, cfg)
    a = planner.prepare_independent(s, c['key'], 1, 'human', SOURCE, {}, root, r['request_id'], 5)
    b = planner.prepare_independent(s, c['key'], 1, 'human', SOURCE, {}, root, r['request_id'], 5)
    assert a == b
    p = Path(a['result']['uri']); p.chmod(0o644); p.write_text('{}')
    with pytest.raises(DevConflict):
        planner.prepare_independent(s, c['key'], 1, 'human', SOURCE, {}, root, r['request_id'], 5)


def test_wait_restart_retains_request_deadline(tmp_path):
    s, e, c, cfg, owner, root = setup(tmp_path, planner_features={'human_wait_seconds': 60})
    add_version(s, c, root)
    r = approval(s, c, cfg)
    dead = dict(owner, pid=99999999)
    s.update_cell(c['key'], owner=dead)
    j = s.reserve(c['key'], 1, 'human', {'help_wait_seconds': 60}); s.claim(j['job_id'], dead)
    s.update_cell(c['key'], owner=dead)
    with s.tx() as db:
        exp = s.get(db, 'experiments', e['experiment_id']); exp.update(owner=dead)
        s.put(db, 'experiments', e['experiment_id'], exp)
        # A real process loss affects the persisted job identity as well.
        raw=json.loads(db.execute('SELECT payload FROM v2_jobs WHERE id=?',(j['job_id'],)).fetchone()[0]);raw['owner']=dead
        db.execute('UPDATE v2_jobs SET payload=? WHERE id=?',(json.dumps(raw),j['job_id']))
    acquire(s, e['experiment_id'], owner)
    assert s.cell(c['key'])['jobs'][0]['state'] == 'claimed'
    assert approval(DevV2Store(s.path), c, cfg)['deadline_epoch'] == r['deadline_epoch']


def test_private_feedback_never_enters_preparation_or_memory(tmp_path):
    s, e, c, cfg, owner, root = setup(tmp_path)
    for field in ['native_success', 'reward', 'evaluator']:
        with pytest.raises(ValueError):
            planner.prepare_independent(s, c['key'], 1, 'human', SOURCE, {field: True}, root, 'test', 5)
    assert not planner.PlannerControl(s).snapshot(c['key'])['work']


def test_actual_service_handlers_empty_authority_contract(tmp_path):
    # Actual Service HTTP handler functions on a fresh authority, with transport
    # explicitly excluded (sandbox forbids sockets). Never real HTTP/native.
    from attention_memory_service.memory_service_api import create_app, RetrieveInput, CandidateInput, OperatorInput
    from fastapi import HTTPException
    app = create_app(tmp_path / 'authority.sqlite', api_key='controlled-api-key-0001', operator_key='controlled-operator-0002')
    routes={r.path:r for r in app.routes if hasattr(r,'endpoint')}
    assert 'development_use_evidence' in routes['/health'].endpoint()['capabilities']
    assert routes['/retrieve'].endpoint(RetrieveInput(context={'namespace':'isolated-test','perception_mode':'sim_gt','suite':'robocasa365','task_id':'open_cabinet'},now=time.time()))==[]
    auth=routes['/store'].dependant.dependencies[0].call
    with pytest.raises(HTTPException) as exc:auth(x_memory_service_key=None)
    assert exc.value.status_code==401
    with pytest.raises(HTTPException) as exc:
        routes['/candidates'].endpoint(CandidateInput(memory_id='test',request_id='new-v2-local-request',guidance='Use small deltas.',repair='Changed deltas.',applicability={'suite':'robocasa365','task_id':'open_cabinet','perception_mode':'sim_gt'},created_at=time.time()))
    assert exc.value.status_code==409 and 'answered request' in exc.value.detail
    operator=routes['/memories/{memory_id}/disable'].dependant.dependencies[0].call
    with pytest.raises(HTTPException) as exc:
        operator(x_memory_service_key='controlled-api-key-0001',x_memory_operator_key=None)
    assert exc.value.status_code==403


def test_simulated_full_loop_memory_revision_human_motion_ledger(tmp_path, monkeypatch):
    settings = dict(base_url='http://127.0.0.1:1', store_id='controlled-test-authority', namespace='controlled-test-only')
    s, e, c, cfg, owner, root = setup(tmp_path, target_depth=2, target_valid_executions=2,
        max_versions=2, help_budget=2, planner_features={'human_wait_seconds': 5, 'memory': settings})
    gw = TestOnlyGateway(cfg)
    monkeypatch.setattr(memory, 'client_for', lambda _: gw)
    done = threading.Event(); decisions = []
    def test_human():
        ctl = planner.PlannerControl(s)
        while not done.wait(.01):
            for r in ctl.snapshot(c['key'])['requests']:
                if r['request_type'] == 'approval' and r['state'] == 'waiting':
                    controlled_reply(s, r); decisions.append(r['request_id'])
    thread = threading.Thread(target=test_human); thread.start()
    client = HintClient()
    class CheckedRunner(Runner):
        def execute(self, request):
            r = planner.PlannerControl(s).snapshot(c['key'])['requests'][-1]
            assert r['state'] == 'consumed'
            assert r['binding']['code_sha256'] == request.policy_sha256
            return super().execute(request)
    # setup owns the fixture for construction; release it before scheduler acquisition.
    s.update_cell(c['key'], owner=None, state='planned')
    try:
        final = run(s, e['experiment_id'], max_cells=1, client=client, runner_factory=CheckedRunner)
    finally:
        done.set(); thread.join(3)
    cell = final['cells'][0]
    assert cell['state'] == 'completed' and len(decisions) == 2
    assert cell['used']['executions'] == 2 and cell['used']['dev_calls'] == 2
    assert s.export_ledger()['recompute_matches'] and not any(s.export_ledger()['goal']['reserved'].values())
    for v in cell['versions']:
        inp = json.loads(Path(v['model_input']['uri']).read_bytes())
        packet = json.loads(inp['messages'][1]['content'])
        entries = packet['attention_control']['help']['content']
        assert entries[0]['memory_id'] == 'controlled-memory' and entries[0]['version'] == 3
        assert Path(v['source']['uri']).with_name('memory-result.json').exists()
    from benchmarks.attention_harness.dev_report import report
    result = report(s, e['experiment_id'])
    # Full report recomputes human wait from receipts, with no fake hint field.
    assert result['resource_ledger']['recompute_matches']
    assert not cell['planner']['work'][0]['result']['uri'].endswith('policy.py')


def test_absolute_goal_deadline_cannot_be_extended_by_request_restart(tmp_path):
    s, e, c, cfg, owner, root = setup(tmp_path)
    add_version(s, c, root)
    with s.tx() as db:
        g = s.get(db, 'goals', e['goal_id']); g['authorized_until'] = time.time() + .1
        s.put(db, 'goals', e['goal_id'], g)
    r = approval(s, c, cfg, timeout=60)
    assert r['deadline_epoch'] <= time.time() + .1
    time.sleep(.12)
    assert planner.PlannerControl(DevV2Store(s.path)).get(r['request_id'])['state'] == 'timed_out'
    assert s.goal(e['goal_id'])['wall_remaining'] == 0


def test_cancel_between_request_and_wait_claim_releases_only_unclaimed(tmp_path, monkeypatch):
    s, e, c, cfg, owner, root = setup(tmp_path)
    _, d = add_version(s, c, root)
    original = s.claim
    def before_claim(*args, **kw):
        cancel(s, e['experiment_id'])
        return original(*args, **kw)
    monkeypatch.setattr(s, 'claim', before_claim)
    with pytest.raises(planner.PlannerActionBlocked, match='cancelled'):
        planner.wait_for_approval(s, c['key'], 1, SOURCE, cfg, owner, d, c['planner_features'], {})
    assert s.cell(c['key'])['jobs'][0]['state'] == 'released'
    assert not any(s.export_ledger()['goal']['reserved'].values())
    assert not any(s.export_ledger()['goal']['used'].values())


def test_http_handler_separate_human_capability_and_exact_binding(tmp_path, monkeypatch):
    import io
    from benchmarks.attention_harness import dev_ui_server as ui
    s, e, c, cfg, owner, root = setup(tmp_path)
    add_version(s, c, root)
    r = approval(s, c, cfg)
    monkeypatch.setenv('ATTENTION_HUMAN_APPROVAL_TOKEN', 'controlled-human-capability-12345')
    monkeypatch.setattr(ui, 'ThreadingHTTPServer', lambda address, cls: SimpleNamespace(handler=cls))
    server = ui.serve(s, tmp_path / 'artifacts', 0)
    def post(token, body):
        h = object.__new__(server.handler)
        raw = json.dumps(body).encode()
        h.path = f"/api/dev/experiments/{e['experiment_id']}/approvals/{r['request_id']}/decision"
        h.rfile = io.BytesIO(raw)
        h.headers = {'Content-Length': str(len(raw)), 'Content-Type': 'application/json', 'X-Attention-Human-Token': token}
        h.authorized = lambda: True  # Outer ordinary UI capability, not human.
        result = []; h.obj = lambda data, status=200: result.append((status, data))
        h.do_POST()
        return result[0]
    body = dict(binding=r['binding'], approved=True, response_id='controlled-http-test')
    assert post('', body)[0] == 403
    bad = copy.deepcopy(body); bad['binding']['code_sha256'] = '0'*64
    assert post('controlled-human-capability-12345', bad)[0] == 409
    assert post('controlled-human-capability-12345', body)[1]['state'] == 'approved'
    assert post('controlled-human-capability-12345', body)[1]['state'] == 'approved'
    assert not s.cell(c['key'])['jobs']


def test_provider_owned_process_cancelled_and_reaped_without_network(tmp_path, monkeypatch):
    import subprocess
    import sys
    s, e, c, cfg, owner, root = setup(tmp_path)
    request = immutable(root / 'model-input.json', {})
    real_popen = subprocess.Popen
    spawned = []
    def harmless_popen(*args, **kw):
        assert 'ATTENTION_HUMAN_APPROVAL_TOKEN' not in kw['env']
        p = real_popen([sys.executable, '-c', 'import time; time.sleep(60)'], **kw)
        spawned.append(p)
        cancel(s, e['experiment_id'])
        return p
    monkeypatch.setenv('ATTENTION_HUMAN_APPROVAL_TOKEN', 'controlled-secret-never-forwarded')
    monkeypatch.setattr(loop.subprocess, 'Popen', harmless_popen)
    with pytest.raises(DevConflict, match='interrupted'):
        loop.contained_provider(s, c['key'], 'model', request, root, time.time()+5)
    assert spawned and spawned[0].poll() is not None
    assert not any(s.export_ledger()['goal']['used'].values())
    with s.tx() as db:
        assert json.loads(db.execute("SELECT payload FROM v2_events WHERE kind='planner_provider_cleanup'").fetchone()[0])['reaped']


def test_advisor_version_change_invalidates_response(tmp_path):
    s, e, c, cfg, owner, root = setup(tmp_path)
    messages = loop.AdvisorProvider().messages('open_cabinet', SOURCE, {})
    scope = planner.advisor_scope(s, c['key'], 1, messages)
    ctl = planner.PlannerControl(s)
    r = ctl.request(c['key'], 1, 'advisor', scope, 60)
    add_version(s, c, root)  # A different accepted generation while reply pending.
    row = ctl.finish_advisor(r['request_id'], response={'uri':'controlled','sha256':'test'}, current_scope=scope)
    assert row['state'] == 'invalidated' and row['adopted'] is False


def test_ui_keeps_five_modules_and_no_human_secret_persistence():
    import re
    page = Path('benchmarks/attention_harness/dev_ui/index.html').read_text()
    assert re.findall(r'data-core-region="([^"]+)"', page) == ['settings','status','execution','request','resources']
    js = Path('benchmarks/attention_harness/dev_ui/app.js').read_text()
    assert 'X-Attention-Human-Token' in js and 'human-capability' in js
    assert 'localStorage' not in js and 'sessionStorage' not in js
    main = Path('benchmarks/attention_harness/ui/live.js').read_text()
    assert '${runSettings(snapshot)}${runOverview(snapshot)}${executionViewer(snapshot)}${requestPanel(snapshot,chosen)}${resourceDetails(snapshot)}' in main
    assert 'data-planner-details' in main


def test_advisor_acceptance_is_not_reported_as_generation_use(tmp_path):
    s, e, c, cfg, owner, root = setup(tmp_path)
    msgs = loop.AdvisorProvider().messages('open_cabinet', SOURCE, {})
    _, _, response, _ = loop.provider_call(s,c['key'],1,'advisor',msgs,owner,root,HintClient())
    ctl=planner.PlannerControl(s)
    row=ctl.snapshot(c['key'])['requests'][0]
    assert row['accepted'] is True and row['adopted'] is None
    guidance=json.loads(Path(response['uri']).read_bytes())['content']
    inp=immutable(root/'controlled-model-input.json',{'messages':loop.model_messages(cfg,SOURCE,{}, {'help':{'content':guidance}}, {})})
    src=immutable(root/'controlled-policy.py',SOURCE)
    ctl.associate_generation(c['key'],1,inp,src)
    row=ctl.snapshot(c['key'])['requests'][0]
    assert row['adopted'] is True and row['adoption']['result_source']==src
    assert s.export_ledger()['planner_control']['background_actual_seconds']>=0


def test_memory_generation_replay_and_lifecycle_fail_closed(tmp_path,monkeypatch):
    s,e,c,cfg,owner,root=setup(tmp_path)
    gw=TestOnlyGateway(cfg)
    settings=dict(base_url='http://127.0.0.1:1',store_id=gw.store_id(),namespace='controlled-test-only')
    monkeypatch.setattr(memory,'client_for',lambda _:gw)
    packet,service=memory.load_memory(s,c['key'],1,SOURCE,cfg,settings,root)
    assert packet['entries']
    for status in ['disabled','expired','rolled_back','candidate']:
        gw.record.status=status
        with pytest.raises(PermissionError):service.recheck(packet)
    gw.record.status='trusted'
    with pytest.raises(PermissionError,match='generation/source'):
        memory.load_memory(s,c['key'],2,SOURCE,cfg,settings,root)
    with pytest.raises(PermissionError,match='generation/source'):
        memory.load_memory(s,c['key'],1,SENSOR,cfg,settings,root)
    changed=copy.deepcopy(packet);changed['entries'][0]['authority']='other'
    with pytest.raises(PermissionError,match='authority/context'):service.recheck(changed)


def test_approval_expiring_at_claim_releases_unclaimed_native_only(tmp_path,monkeypatch):
    s,e,c,cfg,owner,root=setup(tmp_path)
    record,d=add_version(s,c,root)
    r=approval(s,c,cfg);controlled_reply(s,r)
    monkeypatch.setattr(planner,'wait_for_approval',lambda *a,**kw:r['request_id'])
    original=s.claim
    def expired_claim(jid,*args,**kw):
        with s.tx() as db:
            ctl=planner.PlannerControl.__new__(planner.PlannerControl);ctl.store=s
            row=json.loads(db.execute('SELECT payload FROM v2_planner_requests WHERE id=?',(r['request_id'],)).fetchone()[0])
            row['deadline_epoch']=time.time()-1;ctl.save(db,row)
        return original(jid,*args,**kw)
    monkeypatch.setattr(s,'claim',expired_claim)
    with pytest.raises(planner.PlannerActionBlocked,match='invalidated'):
        loop.execute(s,c['key'],1,record,cfg,owner,d,Runner)
    job=s.cell(c['key'])['jobs'][0]
    assert job['kind']=='execute' and job['state']=='released'
    assert not any(s.export_ledger()['goal']['reserved'].values())


def test_legacy_server_start_does_not_add_planner_tables(tmp_path,monkeypatch):
    from benchmarks.attention_harness import dev_ui_server as ui
    s=DevV2Store(tmp_path/'legacy.sqlite')
    with s.tx() as db:before=list(db.execute('SELECT name,sql FROM sqlite_master ORDER BY name'))
    monkeypatch.setattr(ui,'ThreadingHTTPServer',lambda address,cls:SimpleNamespace(handler=cls))
    ui.serve(s,tmp_path/'artifacts',0)
    with s.tx() as db:after=list(db.execute('SELECT name,sql FROM sqlite_master ORDER BY name'))
    assert before==after


@pytest.mark.parametrize('source',[
    'from robot_sdk import left_arm\narm=left_arm\narm.move_delta(0,0,0.01)\n',
    'from robot_sdk import left_arm\nmove=left_arm.move_delta\nmove(0,0,0.01)\n',
    "from robot_sdk import left_arm\ngroups={'a':left_arm}\ngroups['a'].move_delta(0,0,0.01)\n",
    'from robot_sdk import left_gripper\ngrip=left_gripper\nrelease=grip.open\nrelease()\n'])
def test_motion_aliases_cannot_bypass_approval(tmp_path,source):
    from openarm365_dev.policy_validation import validate_generated_policy
    validate_generated_policy(source)
    s,e,c,cfg,owner,root=setup(tmp_path)
    add_version(s,c,root,source)
    scope=planner.binding(c['key'],1,source,cfg)
    assert scope and scope['scope']['operations']
    job=s.reserve(c['key'],1,'execute',{'executions':1})
    with pytest.raises(DevConflict,match='requires human approval'):s.claim(job['job_id'],owner)
    assert not s.cell(c['key'])['jobs'][0].get('claimed_at')
