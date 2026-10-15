import json
from pathlib import Path

import pytest

from benchmarks.attention_harness.core.runtime import AttentionRuntime
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.tests.test_attention_store import populated_store
from benchmarks.attention_harness.formal_attention_run import run_formal_attention
from test_formal_attention_chain import FakeFormalRunner
from test_async_requests import run, advisor_reply


def test_summary_packet_removes_nested_trace_evidence_for_supplied_and_persisted(tmp_path):
    store, request=populated_store(tmp_path/'store.sqlite')
    runtime=AttentionRuntime(store,None,clock=lambda:10,trace_evidence=False,memory_enabled=False)
    supplied={'trace_id':request.trace_id,'failure':{'message':'miss','evidence_ids':['private-ref']},
        'events':[{'message':'event-content'}], 'evidence':[{'content':'evidence-content'}],
        'assessment':{'risk':'none'}, 'memory_guidance':'memory-content',
        'failure_history':[{'attempt_index':0,'evidence_refs':['prior-ref'],
                            'failure':{'message':'prior miss','events':['nested']}}]}
    packet=runtime._advisor_packet(request,supplied)
    assert packet=={'trace_id':request.trace_id,'failure':{'message':'miss'},
                    'failure_history':[{'attempt_index':0,'failure':{'message':'prior miss'}}]}
    persisted=runtime._advisor_packet(request,None)
    assert persisted['failure']['message']=='cube slipped'
    assert not {'events','evidence','assessment','memory_guidance'} & persisted.keys()


def test_full_planner_without_trace_still_asks_using_only_failure_summary(tmp_path,monkeypatch):
    import benchmarks.attention_harness.sim_gt_attention_run as scheduler
    def forbidden(*args,**kwargs):pytest.fail('assess_trace called in ablation')
    monkeypatch.setattr(scheduler,'assess_trace',forbidden)
    packets=[]
    def advisor(request):
        packets.append(json.loads(request['messages'][1]['content'])['trace_packet'])
        assert len(request['messages'])==2
        return advisor_reply(request)
    result=run(tmp_path,lambda sdk,ctx:None,policy_id='full_trace_aware_attention_planner',
               trace_evidence=False,memory_enabled=False,advisor_transport=advisor,max_attempts=4)
    assert packets and all(set(p)=={'trace_id','failure','failure_history'} for p in packets)
    assert all(d['assessment'] is None for d in result['decisions'])
    assert not any(d['action'] in {'inspect_trace','retrieve_memory'} for d in result['decisions'])
    assert result['trace_evidence'] is False and result['memory_enabled'] is False
    assert AttentionStore(Path(result['store'])).scheduler_options('run:'+Path(result['artifact_dir']).name)['trace_evidence'] is False


def test_memory_disabled_skips_retrieval_in_native_binding(tmp_path,monkeypatch):
    from attention_memory_service import MemoryService
    monkeypatch.setattr(MemoryService,'retrieve',lambda *a,**kw:pytest.fail('Memory retrieved'))
    result=run(tmp_path,lambda sdk,ctx:None,policy_id='full_trace_aware_attention_planner',memory_enabled=False)
    assert not any(d['action']=='retrieve_memory' for d in result['decisions'])


def test_formal_executor_memory_off_and_scheduler_flags(tmp_path):
    import hashlib
    code=tmp_path/'code.py';code.write_text('pass\n')
    config=tmp_path/'config.json';config.write_text('{}')
    class ForbiddenGateway:
        def retrieve(self,*args,**kwargs):pytest.fail('formal Memory retrieval')
        def get_memory(self,*args,**kwargs):pytest.fail('formal Memory read')
    result=run_formal_attention(suite='robosuite',task_id='cube_lift',seed=101,
        artifact_root=tmp_path/'runs',policy_id='full_trace_aware_attention_planner',
        policy_code_path=code,approved_policy_sha256=hashlib.sha256(code.read_bytes()).hexdigest(),
        config_path=config,approved_config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
        runner=FakeFormalRunner('robosuite'),memory_gateway=ForbiddenGateway(),
        memory_enabled=False,trace_evidence=False,advisor_replay_only=True,max_attempts=1,sleeper=lambda _:None)
    assert {k:result['scheduler_config'][k] for k in ['memory_enabled','trace_evidence','advisor_replay_only']}=={
        'memory_enabled':False,'trace_evidence':False,'advisor_replay_only':True}
