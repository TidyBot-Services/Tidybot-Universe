"""AI review isolation tests; fake calls never establish real-model acceptance."""
import base64
import hashlib
import io
import json
from pathlib import Path
import pytest
from PIL import Image
from benchmarks.attention_harness import v2_evaluator as ev
from benchmarks.attention_harness.dev_loop import public_feedback
from benchmarks.attention_harness.dev_help import public_screen
from benchmarks.attention_harness.dev_ledger import DevV2Store,RESOURCES,LIMITS
from benchmarks.attention_harness.dev_experiment import create,run,GOAL,verify_manifest
from benchmarks.attention_harness.dev_v2_loop import model_messages,eval_after_execution
from benchmarks.attention_harness.parcc_client import ParccResponse
from openarm365_dev_v2.tests.fakes import Client,Runner

CFG={'task_id':'open_cabinet','task_prompt':'Open the cabinet','eval_agent':{'enabled':True,'model':'parcc/Qwen'}}

def evidence_dir(root,count=22):
    root.mkdir(exist_ok=True)
    (root/'trace.json').write_text(json.dumps({'status':'failed','error':'IK planning rejected','sdk_events':[{'operation':'move_delta','source':'left_arm','status':'failed','arguments':{'dz':.01}}]}))
    (root/'policy.py').write_text('print("public intent")\n'*1000)
    (root/'policy-stderr.txt').write_text('public runtime error\n'*300)
    for label in ('initial','final'):
        (root/(label+'_public_observation.json')).write_text(json.dumps({'robot':{'joint_pos':[0.,1.]},'perception':{'values':{'cabinet':[.1,.2]}}}))
    frames=root/'recording_frames';frames.mkdir(exist_ok=True)
    for i in range(count):Image.new('RGB',(1920,1080),(i,0,0)).save(frames/f'{i:03}.jpg')
    return root

def test_evidence_explicit_whitelist_no_private_reads(tmp_path,monkeypatch):
    root=evidence_dir(tmp_path)
    forbidden=['native_result.json','result.json','safety.json','native-1.jsonl','native-service.log','sandbox_receipt.json','scene.xml','config.json']
    for name in forbidden:(root/name).write_text('native_success _check_success native_result safety SECRET_SCENE')
    read=Path.open
    def guarded(p,*args,**kw):
        assert p.name not in forbidden, f'forbidden file opened: {p}'
        return read(p,*args,**kw)
    monkeypatch.setattr(Path,'open',guarded)
    e=ev.build_eval_evidence(root,{**CFG,'native_success':True})
    wire=ev.encoded(ev.evaluator_messages(e))
    assert not ev.FORBIDDEN.search(wire) and 'SECRET_SCENE' not in wire
    assert e['public_feedback']=={k:public_feedback(json.loads((root/'trace.json').read_text()))[k] for k in ('execution_status','public_error','action_trace')}
    assert len(e['policy_source'])==12000 and len(e['stderr'])==4000

def test_frames_first_last_max_ten_resized_with_original_digest(tmp_path):
    e=ev.build_eval_evidence(evidence_dir(tmp_path),CFG)
    assert 2<=len(e['frames'])<=10
    assert e['frames'][0]['filename'].endswith('000.jpg') and e['frames'][-1]['filename'].endswith('021.jpg')
    for f in e['frames']:
        assert f['sha256']==hashlib.sha256((tmp_path/f['filename']).read_bytes()).hexdigest()
        assert Image.open(io.BytesIO(base64.b64decode(f['data_url'].split(',')[1]))).width<=768

def test_stderr_contamination_and_symlink_rejected(tmp_path):
    root=evidence_dir(tmp_path,0);(root/'policy-stderr.txt').write_text('native_success: true')
    (root/'camera_initial.png').symlink_to(root/'scene.xml')
    e=ev.build_eval_evidence(root,CFG)
    assert 'stderr' not in e and e['frames']==[] and any('rejected' in x for x in e['omissions'])

@pytest.mark.parametrize('text,passed,status',[
    ('EVAL_RESULT: {"passed": true, "feedback": "frame #1 and trace event #1"}',True,'completed'),
    ('```json\nEVAL_RESULT: {"passed": false, "feedback": "trace event #1 failed"}\n```',False,'completed'),
    ("EVAL_RESULT: {'passed': True, 'feedback': 'frame #1'}",True,'completed'),
    ('No envelope {"passed":true}',False,'failed'),('EVAL_RESULT: {oops}',False,'failed'),
    ('EVAL_RESULT: {"passed":"true", "feedback":"bad"}',False,'failed'),
    ('EVAL_RESULT: {"feedback":"missing bool"}',False,'failed')])
def test_parse_fail_closed(text,passed,status):
    r=ev.parse_eval_result(text);assert r['passed'] is passed and r['status']==status

def setup_run(tmp_path,enabled=True,policy='autonomous'):
    s=DevV2Store(tmp_path/'ledger.sqlite')
    selection=dict(request_key='eval-loop-test',tasks=['open_cabinet'],seeds=[102],policies=[policy])
    if enabled:selection['eval_agent']={'enabled':True,'model':'parcc/Qwen'}
    e=create(s,tmp_path/'artifacts',selection)
    budget=tmp_path/'budget.json';budget.write_text(json.dumps({'goal':GOAL,'limits':{'dev_calls':28,'advisor_calls':14,'provider_tokens':500000,'executions':28,'wall_seconds':7200}}))
    return s,e,budget

GOOD='### What happened\ntrace event #1 failed.\nEVAL_RESULT: {"passed":false,"feedback":"trace event #1: use smaller measured deltas."}'
USAGE={'total_tokens':100,'prompt_tokens':40,'completion_tokens':60}

def is_probe(kw):return kw.get('max_tokens')==ev.PROBE_TOKENS and len(kw['messages'])==1
def is_review(kw):return not is_probe(kw) and str(kw['messages'][0]['content']).startswith('You are a robotics')

class ReviewClient(Client):
    """Routes by request shape: probe (8 tokens), review (reviewer system prompt, Qwen=vision / GLM=text-only), else Dev/Advisor.
    error: raised by every review call; probe_error / vision_error: raised only by the Qwen probe / the Qwen vision call."""
    def __init__(self,error=None,probe_error=None,vision_error=None,text=GOOD):
        super().__init__();self.error=error;self.probe_error=probe_error;self.vision_error=vision_error;self.text=text
        self.review_inputs=[];self.probe_inputs=[];self.timeout_seconds=120;self.seen_timeouts=[]
    def chat(self,**kw):
        if is_probe(kw):
            self.probe_inputs.append(kw);self.seen_timeouts.append(('probe',self.timeout_seconds))
            if self.probe_error:raise self.probe_error
            return ParccResponse(kw['model'],'OK',None,.01,1,USAGE)
        if is_review(kw):
            self.review_inputs.append(kw);self.seen_timeouts.append((kw['model'],self.timeout_seconds))
            if self.error:raise self.error
            if kw['model']=='parcc/Qwen' and self.vision_error:raise self.vision_error
            return ParccResponse(kw['model'],self.text,None,.01,1,USAGE)
        return super().chat(**kw)

@pytest.mark.parametrize('error',[RuntimeError('review exploded'),TimeoutError('review timed out'),None])
def test_review_failure_cannot_change_cell_native_or_next_model(tmp_path,error):
    s,e,b=setup_run(tmp_path)
    class SuccessRunner(Runner):native_success=True
    client=ReviewClient(error)
    final=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=client,runner_factory=SuccessRunner)
    c=final['cells'][0];assert c['state']=='completed' and len(c['versions'])==2
    assert len(client.inputs)==2 and len(client.review_inputs)==2
    assert c['used']['eval_calls']==2 and s.goal()['reserved']['eval_calls']==0
    jobs=[j for j in c['jobs'] if j['kind']=='eval'];assert all(j['state']=='settled' for j in jobs)
    review=json.loads((Path(c['versions'][0]['source']['uri']).parent/'evaluator-review.json').read_text())
    assert review['status']==('timeout' if isinstance(error,TimeoutError) else 'failed' if error else 'completed')
    assert review['passed'] is (None if error is None else False)   # no recording => no visual verdict
    assert review['mode']=='text_only' and review['model']=='parcc/GLM' and review['fallback_reason']=='no_frames'
    assert not client.probe_inputs and all(r['model']=='parcc/GLM' for r in client.review_inputs)
    for j in c['jobs']:
        if j['kind']=='execute':assert json.loads(Path(j['receipt']['native_result']['uri']).read_text())['native_success'] is True
    packet=json.loads(client.inputs[1]['messages'][1]['content'])
    if error is None:
        public_screen(packet['public_feedback']['evaluator_review'])
        assert packet['public_feedback']['evaluator_review']['mode']=='text_only'
        assert 'TEXT-ONLY review of your previous attempt (nobody viewed the recording' in client.inputs[1]['messages'][0]['content']
    else:   # an unavailable review is not an opinion: Dev gets nothing instead of a fake 'failed'
        assert 'evaluator_review' not in packet['public_feedback']
        assert 'review of your previous attempt' not in client.inputs[1]['messages'][0]['content']
    assert s.export_ledger()['recompute_matches']
    from benchmarks.attention_harness.dev_report import report,report_csv
    r=report(s,e['experiment_id']);assert not r['cells'][0]['resource_errors']
    assert 'eval_calls' in report_csv(r).splitlines()[0]
    manifest=verify_manifest(e)
    assert manifest['configs']['open_cabinet:102']['eval_agent']['enabled'] is True

def test_advisor_input_unchanged_and_only_dev_gets_review(tmp_path):
    s,e,b=setup_run(tmp_path,policy='reactive_help')
    class HintClient(ReviewClient):
        def chat(self,**kw):
            if not is_review(kw) and not is_probe(kw) and kw['max_tokens']==2048:
                self.inputs.append(kw)
                assert 'evaluator_review' not in str(kw['messages'])
                return ParccResponse('simulated/GLM','Use measured deltas.',None,.01,1,{'total_tokens':100,'prompt_tokens':40,'completion_tokens':60})
            return super().chat(**kw)
    run(s,e['experiment_id'],max_cells=1,budget_file=b,client=HintClient(),runner_factory=Runner)
    assert s.goal()['used']['advisor_calls']==1

def test_skipped_budget_and_old_experiment_no_review(tmp_path):
    s,e,b=setup_run(tmp_path/'new')
    key=e['cells'][0]['key']
    with s.tx() as db:
        c=s.get(db,'cells',key);c['budget']['eval_calls']=0;s.put(db,'cells',key,c)
    client=ReviewClient();final=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=client,runner_factory=Runner)
    assert not client.review_inputs and final['cells'][0]['state']=='completed'
    for v in final['cells'][0]['versions']:
        assert json.loads((Path(v['source']['uri']).parent/'evaluator-review.json').read_text())['status']=='skipped_budget'
    old,e,b=setup_run(tmp_path/'old',False);client=ReviewClient()
    final=run(old,e['experiment_id'],max_cells=1,budget_file=b,client=client,runner_factory=Runner)
    assert not client.review_inputs
    assert all(not (Path(v['source']['uri']).parent/'evaluator-review.json').exists() for v in final['cells'][0]['versions'])
    # Simulate old stored payloads without the newly introduced resource, read-only normalization.
    with old.tx() as db:
        g=old.get(db,'goals',old.goal_id);g['limits'].pop('eval_calls');g['used'].pop('eval_calls');old.put(db,'goals',old.goal_id,g)
    assert old.export_ledger()['recompute_matches'] and old.goal()['remaining']['eval_calls']==0

def test_feedback_screen_rejects_contaminated_opinion(tmp_path):
    s,e,_=setup_run(tmp_path)
    config=verify_manifest(e)['configs']['open_cabinet:102']
    with pytest.raises(ValueError):model_messages(config,'',{'evaluator_review':{'passed':True,'feedback':'native_success true'}},{},{})

def test_review_immutable_text_only_and_disabled(tmp_path):
    root=evidence_dir(tmp_path/'execution',0)
    review=ev.review_execution(root,CFG,ReviewClient())
    assert review['passed'] is None and review['mode']=='text_only' and review['feedback'].startswith('Text-only review')
    assert not ev.FORBIDDEN.search(ev.encoded(review))
    with pytest.raises(FileExistsError):ev.review_execution(root,CFG,ReviewClient())
    other=tmp_path/'disabled';other.mkdir()
    assert ev.review_execution(other,{**CFG,'eval_agent':{'enabled':False}},None)['status']=='disabled'

def test_interrupted_review_settled_without_quarantine(tmp_path):
    s,e,_=setup_run(tmp_path)
    key=e['cells'][0]['key'];owner={'pid':999999999,'start_ticks':'0','boot_id':'missing'}
    directory=tmp_path/'cell';directory.mkdir()
    s.update_cell(key,state='running',owner=owner,artifact_dir=str(directory))
    job=s.reserve(key,1,'eval',{'eval_calls':1,'provider_tokens':100,'model_seconds':120})
    s.claim(job['job_id'],owner)
    with s.tx() as db:s.recover_evals(db)
    c=s.cell(key)
    assert c['state']=='running' and c['jobs'][0]['state']=='settled' and c['used']['eval_calls']==1
    assert s.export_ledger()['recompute_matches']
    assert json.loads((directory/'v1'/'evaluator-review.json').read_text())['status']=='failed'

def test_ui_view_exposes_review_only_for_operators(tmp_path):
    from benchmarks.attention_harness.daily_dev_v2 import DailyDevV2
    s,e,b=setup_run(tmp_path);final=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=ReviewClient(),runner_factory=Runner)
    bridge=DailyDevV2.__new__(DailyDevV2)
    view=bridge.view(s,final['cells'][0])
    assert all(v['evaluator_review']['status']=='completed' for v in view['versions'])
    assert view['ledger']['recompute_matches'] and view['ledger']['goal']['used']['eval_calls']==2

def test_malformed_response_is_settled_and_feedback_is_fail_closed(tmp_path):
    s,e,b=setup_run(tmp_path)
    class Malformed(ReviewClient):
        def chat(self,**kw):
            if is_review(kw):
                self.review_inputs.append(kw)
                return ParccResponse(ev.MODEL,'unexpected content',None,.01,1,USAGE)
            return super().chat(**kw)
    final=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=Malformed(),runner_factory=Runner)
    c=final['cells'][0];assert c['state']=='completed'
    assert all(j['state']=='settled' for j in c['jobs'] if j['kind']=='eval')
    for v in c['versions']:
        r=json.loads((Path(v['source']['uri']).parent/'evaluator-review.json').read_text());assert not r['passed'] and r['status']=='failed'
    assert s.export_ledger()['recompute_matches']

def test_model_contamination_never_written_or_injected(tmp_path):
    root=evidence_dir(tmp_path,1)
    class Contaminated:
        def chat(self,**kw):return ParccResponse(ev.MODEL,'EVAL_RESULT: {"passed":true,"feedback":"native_success true"}',None,.01,1,{'total_tokens':100,'prompt_tokens':40,'completion_tokens':60})
    review=ev.review_execution(root,CFG,Contaminated())
    assert review['status']=='failed' and not review['passed']
    assert not ev.FORBIDDEN.search((root/'evaluator-review.json').read_text())


def test_credential_falls_back_to_the_dev_agent_key_file(tmp_path, monkeypatch):
    """The Eval agent must find the same key as Dev/Advisor even when the process environment has none."""
    from benchmarks.attention_harness import v2_evaluator as ev
    monkeypatch.delenv('PARCC_API_KEY', raising=False)
    monkeypatch.delenv('LITELLM_KEY', raising=False)
    key_file = tmp_path / 'key'
    monkeypatch.setattr(ev, 'KEY_FILE', key_file)
    assert ev.credential() is None                      # nothing anywhere
    key_file.write_text('file-key\n')
    assert ev.credential() == 'file-key'                # runtime key file, newline stripped
    monkeypatch.setenv('LITELLM_KEY', 'env-key')
    assert ev.credential() == 'env-key'                 # the environment still wins


# ---- Qwen (vision) -> GLM (text-only) fallback -------------------------------------------------

def run_review(tmp_path,client,frames=3,**kw):
    tmp_path.mkdir(parents=True,exist_ok=True)
    root=evidence_dir(tmp_path/'execution',frames)
    return root,ev.review_execution(root,CFG,client,**kw)

def test_fallback_when_the_vision_model_does_not_answer_the_probe(tmp_path):
    client=ReviewClient(probe_error=TimeoutError('read timed out'))
    root,r=run_review(tmp_path,client)
    assert len(client.probe_inputs)==1 and [x['model'] for x in client.review_inputs]==['parcc/GLM']   # Qwen never gets the images
    assert r['status']=='completed' and r['mode']=='text_only' and r['model']=='parcc/GLM' and r['model_requested']=='parcc/Qwen'
    assert r['fallback_reason']=='qwen_unreachable' and 'TimeoutError' in r['fallback_detail']
    assert r['passed'] is None and r['feedback'].startswith('Text-only review')           # no verdict without looking
    assert len(r['frames'])>=2                                                           # the frames existed; nobody viewed them
    assert json.loads((root/'evaluator-review.json').read_text())['mode']=='text_only'
    assert r['qwen_seconds']<2

def test_probe_error_is_not_called_unreachable(tmp_path):
    _,r=run_review(tmp_path,ReviewClient(probe_error=RuntimeError('HTTP 503')))
    assert r['fallback_reason']=='qwen_error' and r['mode']=='text_only'

def test_fallback_when_the_vision_call_itself_times_out(tmp_path):
    client=ReviewClient(vision_error=TimeoutError('timed out after 90s'))
    _,r=run_review(tmp_path,client)
    assert len(client.probe_inputs)==1 and [x['model'] for x in client.review_inputs]==['parcc/Qwen','parcc/GLM']
    assert r['fallback_reason']=='qwen_vision_timeout' and r['mode']=='text_only' and r['passed'] is None
    _,r=run_review(tmp_path/'b',ReviewClient(vision_error=RuntimeError('HTTP 500')))
    assert r['fallback_reason']=='qwen_vision_error'

def test_vision_success_never_calls_the_text_model(tmp_path):
    client=ReviewClient(text='EVAL_RESULT: {"passed": true, "feedback": "frame #1 shows the door open; trace event #0 ok"}')
    _,r=run_review(tmp_path,client)
    assert [x['model'] for x in client.review_inputs]==['parcc/Qwen']
    assert r['mode']=='vision' and r['model']=='parcc/Qwen' and r['fallback_reason'] is None and r['passed'] is True
    assert any(p['type']=='image_url' for p in client.review_inputs[0]['messages'][1]['content'])

def test_sticky_text_only_and_no_frames_skip_the_vision_model(tmp_path):
    client=ReviewClient()
    _,r=run_review(tmp_path,client,prefer_text_only=True)
    assert not client.probe_inputs and [x['model'] for x in client.review_inputs]==['parcc/GLM']
    assert r['fallback_reason']=='qwen_skipped_after_vision_timeout' and r['mode']=='text_only'
    client=ReviewClient();_,r=run_review(tmp_path/'n',client,frames=0)
    assert not client.probe_inputs and r['fallback_reason']=='no_frames' and r['passed'] is None

def test_text_model_gets_no_images_and_cannot_claim_to_have_seen_them(tmp_path):
    client=ReviewClient(probe_error=TimeoutError('timed out'))
    run_review(tmp_path,client)
    messages=client.review_inputs[0]['messages']
    wire=ev.encoded(messages)
    assert 'image_url' not in wire and 'data:image' not in wire and 'frame_manifest' not in wire
    assert isinstance(messages[1]['content'],str) and 'TEXT-ONLY' in messages[0]['content'] and 'frame #' not in messages[0]['content']
    assert not ev.FORBIDDEN.search(wire)

def test_each_call_has_its_own_time_limit_and_the_client_limit_is_restored(tmp_path):
    client=ReviewClient(vision_error=TimeoutError('timed out'))
    run_review(tmp_path,client)
    assert client.seen_timeouts==[('probe',ev.PROBE_SECONDS),('parcc/Qwen',ev.VISION_SECONDS),('parcc/GLM',ev.TEXT_SECONDS)]
    assert client.timeout_seconds==120 and ev.WORST_CASE_SECONDS==ev.PROBE_SECONDS+ev.VISION_SECONDS+ev.TEXT_SECONDS

def test_text_model_failure_is_a_failed_review_not_an_exception(tmp_path):
    class TextDown(ReviewClient):
        def chat(self,**kw):
            if is_review(kw) and kw['model']=='parcc/GLM':raise TimeoutError('glm timed out')
            return super().chat(**kw)
    root,r=run_review(tmp_path,TextDown(probe_error=TimeoutError('timed out')))
    assert r['status']=='timeout' and r['passed'] is False and r['feedback']=='AI review unavailable.'
    assert r['fallback_reason']=='qwen_unreachable' and r['mode']=='text_only'
    assert (root/'evaluator-review.json').is_file()

def test_text_only_review_cannot_claim_a_visual_verdict(tmp_path):
    lying=ReviewClient(probe_error=TimeoutError('timed out'),text='EVAL_RESULT: {"passed": true, "feedback": "The door is clearly open in frame #3."}')
    _,r=run_review(tmp_path,lying)
    assert r['passed'] is None and r['feedback'].startswith('Text-only review: no recording was viewed.')

def test_loop_helpers_understand_text_only_reviews(tmp_path):
    from benchmarks.attention_harness.dev_v2_loop import screen_model_packet,previous_review,sticky_text_only
    ok={'passed':None,'feedback':'Text-only review: no recording was viewed.','mode':'text_only'}
    screen_model_packet({'public_feedback':{'evaluator_review':ok}})
    screen_model_packet({'public_feedback':{'evaluator_review':{'passed':True,'feedback':'x'}}})                # old 2-key form
    for bad in ({'passed':None,'feedback':'x'},{'passed':None,'feedback':'x','mode':'vision'},{'passed':'yes','feedback':'x','mode':'text_only'},
                {'passed':True,'feedback':'x','mode':'telepathy'},{'passed':True,'feedback':'x','extra':1}):
        with pytest.raises(ValueError):screen_model_packet({'public_feedback':{'evaluator_review':bad}})
    v1=tmp_path/'v1';v2=tmp_path/'v2';v1.mkdir();v2.mkdir()
    cell=lambda d:{'versions':[{'source':{'uri':str(d/'policy.py')}}]}
    assert previous_review({'versions':[]}) is None
    def write(d,**kw):(d/'evaluator-review.json').write_text(json.dumps(kw))
    write(v1,status='completed',passed=None,feedback='Text-only review: x',mode='text_only',fallback_reason='qwen_unreachable')
    assert previous_review(cell(v1))=={'passed':None,'feedback':'Text-only review: x','mode':'text_only'}
    write(v1,status='completed',passed=False,feedback='trace event #1',mode='vision')
    assert previous_review(cell(v1))=={'passed':False,'feedback':'trace event #1'}
    write(v1,status='failed',passed=False,feedback='AI review unavailable.')
    assert previous_review(cell(v1)) is None
    write(v1,status='completed',passed=None,feedback='x',mode='vision')
    assert previous_review(cell(v1)) is None
    # sticky: only a slow vision call (after a good probe) makes later attempts skip Qwen
    assert sticky_text_only(v2,1) is False
    for reason,expected in (('qwen_vision_timeout',True),('qwen_skipped_after_vision_timeout',True),('qwen_unreachable',False),('no_frames',False),(None,False)):
        write(v1,status='completed',fallback_reason=reason);assert sticky_text_only(v2,2) is expected


def test_probe_and_reviews_leave_a_reasoning_model_room_to_answer(tmp_path):
    """Qwen reasons before it answers: an 8-token probe came back with no final content and was misread as an outage."""
    assert ev.PROBE_TOKENS>=64 and ev.VISION_TOKENS>=4096 and ev.TEXT_TOKENS>=1024
    client=ReviewClient(vision_error=TimeoutError('timed out'))
    run_review(tmp_path,client)
    assert client.probe_inputs[0]['max_tokens']==ev.PROBE_TOKENS
    assert [x['max_tokens'] for x in client.review_inputs]==[ev.VISION_TOKENS,ev.TEXT_TOKENS]


# ---- multi-view evidence ---------------------------------------------------------------------------------------------

def add_views(root,count,views=ev.EXTRA_VIEWS,skip=()):
    """Main journal plus extra-view frames (same control steps), as the worker writes them."""
    frames=root/'recording_frames'
    names=sorted(p.name for p in frames.glob('*.jpg'))
    (frames/'frames.jsonl').write_text(''.join(json.dumps(dict(file=n,physical_sequence=i))+'\n' for i,n in enumerate(names)))
    for key in views:
        d=root/'recording_views'/key;d.mkdir(parents=True)
        rows=[]
        for i in range(count):
            if i in skip:continue
            name=f'{i:06d}.jpg';Image.new('RGB',(960,540),(0,i*3,50)).save(d/name)
            rows.append(dict(file=name,physical_sequence=i,sha256=hashlib.sha256((d/name).read_bytes()).hexdigest()))
        (d/'frames.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))

def test_evidence_samples_every_view_within_the_ten_image_budget(tmp_path):
    root=evidence_dir(tmp_path,22);add_views(root,22)
    e=ev.build_eval_evidence(root,CFG)
    views=[f['view'] for f in e['frames']]
    # this fixture fails at its first action, so the key moment is frame 0 and the main view contributes 3 distinct frames
    assert len(e['frames'])==9 and views.count('overview')==3
    assert {'side','top','left_wrist','right_wrist'}<=set(views)             # the grippers' views are reviewed too
    assert views.count('side')==2 and views.count('top')==2 and views.count('left_wrist')==1 and views.count('right_wrist')==1
    assert e['frames'][0]['filename'].endswith('000.jpg') and e['frames'][0]['view']=='overview'
    for f in e['frames']:
        assert f['sha256']==hashlib.sha256((root/f['filename']).read_bytes()).hexdigest()
        assert Image.open(io.BytesIO(base64.b64decode(f['data_url'].split(',')[1]))).width<=768
    # the key moment (first failed action) is the same control step in every view
    key_steps={f['view']:int(Path(f['filename']).stem) for f in e['frames'] if f['view']!='overview' and 'recording_views' in f['filename']}
    assert key_steps['left_wrist']==key_steps['right_wrist']==0
    wire=ev.evaluator_messages(e)
    captions=[p['text'] for p in wire[1]['content'] if p['type']=='text' and p['text'].startswith('frame #')]
    assert len(captions)==9 and any('(left_wrist view)' in c for c in captions) and any('(overview view)' in c for c in captions)
    manifest=json.loads(wire[1]['content'][0]['text'])['frame_manifest']
    assert [m['view'] for m in manifest]==views
    assert 'left_wrist' in wire[0]['content'] and 'fingertips' in wire[0]['content']
    assert not ev.FORBIDDEN.search(ev.encoded(wire))

def test_review_record_lists_the_views_that_were_sent(tmp_path):
    root=evidence_dir(tmp_path,12);add_views(root,12)
    client=ReviewClient();r=ev.review_execution(root,CFG,client)
    assert r['mode']=='vision' and {f['view'] for f in r['frames']}>= {'overview','side','left_wrist'}
    sent=[p for p in client.review_inputs[0]['messages'][1]['content'] if p['type']=='image_url']
    assert len(sent)==len(r['frames'])

def test_partial_extra_views_and_runs_without_any_fall_back_cleanly(tmp_path):
    root=evidence_dir(tmp_path/'a',22);add_views(root,22,views=('side','right_wrist'))
    views=[f['view'] for f in ev.build_eval_evidence(root,CFG)['frames']]
    assert set(views)=={'overview','side','right_wrist'} and len(views)<=10
    root=evidence_dir(tmp_path/'b',22)                                         # no extra views at all: original behaviour
    e=ev.build_eval_evidence(root,CFG)
    assert 2<=len(e['frames'])<=10 and {f['view'] for f in e['frames']}=={'overview'}
    root=evidence_dir(tmp_path/'c',22);add_views(root,22);import shutil
    (root/'recording_frames/frames.jsonl').write_text('')                      # journal does not match the frames
    assert {f['view'] for f in ev.build_eval_evidence(root,CFG)['frames']}=={'overview'}

def test_extra_view_path_tricks_are_rejected(tmp_path):
    root=evidence_dir(tmp_path,12);add_views(root,12,views=('side',))
    secret=tmp_path/'secret.jpg';Image.new('RGB',(10,10)).save(secret)
    target=root/'recording_views/side/000003.jpg';target.unlink();target.symlink_to(secret)   # symlinked frame
    e=ev.build_eval_evidence(root,CFG)
    assert all('secret' not in f['filename'] for f in e['frames'])

def test_a_mid_run_failure_uses_the_full_ten_image_budget_at_the_same_moment(tmp_path):
    root=evidence_dir(tmp_path,30);add_views(root,30)
    events=[dict(operation='move_delta',source='left_arm',status='ok',arguments={'dz':.01}) for _ in range(6)]
    events[3]=dict(operation='move_delta',source='left_arm',status='failed',arguments={'dz':.01})
    (root/'trace.json').write_text(json.dumps({'status':'failed','error':'IK planning rejected','sdk_events':events}))
    e=ev.build_eval_evidence(root,CFG)
    views=[f['view'] for f in e['frames']]
    assert len(views)==10 and views.count('overview')==4
    steps={f['view']:int(Path(f['filename']).stem) for f in e['frames'] if f['view'] in('left_wrist','right_wrist')}
    assert steps['left_wrist']==steps['right_wrist'] and 0<steps['left_wrist']<29      # mid-run, not first/last
    assert any(int(Path(f['filename']).stem)==steps['left_wrist'] and f['view']=='overview' for f in e['frames'])  # overview at that same step
