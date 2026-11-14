"""Human and assisted replies through the offline broker/export/import chain."""
import json
import threading
import time
from pathlib import Path

import pytest

from benchmarks.attention_harness import dev_agent_human_reply as human
from benchmarks.attention_harness import memory_auto_import as importer, memory_provisional as provisional
from benchmarks.attention_harness import memory_run_notes
from benchmarks.attention_harness.tests.test_dev_agent_d7 import fixture, events
from benchmarks.attention_harness.tests.test_dev_agent_memory_v2 import rig, proposal
from benchmarks.attention_harness.tests.test_memory_provisional import REJECTED


def reply(f, *, assisted=False, skip=False):
    f.helper.config = dict(f.helper.config, human_reply_seconds=30)
    failures = []
    def send():
        try:
            until = time.monotonic()+10
            while time.monotonic() < until:
                wait = human.pending(f.ws.private)
                if wait:
                    human.submit(f.ws.private, wait['execution_id'], content='Lift slowly before moving.',
                                 replied_by='alice', assisted=assisted, skip=skip)
                    return
                time.sleep(.005)
            raise AssertionError('no open human wait')
        except Exception as exc:failures.append(exc)
    thread = threading.Thread(target=send);thread.start()
    try:answer = f.call('ask_help', {'question':'How to lift?'})['result']
    finally:thread.join(timeout=12)
    assert not thread.is_alive() and not failures, failures
    return answer


@pytest.fixture
def human_proposal(rig):
    f = rig();f.call('submit');answer = reply(f);f.call('submit')
    assert answer['provider'] == 'human'
    return f, proposal(f)


def test_human_note_proposal_and_service_import(human_proposal, tmp_path):
    from attention_memory_service.memory_service import MemoryService
    f, value = human_proposal
    note = json.loads((f.root/'v2/memory-run-note.json').read_bytes())
    payload = importer.provisional_payload(value, 'test')
    for row in (note, note['origin'], value['origin'], payload['origin']):
        assert row['replied_by'] == 'alice'
        assert row['hint_human_seconds'] > 0 and row['actual_human_seconds'] >= 0
        assert all(k in row for k in provisional.HUMAN_ATTRIBUTION)
    assert note['provider'] == value['provider'] == payload['origin']['provider'] == 'human'
    assert note['unvalidated'] and note['excluded_from_results']
    evidence = payload['agent_evidence']['help']
    assert {'human_reply','human_wait','human_outcome'} <= evidence.keys() and 'advisor' not in evidence
    outcome, wait = (json.loads(evidence[k]) for k in ('human_outcome','human_wait'))
    assert note['actual_human_seconds'] == outcome['at']-wait['started_at']
    svc = MemoryService(tmp_path/'svc.sqlite')
    counts = importer.run([f.store.path], client=svc, namespace='test', out=lambda *a:None)
    assert counts['created'] == 1 and counts['refused'] == counts['deferred'] == 0, counts
    mid = provisional.memory_id('exp','cell',2)
    assert svc.v2.provenance(mid)['origin'] == payload['origin']
    summary = svc.get_dev_v2_memory_public(mid)
    assert summary['origin_summary']['replied_by'] == 'alice'


@pytest.mark.parametrize('change', ['content', 'late', 'skip', 'timed_out', 'missing_operator', 'assisted'])
def test_invalid_human_evidence_never_creates_either_note(rig, change):
    f = rig();f.call('submit');reply(f)
    folder = f.ws.private/'help/e000001'
    path = folder/human.OUTCOME;outcome = json.loads(path.read_bytes())
    if change == 'content':outcome['content'] = 'Tampered'
    elif change == 'late':outcome['at'] = json.loads((folder/human.WAIT).read_bytes())['deadline_at']+1
    elif change in ('skip', 'timed_out'):outcome['kind'] = change
    elif change == 'missing_operator':outcome.pop('replied_by')
    else:outcome['assisted'] = True
    path.write_text(json.dumps(outcome));f.call('submit')
    assert not (f.root/'v2/memory-run-note.json').exists()
    assert not (f.root/'v2/memory-provisional-proposal.json').exists()
    assert events(f,'planner_memory_provisional_skipped')


def test_human_digest_drift_is_refused(human_proposal):
    f, value = human_proposal
    bundle = json.loads(Path(value['refs']['agent_help_evidence']['uri']).read_bytes())
    path = Path(bundle['refs']['human_outcome']['uri']);path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(importer.Refused,match='digest drift'):importer.provisional_payload(value,'test')


def test_assisted_reply_delivered_without_notes_or_human_attention(rig):
    from benchmarks.attention_harness.dev_agent_view import projection
    f = rig();f.call('submit');answer = reply(f, assisted=True);f.call('submit')
    assert answer['provider'] == 'human_assisted' and answer['delivered']
    assert answer['content'] == 'Lift slowly before moving.'
    assert answer['hint_human_seconds'] > 0  # Raw estimate remains auditable.
    assert not (f.root/'v2/memory-run-note.json').exists()
    assert not (f.root/'v2/memory-provisional-proposal.json').exists()
    for kind in ('planner_memory_provisional_skipped','planner_memory_run_note_skipped'):
        assert events(f,kind)[-1]['reason'] == 'assisted_reply_not_source'
    attention = f.ledger.snapshot()['attention']
    assert attention['hint_human_seconds'] == attention['actual_human_seconds'] == 0
    assert attention['help_count'] == attention['help_by_provider']['human_assisted'] == 1
    report = projection(f.store, f.store.cell('cell'))
    assert report['agent']['attention']['help_by_provider']['human_assisted'] == 1
    assert report['agent']['attention']['hint_human_seconds'] == 0


@pytest.mark.parametrize('kind', ['skip','timed_out'])
def test_fallback_keeps_advisor_source(rig, kind):
    f = rig();f.call('submit')
    if kind == 'skip':answer = reply(f,skip=True)
    else:
        f.helper.config = dict(f.helper.config,human_reply_seconds=.02)
        answer = f.call('ask_help',{'question':'Help?'})['result']
    assert answer['provider'] == 'advisor'
    f.call('submit');value = proposal(f)
    assert value['provider'] == 'advisor'
    assert 'advisor' in importer.agent_evidence(value)['help']
    assert 'human_outcome' not in importer.agent_evidence(value)['help']


def test_ineffective_human_is_skipped(rig):
    f = rig();f.call('submit');reply(f)
    execute = f.executor.execute
    def unchanged(*a, **kw):
        result = execute(*a, **kw)
        path = f.executor.episode/'trace.json';path.chmod(0o644)
        path.write_text(json.dumps(dict(status='failed',error=REJECTED['public_error'],sdk_events=REJECTED['action_trace'])))
        return result
    f.executor.execute = unchanged;f.call('submit')
    assert not (f.root/'v2/memory-run-note.json').exists()
    assert not (f.root/'v2/memory-provisional-proposal.json').exists()
    assert events(f,'planner_memory_provisional_skipped')[-1]['reason'] == 'intervention not effective'


def test_human_cannot_use_old_planner_path(human_proposal):
    f, value = human_proposal
    with pytest.raises(provisional.NotStaged, match='human_requires_dev_agent'):
        provisional.stage(f.store,'cell',2,f.root/'v2')
    assert memory_run_notes.create(f.store,'cell',2,f.root/'v2') is None
    assert events(f,'planner_memory_run_note_skipped')[-1]['reason'] == 'human_requires_dev_agent'


def test_local_submit_records_operator_and_assisted_boolean(tmp_path):
    folder = tmp_path/'help/e000001';folder.mkdir(parents=True)
    (folder/human.WAIT).write_text(json.dumps(dict(execution_id='e000001',started_at=time.time(),deadline_at=time.time()+60)))
    with pytest.raises(ValueError,match='boolean'):
        human.submit(tmp_path,'e000001',content='Lift.',assisted='false')
    with pytest.raises(ValueError,match='operator'):
        human.submit(tmp_path,'e000001',content='Lift.',replied_by=' ')
    human.submit(tmp_path,'e000001',content='Lift.')
    outcome = json.loads((folder/human.OUTCOME).read_bytes())
    assert outcome['replied_by'] == 'operator' and outcome['assisted'] is False


@pytest.mark.parametrize('remote', [False, True])
@pytest.mark.parametrize('assisted', [False, True])
def test_reply_http_records_server_identity_without_sockets(tmp_path, monkeypatch, remote, assisted):
    import re
    from types import SimpleNamespace
    from benchmarks.attention_harness import ui_server
    from benchmarks.attention_harness.core.store import AttentionStore
    from benchmarks.attention_harness.daily_dev_v2 import DailyDevV2
    from benchmarks.attention_harness.dev_agent_secret import Secret
    from benchmarks.attention_harness.gateway_auth import control_token_for
    from benchmarks.attention_harness.tests.fakes.byok_http import MemoryServer, exchange
    from remote_gateway import policy
    from remote_gateway.upstream import signature
    eid = 'devexp-'+'a'*24;cell = 'b'*64
    folder = tmp_path/'cell/agent/private/help/e000001';folder.mkdir(parents=True)
    (folder/human.WAIT).write_text(json.dumps(dict(execution_id='e000001',started_at=time.time(),deadline_at=time.time()+60)))
    bridge = object.__new__(DailyDevV2)
    bridge.store = lambda _:SimpleNamespace(cell=lambda _:dict(experiment_id=eid,artifact_dir=str(tmp_path/'cell')))
    monkeypatch.setattr(ui_server,'ThreadingHTTPServer',MemoryServer)
    secret = Secret('synthetic-gateway-secret')
    server = ui_server.create_server(store=AttentionStore(tmp_path/'ui.sqlite'),port=0,dev_v2_bridge=bridge,
                                    gateway_secret=secret if remote else None)
    body = dict(cell=cell,execution_id='e000001',content='Lift.',assisted=assisted)
    if remote:body = policy.help_reply(dict(body,replied_by='spoofed'))
    raw = json.dumps(body).encode();path = f'/api/dev-v2/experiments/{eid}/help-reply'
    if remote:
        headers = {'X-Attention-Token':control_token_for(secret),'X-Attention-Owner':'alice',
                   'X-Attention-Gateway-Auth':signature(secret.reveal().encode(),'alice','POST',path,raw,time.time())}
    else:
        status, html, _ = exchange(server,'GET','/ui/')
        assert status == 200
        headers = {'X-Attention-Token':re.search(rb'data-control-token="([^"]+)"',html)[1].decode()}
    status, result, _ = exchange(server,'POST',path,raw=raw,headers=headers)
    assert status == 200, result
    outcome = json.loads((folder/human.OUTCOME).read_bytes())
    assert outcome['replied_by'] == ('alice' if remote else 'operator')
    assert outcome['assisted'] is assisted
