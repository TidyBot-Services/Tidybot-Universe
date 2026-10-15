from dataclasses import replace
import json
from pathlib import Path

from benchmarks.attention_harness.attention_modes import AssistanceMode
from benchmarks.attention_harness.core.advisor import AdvisorProxy
from benchmarks.attention_harness.core.models import RunStatus
from benchmarks.attention_harness.core.runtime import AttentionRuntime
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.request_lifecycle import request_timeline, main
from benchmarks.attention_harness.ui_server import dashboard_snapshot
from benchmarks.attention_harness.tests.test_attention_store import records


def human(tmp_path):
    store=AttentionStore(tmp_path/'store.sqlite')
    run,attempt,trace,request=records()
    request=replace(request,mode=AssistanceMode.LIVE_HUMAN_FIRST,deadline_at=20)
    store.create_run(run);store.create_attempt(attempt);store.put_trace(trace)
    runtime=AttentionRuntime(store,None,clock=lambda:8)
    runtime.open_request(request)
    return store,request,runtime


def test_human_timeline_adoption_execution_candidate_and_restart(tmp_path,capsys):
    store,request,runtime=human(tmp_path)
    store.defer_request(request.request_id,deferred_at=6)
    runtime.submit_human_response(request.request_id,response_id='reply',content='move left')
    store.record_response_use(request.request_id,response_id='reply',use='guidance')
    store.link_response_execution(request.request_id,execution_id='execution-2')
    store._audit_event('candidate',request.request_id,'memory.candidate_ingested',
        {'request_id':request.request_id,'memory_id':'candidate-1','run_id':request.run_id})
    before=store.path.read_bytes()
    timeline=request_timeline(store,request.run_id)
    row=timeline['requests'][0]
    assert row['times']['pending']==[4]
    assert row['times']['deferred']==[6]
    assert row['times']['answered']==[8]
    assert row['response_latency_seconds']==4
    assert row['used_response']['content']=='move left'
    assert row['disposition']=='accepted' and row['state']=='answered'
    assert row['execution_links'][0]['execution_id']=='execution-2'
    assert row['candidate_memories'][0]['memory_id']=='candidate-1'
    assert main(['--store-path',str(store.path),'--run-id',request.run_id])==0
    assert json.loads(capsys.readouterr().out)==timeline
    assert store.path.read_bytes()==before
    assert request_timeline(AttentionStore(store.path),request.run_id)==timeline
    assert dashboard_snapshot(store,request.run_id)['request_timeline']==timeline


def test_ignore_fallback_cancel_does_not_invent_reply(tmp_path):
    store,request,runtime=human(tmp_path)
    runtime.decline(request.request_id)
    runtime.cancel(request.request_id)
    row=request_timeline(store,request.run_id)['requests'][0]
    assert row['times']['ignored']==[8]
    assert row['times']['fallback']==[8]
    assert row['times']['cancelled']==[8]
    assert row['reply'] is None and row['used_response'] is None
    assert row['response_latency_seconds'] is None


def test_timeout_fallback_answer_and_ignored_disposition(tmp_path):
    store,request,runtime=human(tmp_path)
    runtime.clock=lambda:21
    runtime.proxy=AdvisorProxy(store,transport=lambda _: 'fallback reply',sleeper=lambda _:None)
    runtime.handle_deadline(request.request_id)
    store.record_response_ignored(request.request_id,reason='attempts_exhausted',ignored_at=22)
    row=request_timeline(store,request.run_id)['requests'][0]
    assert row['times']['timeout']==[21]
    assert row['times']['fallback']==[21]
    assert row['times']['answered']==[21]
    assert row['times']['ignored']==[22]
    assert row['reply']['content']=='fallback reply'
    assert row['used_response'] is None and row['disposition']=='ignored'


def test_legacy_missing_transition_clock_is_explicit(tmp_path):
    store,request,runtime=human(tmp_path)
    runtime.cancel(request.request_id)
    with store._transaction() as db:
        row=db.execute('SELECT payload FROM events WHERE event_key=?',(f'cancel:{request.request_id}',)).fetchone()
        value=json.loads(row[0]);value.pop('transitioned_at')
        db.execute('UPDATE events SET payload=? WHERE event_key=?',(json.dumps(value),f'cancel:{request.request_id}'))
    last=request_timeline(store,request.run_id)['requests'][0]['transitions'][-1]
    assert last['at'] is None and last['time_source']=='unrecorded'
