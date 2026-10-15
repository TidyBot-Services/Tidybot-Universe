from __future__ import annotations
import hashlib,json,os,sys,time
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import pytest
REPO=Path(os.environ.get('ATTENTION_REPO',Path(__file__).resolve().parents[3]))
sys.path[:0]=[str(REPO),str(REPO/'benchmarks/attention_harness/tests'),str(REPO/'skill-agent-setup/claude-code')]
from benchmarks.attention_harness.core.store import AttentionStore,StateConflictError
from benchmarks.attention_harness.core.runtime import AttentionRuntime
from benchmarks.attention_harness.core.models import RunStatus
from benchmarks.attention_harness.attention_modes import AssistanceMode
from benchmarks.attention_harness.tests.test_attention_store import records
from benchmarks.attention_harness.tests.test_formal_attention_chain import FakeFormalRunner
from benchmarks.attention_harness.tests.test_guidance_adoption_v1 import PublicBackend
from benchmarks.attention_harness.robosuite_memory.formal_sandbox import execute_formal_policy
from benchmarks.attention_harness.formal_attention_run import run_formal_attention
from benchmarks.attention_harness.ui_server import dashboard_snapshot
from tidybot_sdk import TidyBotSDK
POLICY=REPO/'benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_guidance_v1_1_dev_2026-10-01/base/robosuite_cube_lift/policy.py'
ZH='请检查夹爪与方块是否对齐，降低抓取高度，夹紧后确认抓牢，再向上抬起。'
AB='AB_CONTROL_V1 grasp_offset_m=0.005;approach_tolerance_m=0.004;close_settle_steps=30;'
EN='Check the grasp height and lower it slightly. Close the gripper and confirm a secure grip before lifting.'

def chain(tmp_path,text):
 class WorkerRunner(FakeFormalRunner):
  def __init__(self):super().__init__('robosuite');self.commands=[]
  def execute(self,request):
   result=super().execute(request);events=[]
   worker=execute_formal_policy(code=POLICY.read_text(),sdk=TidyBotSDK(PublicBackend('robosuite',0),event_sink=events.append),context={'suite':'robosuite','task_id':'cube_lift','attention_input':request.attention_input},deadline=time.monotonic()+10,stderr_path=tmp_path/f'worker-{len(self.requests)}.stderr')
   assert worker.status=='completed',worker.error
   self.commands.append(events)
   trace_ref=result['artifacts']['trace'];trace=json.loads(Path(trace_ref['uri']).read_text());trace['sdk_events']=events
   Path(trace_ref['uri']).write_text(json.dumps(trace));trace_ref['sha256']=hashlib.sha256(Path(trace_ref['uri']).read_bytes()).hexdigest()
   # Isolated native-verdict fixture; never presented as a real task result.
   if len(self.requests)==2:
    result['native_success']=True;result['native_evaluator']['native_success']=True
    ref=result['artifacts']['native_result'];value=json.loads(Path(ref['uri']).read_text());value['native_success']=True;Path(ref['uri']).write_text(json.dumps(value));ref['sha256']=hashlib.sha256(Path(ref['uri']).read_bytes()).hexdigest()
   return result
 runner=WorkerRunner();store_path=tmp_path/'store.sqlite3';config=tmp_path/'config.json';config.write_text('{}')
 def respond(_):
  store=AttentionStore(store_path)
  for event in store.events():
   if event['event_type']=='request.created':
    request=store.get_request(event['entity_id'])
    if request.state.value=='pending':AttentionRuntime(store,None,clock=time.time).submit_human_response(request.request_id,response_id=f'test-response:{request.request_id}',content=text)
 result=run_formal_attention(suite='robosuite',task_id='cube_lift',seed=101,artifact_root=tmp_path/'runs',policy_id='reactive_help',policy_code_path=POLICY,approved_policy_sha256=hashlib.sha256(POLICY.read_bytes()).hexdigest(),config_path=config,approved_config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),runner=runner,max_attempts=2,assistance_credits=1,assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST,human_deadline_seconds=10,sleeper=respond,store_path=store_path,advisor_transport=lambda _:pytest.fail('Unexpected proxy fallback'))
 store=AttentionStore(store_path);run_id=f"run:{Path(result['artifact_dir']).name}"
 return runner,result,store,dashboard_snapshot(store,run_id,artifact_root=tmp_path/'runs')

def test_r4_02_success_trace_survives_restart_links_four_layers_without_exposing_evaluator(tmp_path):
 runner,result,store,snapshot=chain(tmp_path,AB)
 latest=result['attempts'][-1]['attention_trace'];request_id=result['requests'][0]['request_id']
 assert snapshot['station']['latest_execution']['execution_id']==latest['execution_id']
 evidence=snapshot['response_evidence'][request_id]
 assert evidence['association']['attempt_id']==latest['attempt_id']
 assert evidence['control_comparison']['status']=='visible_commands_differ'
 assert evidence['control_comparison']['after'] and evidence['trace_digests']['after']['source_sha256']
 assert evidence['native_outcome']=={'status':'verified','value':True,'attempt_id':latest['attempt_id']}
 assert evidence['causal_adoption']=='unconfirmed'
 packet=snapshot['traces'][latest['advisor_trace_id']]
 assert not any(e['event_type']=='evaluator.result' for e in packet['events'])
 assert store.get_raw_trace(latest['raw_trace_id'])['outcome']['native_success'] is True

def test_r4_05_ordinary_chinese_is_compiled_and_changes_actual_worker_commands(tmp_path):
 runner,result,store,snapshot=chain(tmp_path,ZH)
 assert runner.requests[1].attention_input['advisor_guidance']==AB
 closing=[e['arguments']['settle_steps'] for e in runner.commands[1] if e['operation']=='close']
 assert closing==[30]
 assert runner.commands[0]!=runner.commands[1]
 request_id=result['requests'][0]['request_id'];use=snapshot['response_uses'][request_id]
 assert use['guidance_compilation']['original']==ZH
 assert use['guidance_compilation']['parameters']=={'grasp_offset_m':.005,'approach_tolerance_m':.004,'close_settle_steps':30}
 assert snapshot['responses'][use['response_id']]['content']==ZH

def test_english_ui_preset_matches_chinese_controls_and_preserves_original(tmp_path):
 from benchmarks.attention_harness.hint_compilation import prepare_human_guidance
 parameters={'grasp_offset_m':.005,'approach_tolerance_m':.004,'close_settle_steps':30}
 english=prepare_human_guidance(EN,suite='robosuite',task_id='cube_lift')
 chinese=prepare_human_guidance(ZH,suite='robosuite',task_id='cube_lift')
 assert english['status']=='supported' and english['rule']=='policy_text_parameters_v1'
 assert english['original']==english['compiled']==EN
 assert english['parameters']==chinese['parameters']==parameters
 en_dir=tmp_path/'english';en_dir.mkdir();zh_dir=tmp_path/'chinese';zh_dir.mkdir()
 runner,_,_,snapshot=chain(en_dir,EN)
 zh_runner,_,_,_=chain(zh_dir,ZH)
 assert runner.requests[1].attention_input['advisor_guidance']==EN
 commands=lambda r:[(e['operation'],e['arguments']) for e in r.commands[1]]
 assert commands(runner)==commands(zh_runner),'Frozen policy must issue the same supported SDK commands'
 assert [e['arguments']['settle_steps'] for e in runner.commands[1] if e['operation']=='close']==[30]
 use=next(iter(snapshot['response_uses'].values()))
 assert snapshot['responses'][use['response_id']]['content']==EN
 assert use['guidance_compilation']['original']==EN
 assert use['guidance_compilation']['parameters']==parameters

@pytest.mark.parametrize('terminal',[RunStatus.FAILED,RunStatus.CANCELLED,RunStatus.COMPLETED])
def test_r4_04_terminal_release_visible_and_idempotent(tmp_path,terminal):
 store=AttentionStore(tmp_path/'store.sqlite3');run,attempt,trace,request=records()
 store.create_run(replace(run,assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST));store.transition_run(run.run_id,RunStatus.RUNNING,event_key='start');store.create_attempt(attempt);store.put_trace(trace)
 runtime=AttentionRuntime(store,SimpleNamespace(answer=lambda **_:SimpleNamespace(total_tokens=2000)),clock=lambda:20)
 runtime.open_request(replace(request,mode=AssistanceMode.LIVE_HUMAN_FIRST,deadline_at=10))
 with pytest.raises(StateConflictError,match='token budget exhausted'):runtime.handle_deadline(request.request_id)
 store.transition_run(run.run_id,terminal,event_key='finish');store.transition_run(run.run_id,terminal,event_key='finish')
 snapshot=dashboard_snapshot(store,run.run_id);releases=[e for e in snapshot.get('budget_events',[]) if e['event_type']=='budget.released']
 assert len(releases)==1
 assert releases[0]['payload']=={'reservation_id':AttentionRuntime._reservation_id(request.request_id),'credits':1,'reason':'run_terminal'}
 assert snapshot['resources']['assistance']['reserved']==0

@pytest.mark.parametrize('text',['请降低抓取高度。','不要降低抓取高度，夹紧后确认抓牢','AB_CONTROL_V1 close_settle_steps=999;降低抓取高度，夹紧后确认抓牢','查看是否需要降低抓取高度，夹紧后确认抓牢'])
def test_r4_05_ambiguous_negated_or_invalid_guidance_is_not_silently_translated(tmp_path,text):
 runner,_,_,_=chain(tmp_path,text)
 assert runner.requests[1].attention_input['advisor_guidance']==text
