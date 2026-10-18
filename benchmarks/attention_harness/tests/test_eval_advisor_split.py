"""Zero-cost role and timing contract tests: fake providers and fake frames."""
import json
from pathlib import Path

import pytest
from PIL import Image

from benchmarks.attention_harness import eval_agent, dev_advisor_proxy
from benchmarks.attention_harness.dev_help import decide
from benchmarks.attention_harness.dev_configuration import normalize_configuration,derive_cell_budget
from benchmarks.attention_harness.dev_ledger import attention_cost
from benchmarks.attention_harness.moment_localization import locate_event, select_moments
from benchmarks.attention_harness.v2_evaluator import build_eval_evidence
from benchmarks.attention_harness.dev_experiment import create,run,GOAL
from benchmarks.attention_harness.dev_v2_loop import question_for
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.parcc_client import ParccResponse
from benchmarks.attention_harness.dev_request_view import build_request_view,human_seconds,shown_words
from benchmarks.attention_harness.free_models import is_unmetered_model,limited_resources
from openarm365_dev_v2.tests.fakes import Client,Runner


FIXTURE=Path(__file__).parent/'fixtures'/'replay_timing_20261014.json'


class FakeReply:
    def __init__(self,content):
        self.content=content
        self.usage={'total_tokens':7,'prompt_tokens':3,'completion_tokens':4}


class FakeClient:
    def __init__(self,content):self.content=content;self.calls=[];self.timeout_seconds=90
    def chat(self,**kw):self.calls.append(kw);return FakeReply(self.content)


def evidence():
    return {'task':{'task_id':'fake','task_prompt':'Move an object'},'public_feedback':{'execution_status':'failed','public_error':'motion rejected','action_trace':[]},'frames':[{'filename':'000001.jpg','sha256':'a'*64,'view':'external','data_url':'data:image/jpeg;base64,AA=='}],'omissions':[]}


def test_roles_models_and_vision_boundary(tmp_path):
    ec=eval_agent.normalize({});ac=dev_advisor_proxy.normalize({})
    assert ec['model']==ac['model']=='parcc/Qwen'
    assert ec['prompt_sha256']!=ac['prompt_sha256'] and ec['cache_version']!=ac['cache_version']
    eval_client=FakeClient('{"passed": false, "reason": "frame #1: motion stopped", "reference_suggestion": "Try smaller motion"}')
    review=eval_agent.ask(evidence(),ec,eval_client,cache_dir=tmp_path)
    assert review['passed'] is False and review['mode']=='vision'
    assert any(isinstance(x.get('content'),list) for x in eval_client.calls[0]['messages'])
    advisor_client=FakeClient('Retry with a smaller motion.')
    from benchmarks.attention_harness.dev_request_view import build_request_view
    request=build_request_view(task=evidence()['task'],question='What stopped?',frames=evidence()['frames'],shown_video_seconds=2)
    hint=dev_advisor_proxy.ask(request,ac,advisor_client,cache_dir=tmp_path)
    assert hint['mode']=='vision' and hint['feedback'].startswith('Retry')
    assert isinstance(advisor_client.calls[0]['messages'][1]['content'],list)
    assert eval_agent.ask(evidence(),ec,FakeClient('bad'),cache_dir=tmp_path)['cache_hit']
    with pytest.raises(eval_agent.EvalUnavailable):eval_agent.ask({**evidence(),'frames':[]},ec,FakeClient('bad'))


def test_eval_v3_fact_and_advice_separation_and_missing_fields():
    parsed=eval_agent.parse_v3(json.dumps({'passed':False,'reason':'Trace event #2 stopped. Try a smaller move.','reference_suggestion':'Inspect the path.'}))
    assert parsed['reason']=='Trace event #2 stopped.'
    assert 'Try a smaller move.' in parsed['reference_suggestion'] and parsed['reason_advice_moved']
    for value in ({'passed':True},{'reason':'frame #1 stopped'},
                  {'passed':False,'reason':'建议调整夹爪。'}):
        with pytest.raises(ValueError):eval_agent.parse_v3(json.dumps(value,ensure_ascii=False))


def test_dev_sees_only_reason_for_every_policy(tmp_path,monkeypatch):
    store,experiment,_=_loop_setup(tmp_path,monkeypatch,mode='interactive')
    from benchmarks.attention_harness.dev_v2_loop import model_messages
    from benchmarks.attention_harness.dev_experiment import verify_manifest
    config=verify_manifest(experiment)['configs']['open_cabinet:102']
    review={'passed':False,'reason':'frame #1 stopped.','reference_suggestion':'SECRET_SUGGESTION'}
    messages=[]
    for policy in ('autonomous','reactive_help','full_trace_aware_attention_planner'):
        value=model_messages(config,'',{'eval_review':review},{'policy_id':policy,'action':'continue'}, {},True)
        joined=json.dumps(value)
        assert 'SECRET_SUGGESTION' not in joined and 'frame #1 stopped.' in joined
        messages.append(json.loads(value[1]['content'])['public_feedback']['eval_review'])
    assert messages[0]==messages[1]==messages[2]=={'passed':False,'reason':'frame #1 stopped.'}


def test_ui_review_projection_keeps_reason_and_suggestion_separate(tmp_path):
    from benchmarks.attention_harness.dev_request_view import display_eval_review
    from benchmarks.attention_harness.dev_loop import sha
    path=tmp_path/'eval-review.json'
    path.write_text(json.dumps({'status':'completed','passed':False,'reason':'frame #1 stopped.',
                                'reference_suggestion':'Try smaller motion.'}))
    frozen=path.read_bytes()
    cell={'jobs':[{'kind':'eval','version':1,'state':'settled','receipt':{'eval_review':{'uri':str(path),'sha256':sha(path)}}}]}
    review=display_eval_review(cell,1)
    assert review['reason']=='frame #1 stopped.' and review['reference_suggestion']=='Try smaller motion.'
    assert 'Reference suggestion: Try smaller motion.' in review['feedback']
    assert path.read_bytes()==frozen


def test_request_view_is_only_advisor_input_and_cache_uses_frames(tmp_path):
    frames=[{**evidence()['frames'][0],'view':view,'sha256':str(i)*64} for i,view in enumerate(('external','side','top','left_wrist','right_wrist'),1)]
    view=build_request_view(task={'task_id':'fake','task_prompt':'Move an object','native_success':True},question='What stopped?',
        frames=frames,events=[{'operation':'run_policy','status':'failed','error':'motion rejected','native_success':True}],
        diff='-old\n+new',eval_review={'passed':False,'reason':'frame #1 stopped','reference_suggestion':'Try smaller motion'},shown_video_seconds=3)
    def keys(value):
        if isinstance(value,dict):
            for key,item in value.items():yield key;yield from keys(item)
        elif isinstance(value,list):
            for item in value:yield from keys(item)
    assert not {'native_success','native_result','reward','oracle'} & set(keys(view))
    assert {f['view'] for f in view['frames']}=={'overview','side','top','left_wrist','right_wrist'}
    assert view['steps'][0]['label']=='The robot plan stopped' and view['steps'][0]['stop_reason']=='Stopped: motion rejected'
    client=FakeClient('Move more slowly.');config=dev_advisor_proxy.normalize({})
    result=dev_advisor_proxy.ask(view,config,client,cache_dir=tmp_path)
    sent=client.calls[0]['messages'][1]['content']
    assert json.loads(sent[0]['text'])=={**{k:v for k,v in view.items() if k!='frames'},'frame_manifest':[{k:f[k] for k in ('view','sha256','filename')} for f in view['frames']]}
    assert len([part for part in sent if part['type']=='image_url'])==5
    assert result['frames'][0]['sha256']==view['frames'][0]['sha256']
    assert result['hint_human_seconds']==human_seconds(view,result['feedback'])['hint_human_seconds']
    assert result['shown_words']==shown_words(view) and result['answer_words']==len('Movemoreslowly.')
    assert dev_advisor_proxy.ask(view,config,client,cache_dir=tmp_path)['cache_hit']
    changed={**view,'frames':[{**view['frames'][0],'sha256':'f'*64},*view['frames'][1:]]}
    assert dev_advisor_proxy.ask(changed,config,client,cache_dir=tmp_path)['cache_key']!=result['cache_key']


def test_independence_and_free_qwen_accounting():
    from benchmarks.attention_harness.dev_configuration import independent_eval_advisor
    ec=eval_agent.normalize({});ac=dev_advisor_proxy.normalize({})
    assert independent_eval_advisor(ec,ac)
    for key in ('prompt_version','prompt_sha256','cache_version','cache_namespace','session_namespace'):
        with pytest.raises(ValueError):independent_eval_advisor(ec,{**ac,key:ec[key]})
    assert is_unmetered_model(ac['model'])
    assert not {'eval_calls','provider_tokens','input_tokens','output_tokens'} & set(limited_resources(eval_config=ec,model_names=('parcc/GLM',ac['model'])))


def test_old_v2_manifest_and_artifact_read_without_rewrite(tmp_path,monkeypatch):
    import hashlib
    from benchmarks.attention_harness.dev_experiment import verify_manifest
    store,experiment,_=_loop_setup(tmp_path,monkeypatch,mode='interactive')
    manifest=json.loads(Path(experiment['manifest']['uri']).read_bytes());path=tmp_path/'legacy-manifest.json'
    old_eval={**eval_agent.default_config(),'prompt_version':eval_agent.OLD_PROMPT_VERSION,
              'prompt_sha256':hashlib.sha256(eval_agent.SYSTEM_V2.encode()).hexdigest()}
    old_eval.pop('cache_namespace');old_eval.pop('session_namespace')
    old_advisor={**dev_advisor_proxy.default_config(),'model':dev_advisor_proxy.OLD_MODEL,
                 'mode':'text_only','prompt_version':dev_advisor_proxy.OLD_PROMPT_VERSION,
                 'prompt_sha256':hashlib.sha256(dev_advisor_proxy.OLD_SYSTEM.encode()).hexdigest(),
                 'cache_version':dev_advisor_proxy.OLD_CACHE_VERSION,'max_selected_frames':4}
    old_advisor.pop('cache_namespace');old_advisor.pop('session_namespace')
    manifest['selection'].update(eval_agent=old_eval,advisor_proxy=old_advisor)
    for config in manifest['configs'].values():config.update(eval_agent=old_eval,advisor_proxy=old_advisor)
    path.write_text(json.dumps(manifest,ensure_ascii=False))
    frozen=path.read_bytes();experiment['manifest']={'uri':str(path),'sha256':hashlib.sha256(frozen).hexdigest()}
    assert verify_manifest(experiment)['selection']['eval_agent']['prompt_version']==eval_agent.OLD_PROMPT_VERSION
    artifact=tmp_path/'old-review.json';artifact.write_text('{"passed": false, "feedback": "trace event #1 stopped"}')
    old_bytes=artifact.read_bytes()
    assert eval_agent.visible_review(json.loads(artifact.read_bytes()))=={'passed':False,'reason':'trace event #1 stopped','reference_suggestion':''}
    assert path.read_bytes()==frozen and artifact.read_bytes()==old_bytes


def test_new_daily_binds_visual_advisor_and_old_daily_stays_frozen(tmp_path):
    from benchmarks.attention_harness.daily_dev_v2 import bind_daily_advisor,DEFAULT_CELL
    selection={'eval_agent':{'enabled':True,'model':'parcc/Qwen'}}
    path=tmp_path/'selection.json'
    fresh=bind_daily_advisor(selection,path)
    assert fresh['advisor_proxy']['model']=='parcc/Qwen' and fresh['advisor_proxy']['mode']=='vision'
    assert DEFAULT_CELL['hint_human_seconds']>0
    path.write_text(json.dumps(selection))
    frozen=path.read_bytes()
    assert bind_daily_advisor(selection,path)==selection and path.read_bytes()==frozen


def test_eval_controls_policy_and_diagnostic_fallback():
    feedback={'execution_status':'completed','public_failure':True,'public_error':'','action_trace':[]}
    common=dict(policy='reactive_help',parameters={},version=2,feedback=feedback,failures=1,help_remaining=1)
    assert decide(**common,eval_review={'passed':False,'feedback':'try again'},eval_available=True)['request_type']=='hint'
    assert decide(**common,eval_review={'passed':True,'feedback':'done'},eval_available=True)['action']=='continue'
    assert decide(**common)['action']=='continue'
    fallback=decide(**common,diagnostic_fallback=True)
    assert fallback['context']['eval_fallback'] is True and fallback['request_type']=='hint'


@pytest.mark.parametrize('name,count', [('1',62),('5',21)])
def test_real_replay_timing_fixture(name,count):
    timing=json.loads(FIXTURE.read_text())[name]
    assert len(timing['frame_trace_seconds'])==count
    event={'timestamp_basis':'relative','timestamp':timing['frame_trace_seconds'][1]}
    assert locate_event(event,timing)['frame_index']==1
    event['timestamp']=(timing['frame_trace_seconds'][1]+timing['frame_trace_seconds'][2])/2
    assert locate_event(event,timing)['frame_index']==1  # UI uses floor, not nearest by distance
    for bad in ({**timing,'frame_video_seconds':timing['frame_video_seconds'][:-1]},
                {**timing,'frame_trace_seconds':[timing['frame_trace_seconds'][0]]*count},None):
        assert locate_event(event,bad)['status']=='unavailable'
    event['timestamp']=timing['frame_trace_seconds'][-1]+1
    assert locate_event(event,timing)['reason']=='event_outside_sample_range'


def test_same_sample_before_moves_back_and_unlocatable_falls_back():
    timing=json.loads(FIXTURE.read_text())['1'];t=timing['frame_trace_seconds']
    events=[{'event_id':'v1:0','timestamp_basis':'relative','timestamp':t[1]},
            {'event_id':'v1:1','timestamp_basis':'relative','timestamp':(t[1]+t[2])/2}]
    moments,status=select_moments(events,['v1:1'],timing,available_views=('left_wrist','external'))
    assert status['status']=='targeted' and [(m['role'],m['frame_index']) for m in moments[:2]]==[('before',0),('after',1)]
    assert moments[0]['view']=='left_wrist'
    events[1]['timestamp']=t[-1]+1
    assert select_moments(events,['v1:1'],timing)[1]['status']=='generic_fallback'


def test_frame_cap_covers_each_selected_event_before_extra_views():
    timing=json.loads(FIXTURE.read_text())['1'];t=timing['frame_trace_seconds']
    events=[{'event_id':f'v1:{i}','timestamp_basis':'relative','timestamp':t[i+1]} for i in range(3)]
    moments,status=select_moments(events,['v1:1','v1:2'],timing,available_views=('left_wrist','right_wrist'),max_frames=4)
    assert status['status']=='targeted'
    assert {m['event_id'] for m in moments}=={'v1:1','v1:2'}
    assert all(m['view']=='left_wrist' for m in moments)
    assert select_moments(events,['v1:0','v1:1','v1:2'],timing,max_frames=4)[1]['reason']=='selected_events_exceed_frame_limit'


def test_memory_only_coexists_and_eval_is_compute():
    memory={'base_url':'http://127.0.0.1:9999','store_id':'test-store','namespace':'test'}
    # Configuration validation only: no request is sent to this endpoint.
    config=normalize_configuration({'policies':['full_trace_aware_attention_planner'],'planner_features':{'memory':memory},
        'eval_agent':{},'advisor_proxy':{}})
    assert config['planner_features']['approval_rule']=='none'
    assert config['planner_features']['human_wait_seconds']==0
    budget=derive_cell_budget(config)
    assert budget['eval_calls']==config['target_valid_executions']
    assert attention_cost({'advisor_calls':1,'hint_human_seconds':30,'eval_calls':4})==1.5
    assert attention_cost({'advisor_calls':1,'hint_human_seconds':30,'review_human_seconds':1000})==1.5


def test_eval_frame_sampling_uses_recording_time(tmp_path):
    folder=tmp_path/'recording_frames';folder.mkdir()
    for index in range(12):Image.new('RGB',(8,8),(index,0,0)).save(folder/f'{index:06d}.jpg')
    (tmp_path/'trace.json').write_text(json.dumps({'status':'failed','error':'motion rejected','sdk_events':[
        {'source':'right_gripper','operation':'close','status':'failed','timestamp_basis':'relative','timestamp':7.3,'arguments':{},'error':'motion rejected'}]}))
    (tmp_path/'camera_recording.json').write_text(json.dumps({'replay_timing':{'basis':'relative','frame_trace_seconds':list(range(12)),'frame_video_seconds':list(range(12))}}))
    result=build_eval_evidence(tmp_path,{'task_id':'fake','task_prompt':'Move an object'})
    assert result['frame_localization']=='recording_mapping'
    assert 'recording_frames/000007.jpg' in [f['filename'] for f in result['frames']]
    (tmp_path/'camera_recording.json').unlink()
    result=build_eval_evidence(tmp_path,{'task_id':'fake','task_prompt':'Move an object'})
    assert result['frame_localization'].startswith('event_order_fallback:')


def test_question_template_is_stable_per_failure_category():
    first=question_for({'failure_type':'grasp'})
    assert first==question_for({'failure_type':'grasp'})
    assert first!=question_for({'failure_type':'placement'})


class FrameRunner(Runner):
    def execute(self,request):
        result=super().execute(request)
        folder=Path(result['artifact_dir'])/'recording_frames';folder.mkdir()
        for i in range(4):Image.new('RGB',(16,16),(i,0,0)).save(folder/f'{i:06d}.jpg')
        return result


class TimedFrameRunner(FrameRunner):
    def execute(self,request):
        result=super().execute(request);root=Path(result['artifact_dir'])
        timing={'basis':'relative','frame_trace_seconds':[0.0,0.5,1.0,1.5],
                'frame_video_seconds':[0.0,0.5,1.0,1.5]}
        result['recording']['replay_timing']=timing
        (root/'camera_recording.json').write_text(json.dumps({'replay_timing':timing}))
        (root/'recording_frames'/'frames.jsonl').write_text(''.join(json.dumps({'file':f'{i:06d}.jpg','physical_sequence':i,'trace_seconds':timing['frame_trace_seconds'][i]})+'\n' for i in range(4)))
        trace_path=Path(result['artifacts']['trace']['uri']);trace=json.loads(trace_path.read_text())
        trace['sdk_events'][0].update(timestamp_basis='relative',timestamp=1.0)
        trace_path.chmod(0o644)
        trace_path.write_text(json.dumps(trace));result['artifacts']['trace']['sha256']=__import__('hashlib').sha256(trace_path.read_bytes()).hexdigest()
        receipt_path=Path(result['artifacts']['sandbox_receipt']['uri']);receipt=json.loads(receipt_path.read_text());receipt['elapsed_seconds']=10
        receipt_path.chmod(0o644)
        receipt_path.write_text(json.dumps(receipt));result['artifacts']['sandbox_receipt']['sha256']=__import__('hashlib').sha256(receipt_path.read_bytes()).hexdigest()
        return result


class RoutedClient(Client):
    def __init__(self,passed=False,vision_down=False,advisor_down=False):
        super().__init__();self.passed=passed;self.vision_down=vision_down;self.advisor_down=advisor_down;self.role_calls=[];self.timeout_seconds=90
    def chat(self,**kw):
        prompt=kw['messages'][0]['content']
        if prompt.startswith('You are the system Eval agent'):
            self.role_calls.append(('eval',kw['model']))
            if self.vision_down:raise TimeoutError('fake vision failure')
            content=json.dumps({'passed':self.passed,'reason':'frame #1 shows the attempted motion','reference_suggestion':'Try smaller motion'})
        elif prompt.startswith('You are a human-like robotics advisor'):
            self.role_calls.append(('advisor',kw['model']))
            if self.advisor_down:raise TimeoutError('fake advisor vision failure')
            content='Try a smaller motion.'
        else:return super().chat(**kw)
        return ParccResponse(kw['model'],content,None,.01,1,{'total_tokens':7,'prompt_tokens':3,'completion_tokens':4})


def _loop_setup(tmp_path,monkeypatch,mode='experiment'):
    import openarm365_dev_v2.dev_config as dev_config
    identity=tmp_path/'fake-model-identity.txt';identity.write_text('test only')
    monkeypatch.setattr(dev_config,'model_sources',lambda **kw:{str(identity.resolve()):dev_config.sha(identity)})
    store=DevV2Store(tmp_path/'ledger.sqlite')
    selection={'request_key':'eval-advisor-split-unit','tasks':['open_cabinet'],'seeds':[102],
        'policies':['reactive_help'],'help_budget':1,'mode':mode,
        'eval_agent':{'enabled':True,'model':'parcc/Qwen'},'advisor_proxy':{}}
    experiment=create(store,tmp_path/'artifacts',selection)
    # The authorization must name the same wall envelope as the frozen goal.
    budget=tmp_path/'budget.json';budget.write_text(json.dumps({'goal':GOAL,'budget_tier':'dev','limits':{'dev_calls':28,'advisor_calls':14,'provider_tokens':500000,'executions':28,'wall_seconds':store.goal()['wall_limit']}}))
    return store,experiment,budget


def test_per_version_eval_then_one_advisor_hint(tmp_path,monkeypatch):
    store,experiment,budget=_loop_setup(tmp_path,monkeypatch);client=RoutedClient()
    result=run(store,experiment['experiment_id'],max_cells=1,budget_file=budget,client=client,runner_factory=FrameRunner)
    cell=result['cells'][0]
    assert cell['state']=='completed' and cell['used']['eval_calls']==2 and cell['used']['advisor_calls']==1
    assert cell['used']['hint_human_seconds']>0 and 'review_human_seconds' not in cell['used']
    assert client.role_calls==[('eval','parcc/Qwen'),('advisor','parcc/Qwen')]
    assert [j for j in cell['jobs'] if j['kind']=='eval' and j['version']==2][0]['receipt']['cache_hit'] is True
    model_packets=[json.loads(call['messages'][1]['content']) for call in client.inputs]
    assert model_packets[1]['public_feedback']['eval_review']['passed'] is False
    assert model_packets[1]['attention_control']['request_type']=='hint'
    assert store.export_ledger()['recompute_matches']


def test_eval_pass_stops_without_human_attention(tmp_path,monkeypatch):
    store,experiment,budget=_loop_setup(tmp_path,monkeypatch);client=RoutedClient(passed=True)
    result=run(store,experiment['experiment_id'],max_cells=1,budget_file=budget,client=client,runner_factory=FrameRunner)
    cell=result['cells'][0]
    assert cell['state']=='completed' and cell['reason']=='eval_passed' and len(cell['versions'])==1
    assert cell['used']['eval_calls']==1 and cell['used']['advisor_calls']==0 and cell['used']['hint_human_seconds']==0
    assert client.role_calls==[('eval','parcc/Qwen')]
    from benchmarks.attention_harness.dev_report import report
    summary=report(store,experiment['experiment_id'])
    assert summary['eval_confusion_matrix']['false_positive']==1
    assert summary['eval_false_stops']==1


def test_eval_vision_failure_invalidates_cell(tmp_path,monkeypatch):
    store,experiment,budget=_loop_setup(tmp_path,monkeypatch);client=RoutedClient(vision_down=True)
    result=run(store,experiment['experiment_id'],max_cells=1,budget_file=budget,client=client,runner_factory=FrameRunner)
    cell=result['cells'][0]
    assert cell['state']=='condition_invalid' and 'eval_vision_unavailable' in cell['reason']
    assert len(cell['versions'])==1 and cell['used']['advisor_calls']==0


def test_advisor_vision_failure_retries_then_invalidates_cell(tmp_path,monkeypatch):
    store,experiment,budget=_loop_setup(tmp_path,monkeypatch);client=RoutedClient(advisor_down=True)
    result=run(store,experiment['experiment_id'],max_cells=1,budget_file=budget,client=client,runner_factory=FrameRunner)
    cell=result['cells'][0]
    assert cell['state']=='condition_invalid' and cell['reason'].startswith('advisor_vision_unavailable:')
    assert cell['used']['advisor_calls']==1 and client.role_calls.count(('advisor','parcc/Qwen'))==2


def test_interactive_eval_outage_marks_public_fallback(tmp_path,monkeypatch):
    store,experiment,budget=_loop_setup(tmp_path,monkeypatch,mode='interactive');client=RoutedClient(vision_down=True)
    result=run(store,experiment['experiment_id'],max_cells=1,budget_file=budget,client=client,runner_factory=FrameRunner)
    cell=result['cells'][0]
    assert cell['state']!='condition_invalid' and cell['used']['advisor_calls']==1
    decision=json.loads(Path(cell['versions'][1]['decision']['uri']).read_text())
    assert decision['context']['eval_fallback'] is True
    eval_record=json.loads((Path(cell['versions'][0]['source']['uri']).parent/'eval-review.json').read_text())
    assert eval_record['status']=='unavailable_diagnostic'


def test_full_planner_targeted_hint_records_sampled_frame(tmp_path,monkeypatch):
    import benchmarks.attention_harness.dev_v2_loop as loop
    store,_,budget=_loop_setup(tmp_path,monkeypatch)
    # Force the policy outcome only; exercise the real request builder and ledger.
    # Use the full planner in a fresh selection with the same test-only identity.
    selection={'request_key':'targeted-moment-unit','tasks':['open_cabinet'],'seeds':[102],
        'policies':['full_trace_aware_attention_planner'],'help_budget':1,'mode':'experiment',
        'eval_agent':{'enabled':True,'model':'parcc/Qwen'},'advisor_proxy':{}}
    second=create(store,tmp_path/'artifacts',selection)
    original=loop.decide
    def choose(*args,**kwargs):
        if kwargs.get('version',args[2] if len(args)>2 else 0)==1:
            return {'action':'continue','reason':'first_version','request_type':None,'context':{}}
        return {'action':'request','reason':'trace_targeted','request_type':'hint','context':{'assessment':{'failure_type':'grasp'}},'event_ids':['v1:0'],'evidence_ids':[]}
    monkeypatch.setattr(loop,'decide',choose)
    client=RoutedClient()
    result=run(store,second['experiment_id'],max_cells=1,budget_file=budget,client=client,runner_factory=TimedFrameRunner)
    cell=result['cells'][0];path=Path(cell['versions'][1]['source']['uri']).parent/'advisor-hint.json'
    request=json.loads(path.read_text())
    archived_view=json.loads(Path(request['request_view']['uri']).read_text())
    assert archived_view['question']==request['question'] and archived_view['request_kind']=='targeted'
    assert archived_view['frames'][0]['sha256']==request['frames'][0]['sha256']
    assert request['localization']['status']=='targeted'
    assert request['selected_moments'][0]['frame_index']==2
    assert request['selected_frame_manifest'][0]['sha256']
    assert request['logical_human_seconds']==request['hint_human_seconds']
    assert request['shown_video_seconds']>=0 and request['frames'][0]['sha256']
