"""D4 fake Codex Dev/Eval, exact Advisor packets and public-only evidence."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import time
from types import SimpleNamespace
import pytest
from PIL import Image, PngImagePlugin
from benchmarks.attention_harness.dev_agent_workspace import Workspace,atomic_write,encode
from benchmarks.attention_harness.dev_agent_ledger import AgentLedger,LedgerBudget
from benchmarks.attention_harness.dev_agent_broker import Broker,FakeEvaluator
from benchmarks.attention_harness.dev_agent_adapters import FakeProber
from benchmarks.attention_harness.dev_agent_eval import Evaluator,build_workspace,PUBLIC_FILES,unavailable
from benchmarks.attention_harness.dev_agent_attention import Helper,request_view
from benchmarks.attention_harness.dev_agent_prompts import manifest_fields,EVAL_PROMPT,DEV_PROMPT
from benchmarks.attention_harness.dev_agent_recording import recording_frames
from benchmarks.attention_harness.dev_agent_loop import drive,CONTINUE_MESSAGE
from benchmarks.attention_harness.tests.test_dev_agent_d2 import CONFIG,SOURCE,fake_session,Executor
from benchmarks.attention_harness.tests.test_dev_agent_d1 import request,settled
from benchmarks.attention_harness.tests.test_eval_advisor_split import FakeClient,evidence
from benchmarks.attention_harness import dev_advisor_proxy,eval_agent
from benchmarks.attention_harness.dev_request_view import build_request_view,human_seconds
from benchmarks.attention_harness.core.policies import POLICY_IDS

VERDICT=dict(passed=False,reason='stdout.txt: FAILURE at grasp.',evidence=[dict(claim='Grasp failed.',source='stdout.txt:1')],advice='Try a smaller approach; inspect frames/overview-000001-sim-0.200000s.png.')

@pytest.fixture
def ws(tmp_path):
    result=Workspace(tmp_path/'agent').prepare(dict(task_id='fake',task_prompt='Move object.'),CONFIG)
    atomic_write(result.ws,'work/main.py',SOURCE)
    return result


def image_bytes():
    stream=io.BytesIO();Image.new('RGB',(24,16),(10,80,150)).save(stream,format='PNG');return stream.getvalue()


def seed_record(ws,vid='v0001',eid='e000001'):
    for name in PUBLIC_FILES:
        value=b'FAILURE: grasp missed.\n' if name=='stdout.txt' else b'' if name=='stderr.txt' else []
        ws.publish(f'executions/{eid}/{name}',value)
    frames=[]
    for i in range(2):
        name=f'frames/overview-{i:06d}-sim-{i/5:.6f}s.png';raw=image_bytes()
        ws.publish(f'executions/{eid}/{name}',raw)
        frames.append(dict(filename=name,index=i,sim_seconds=i/5,view='overview',sha256=hashlib.sha256(raw).hexdigest()))
    ws.publish(f'executions/{eid}/frames.json',frames)
    ws.publish(f'versions/{vid}/bundle.py',b'print("SUCCESS")\n')
    ws.publish(f'versions/{vid}/sources/main.py',b'print("SUCCESS")\n')
    ws.publish(f'versions/{vid}/sources/helper.py',b'X=1\n')
    return dict(version_id=vid,execution_id=eid,outcome={'status':'failed'},eval={'status':'completed',**VERDICT})


def eval_factory(ws, text=None, *, sleep=0, code=0):
    script=ws.private/'fake-eval.py'
    script.write_text('#!/usr/bin/python3\nimport json,time,sys\n'
        f'time.sleep({sleep!r})\n'
        "print(json.dumps({'type':'thread.started','thread_id':'fresh-eval'}),flush=True)\n"
        +f"print(json.dumps({{'type':'item.completed','item':{{'type':'agent_message','text':{(json.dumps(VERDICT) if text is None else text)!r}}}}}),flush=True)\n"
        +"print(json.dumps({'type':'turn.completed','usage':{'input_tokens':11,'output_tokens':8}}),flush=True)\n"
        +f'sys.exit({code})\n')
    script.chmod(0o700)
    return lambda root,prompt:[str(script),'-a','never','exec','-C',str(root),'--json','-m','unused','-c','model_reasoning_effort="low"',prompt]


def test_eval_workspace_exact_public_record_and_prompt(ws):
    execution=seed_record(ws)
    ws.publish('versions/v0002/secret.txt',b'future record')
    atomic_write(ws.private,'score.json',b'{"native_success":true}')
    # Hostile modifications to Dev's copy are never consumed.
    (ws.ws/'executions/e000001/stdout.txt').write_text('SUCCESS')
    ev=Evaluator(ws,model='gpt-6-sol',command_factory=eval_factory(ws))
    result=ev.evaluate(execution,deadline=time.monotonic()+5)
    assert result==dict(status='completed',**VERDICT)
    root=ws.private/'eval/v0001/ws'
    expected={*PUBLIC_FILES,'task.json','bundle.py','sources/main.py','sources/helper.py',
        'frames/overview-000000-sim-0.000000s.png','frames/overview-000001-sim-0.200000s.png'}
    assert {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}==expected
    assert (root/'stdout.txt').read_text().startswith('FAILURE')
    for p in root.rglob('*'):
        assert not re.search(r'native_success|object_in_sink|native_result|check_success|reward',str(p.relative_to(root)))
        if p.is_file():assert not re.search(rb'native_success|object_in_sink|native_result|check_success|reward',p.read_bytes())
    command=json.loads((root.parent/'command.json').read_text())
    assert '--ephemeral' in command and '--output-schema' in command and 'resume' not in command
    assert command[command.index('-m')+1]=='gpt-6-sol' and 'model_reasoning_effort="high"' in command
    assert command[-1]==EVAL_PROMPT and ev.usage=={'input_tokens':11,'output_tokens':8}
    fields=manifest_fields()
    for role,prompt in [('eval',EVAL_PROMPT),('dev',DEV_PROMPT)]:
        assert fields[role+'_agent_prompt_sha256']==hashlib.sha256(prompt.encode()).hexdigest()
    assert fields['eval_agent_prompt_version']!='eval-agent-vision-v3'
    assert 'Give concrete' not in DEV_PROMPT  # No hidden grading instruction.
    assert '失败以 stdout 为准' in EVAL_PROMPT and 'view_image' in EVAL_PROMPT
    assert not re.search(r'native_success|object_in_sink|native_result|check_success|reward',DEV_PROMPT+EVAL_PROMPT)


@pytest.mark.parametrize('text,code,sleep,expected',[
    ('not json',0,0,'schema_invalid'),('{"passed": "false"}',0,0,'schema_invalid'),
    (json.dumps(VERDICT),9,0,'process_failed'),(None,0,5,'timeout')])
def test_eval_unavailable_and_process_kill(ws,text,code,sleep,expected):
    execution=seed_record(ws)
    ev=Evaluator(ws,command_factory=eval_factory(ws,text,sleep=sleep,code=code))
    before=time.monotonic();value=ev.evaluate(execution,deadline=before+(.15 if sleep else 3))
    assert value['status']=='unavailable' and value['passed'] is None and expected in value['reason']
    assert time.monotonic()-before<3 and (ev.process is None or ev.process.poll() is not None)
    assert (ws.private/'eval/v0001/usage.json').exists()


def test_recording_pixels_metadata_and_sim_time(tmp_path):
    folder=tmp_path/'recording_frames';folder.mkdir()
    meta=PngImagePlugin.PngInfo();meta.add_text('comment','native_success reward check_success')
    path=folder/'private-native_result.png';Image.new('RGB',(24,16),(20,40,60)).save(path,pnginfo=meta)
    rows=[dict(file=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),physical_sequence=7,trace_seconds=900)]
    (folder/'frames.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in rows))
    frames=list(recording_frames(tmp_path,5))
    assert len(frames)==1 and frames[0]['filename']=='frames/overview-000000-sim-1.400000s.png'
    assert not re.search(rb'native_success|native_result|check_success|reward',frames[0]['data'])
    with Image.open(io.BytesIO(frames[0]['data'])) as im:
        assert im.info=={} and im.getpixel((0,0))==(20,40,60)
    # Production capture paths feed rendered RGB directly, without text drawing.
    for name in ['benchmarks/attention_harness/robocasa365/human_camera.py','openarm365_dev_v2/extra_views.py','benchmarks/attention_harness/robocasa365/recording.py']:
        assert not re.search(r'putText|ImageDraw|drawtext',Path(name).read_text())


def test_video_only_extracts_all_frames(tmp_path):
    import shutil
    if not shutil.which('ffmpeg'):pytest.skip('ffmpeg unavailable')
    pictures=tmp_path/'input';pictures.mkdir()
    for i in range(2):Image.new('RGB',(24,16),(i*100,20,30)).save(pictures/f'{i:06d}.png')
    subprocess.run(['ffmpeg','-v','error','-framerate','5','-i',str(pictures/'%06d.png'),'-pix_fmt','yuv420p',str(tmp_path/'advisor_replay.mp4')],check=True)
    (tmp_path/'camera_recording.json').write_text(json.dumps(dict(initial_frame_saved=True,frame_count=2)))
    result=list(recording_frames(tmp_path,5))
    assert len(result)==2 and [r['sim_seconds'] for r in result]==[0,.2]
    shutil.copyfile(tmp_path/'advisor_replay.mp4',tmp_path/'advisor_replay_side.mp4')
    (tmp_path/'camera_views.json').write_text(json.dumps({'views':{'side':{'frame_count':2}}}))
    result=list(recording_frames(tmp_path,5))
    assert len(result)==4 and [r['view'] for r in result]==['overview','overview','side','side']


def fake_view(*args,**kw):
    return build_request_view(task=evidence()['task'],question=kw.get('question') or 'What stopped?',
        frames=evidence()['frames'],eval_review=kw.get('review'),shown_video_seconds=2)


def helper(ws,policy='reactive_help',**kw):
    ledger=AgentLedger(ws.private/'ledger.sqlite','cell',help_limit=10)
    client=FakeClient('Try a smaller motion.')
    value=Helper(ws,ledger,policy=policy,config={'advisor_proxy':dev_advisor_proxy.default_config()},
                 client=client,view_builder=fake_view,**kw)
    value.context=dict(episode='unused',recording={},execution_seconds=2)
    return value,ledger,client


def test_reactive_grant_once_cache_cost_and_unavailable(ws):
    h,ledger,client=helper(ws)
    assert h.ask({'question':'Help?'},deadline=time.monotonic()+5)['status']=='not_eligible'
    execution=seed_record(ws);h.boundary(execution,deadline=time.monotonic()+5)
    result=h.ask({'question':'What stopped?'},deadline=time.monotonic()+5)
    assert result['delivered'] and result['requested'] and not result['system_injected']
    assert h.ask({'question':'Again?'},deadline=time.monotonic()+5)['status']=='not_eligible'
    second=seed_record(ws,'v0002','e000002');h.boundary(second,deadline=time.monotonic()+5)
    result2=h.ask({'question':'What stopped?'},deadline=time.monotonic()+5)
    assert result2['cache_hit'] and len(client.calls)==1
    assert result2['hint_human_seconds']==result['hint_human_seconds']>0
    assert ledger.snapshot()['used']['help']==2
    third=seed_record(ws,'v0003','e000003');third['eval']=unavailable('v0003','timeout')
    result3=h.boundary(third,deadline=time.monotonic()+5)
    assert result3['eligible'] and h.state['failures']==0
    assert result3['decision']['context']['eval_verdict']=='unknown'
    h.finish();assert h.state['intervals']['v0003']['not_used']
    assert client.calls[0]['messages'][0]['content'].encode()==dev_advisor_proxy.SYSTEM.encode()
    assert 'context' not in result and 'unused' not in (ws.ws/'versions/v0001/help.json').read_text()


@pytest.mark.parametrize('policy',POLICY_IDS)
def test_all_policy_decisions_ignore_probes_runs_turns(ws,policy):
    h,ledger,client=helper(ws,policy)
    if policy=='demo_first':
        h.demo={'content':'Example code'};assert h.initial()['system_injected'];assert h.initial() is None
        assert ledger.snapshot()['used']['help']==1
    baseline=[]
    for n in range(1,5):
        e=seed_record(ws,f'v{n:04d}',f'e{n:06d}')
        row=h.boundary(e,deadline=time.monotonic()+5);baseline.append(row['decision'])
        # Replaying the same formal boundary is idempotent even after many
        # non-formal broker requests. These never call boundary at all.
        b=Broker(ws,executor=Executor(),prober=FakeProber(),evaluator=FakeEvaluator(),helper=h,budget=LedgerBudget(ledger),agent_mode=True)
        try:
            for i in range(3):
                for op in ('observe','run'):
                    rid=f'{n}-{i}-{op}';b.accept(request(rid,op));assert settled(b,rid)['status']=='completed'
            assert h.boundary(e,deadline=time.monotonic()+5)['decision']==baseline[-1]
        finally:b.close()
        if policy not in ('reactive_help','retry_k_then_ask'):
            before=ledger.snapshot()['used']['help']
            assert h.ask({'question':'Extra?'},deadline=time.monotonic()+5)['status']=='policy_denied'
            assert ledger.snapshot()['used']['help']==before
    from benchmarks.attention_harness.dev_help import decide
    prior=[]
    for n,actual in enumerate(baseline,1):
        diagnostic=dict(execution_status='failed',public_error='',public_failure=True,action_trace=[])
        expected=decide(policy,h.parameters,n,diagnostic,n,prior,help_remaining=actual['context']['assistance_remaining'],
            eval_review={'passed':False,'reason':VERDICT['reason']},eval_available=True)
        assert actual==expected;prior.append(diagnostic)


def test_retry_unavailable_resets_failure_streak(ws):
    h,_,_=helper(ws,'retry_k_then_ask')
    for n,verdict in enumerate([VERDICT,unavailable('v2','timeout'),VERDICT,VERDICT],1):
        e=seed_record(ws,f'v{n:04d}',f'e{n:06d}');e['eval']=verdict
        value=h.boundary(e,deadline=time.monotonic()+5)
        assert value['eligible']==(n==4)


def test_full_fake_dev_eval_run(ws):
    class Physical(Executor):
        def execute(self,*a,**kw):
            result=super().execute(*a,**kw)
            result['stdout']='FAILURE: grasp missed.' if self.calls==1 else 'Motion finished.'
            return result
    h,ledger,client=helper(ws)
    class SequenceEval(Evaluator):
        def evaluate(self,execution,**kw):
            answer={**VERDICT,'passed':int(execution['version_id'][1:])>1}
            self.command_factory=eval_factory(ws,json.dumps(answer))
            return super().evaluate(execution,**kw)
    ev=SequenceEval(ws,command_factory=eval_factory(ws))
    session=fake_session(ws,[
        "a=json.loads(tool('submit').stdout); assert a['result']['eval']['advice']; b=json.loads(tool('ask_help','--question','What stopped?').stdout); assert b['result']['delivered']",
        "assert 'Try a smaller motion.' in prompt; assert 'frames/overview' in prompt; a=json.loads(tool('submit').stdout); assert a['result']['message']=="+repr(CONTINUE_MESSAGE),
        "assert "+repr(CONTINUE_MESSAGE)+" in prompt; tool('submit'); time.sleep(5)" ])
    result=drive(ws,session,ledger,executor=Physical((3,)),prober=FakeProber(),evaluator=ev,helper=h)
    assert result['reason']=='native_success',result
    assert ledger.snapshot()['used']==dict(formal_versions=3,moving_executions=3,read_only_probes=0,eval_runs=3,help=1)
    rows=[json.loads(p.read_text()) for p in sorted((ws.private/'versions').glob('*.json'))]
    assert [r['eval_verdict'] for r in rows]==['fail','pass','pass']
    assert [r['agree'] for r in rows]==[True,False,True]
    assert [r['native_success'] for r in rows]==[False,False,True]
    assert len(client.calls)==1 and session.turn==3
    for job in ledger.jobs():
        if job['kind']=='eval':assert job['usage']=={'input_tokens':11,'output_tokens':8}
    banned=r'native_success|object_in_sink|native_result|check_success|reward'
    assert not re.search(banned,' '.join(session.messages))
    for root in [ws.ws,*[p/'ws' for p in (ws.private/'eval').iterdir()]]:
        for path in root.rglob('*'):
            assert not re.search(banned,str(path.relative_to(root)))
            if path.is_file():assert not re.search(banned,path.read_text(errors='replace'))


def test_one_call_eval_packets_golden(tmp_path):
    # Compare exact prompt/config/request packet with the committed D2+D3 source.
    import types
    name='benchmarks.attention_harness._d4_original_eval'
    source=subprocess.check_output(['git','show','31fd2a5:benchmarks/attention_harness/eval_agent.py'],text=True)
    original=types.ModuleType(name);original.__package__='benchmarks.attention_harness';exec(compile(source,name,'exec'),original.__dict__)
    answer=json.dumps(dict(passed=False,reason='Frame #1 did not move. Try a smaller step.',reference_suggestion='Inspect logs.'))
    old_client,new_client=FakeClient(answer),FakeClient(answer)
    old=original.ask(evidence(),original.default_config(),old_client)
    new=eval_agent.ask(evidence(),eval_agent.default_config(),new_client)
    assert encode(old_client.calls)==encode(new_client.calls)
    assert original.SYSTEM.encode()==eval_agent.SYSTEM.encode()
    assert new['reason']==old['reason'] and new['reference_suggestion']==old['reference_suggestion']


@pytest.mark.parametrize('policy',['reactive_help','full_trace_aware_attention_planner'])
def test_original_advisor_request_view_and_seconds_byte_equal(tmp_path,monkeypatch,policy):
    from benchmarks.attention_harness import dev_v2_loop as old
    episode=tmp_path/'episode';episode.mkdir()
    folder=episode/'recording_frames';folder.mkdir()
    rows=[]
    for i in range(2):
        path=folder/f'{i:06d}.jpg';Image.new('RGB',(24,16),(20,50,90)).save(path)
        rows.append(dict(file=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),physical_sequence=i,trace_seconds=i+.5))
    (folder/'frames.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    timing=dict(frame_trace_seconds=[.5,1.5],frame_video_seconds=[0,1],basis='relative',event_clock='sdk_completion')
    (episode/'camera_recording.json').write_text(json.dumps(dict(replay_timing=timing)))
    (episode/'trace.json').write_text(json.dumps(dict(status='failed',error='motion failed',sdk_events=[dict(source='right_gripper',operation='close',status='failed',timestamp_basis='relative',timestamp=1.5,arguments={},error='motion failed')])))
    (episode/'policy.py').write_text('print("step")')
    config=dict(task_id='fake',task_prompt='Move object.',advisor_proxy=dev_advisor_proxy.default_config())
    decision=dict(context={'assessment':None},event_ids=['v1:0'] if policy.startswith('full') else [])
    review={'passed':False,'reason':'Frame #1: motion stopped.'};diff='+print("step")\n'
    def artifact(path,value):
        path.write_text(json.dumps(value) if not isinstance(value,str) else value)
        return dict(uri=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    bundle=artifact(tmp_path/'bundle.json',{'artifact_dir':str(episode)})
    diffref=artifact(tmp_path/'diff.patch',diff)
    execution=dict(state='settled',receipt=dict(bundle=bundle,recording={'replay_timing':timing}),actual={'execution_seconds':2})
    cell=dict(policy_id=policy,versions=[dict(version=1,diff=diffref)],artifact_dir=str(tmp_path/'run/cells/cell'))
    class Store:
        def cell(self,*a):return cell
        def reserve_check(self,*a,**kw):pass
        def reserve(self,*a,**kw):return {'job_id':'advisor'}
        def claim(self,*a):pass
        def settle(self,*a):pass
    monkeypatch.setattr(old,'job_for',lambda c,v,k:execution if k=='execute' else None)
    monkeypatch.setattr(old,'requested_eval',lambda c:review)
    before,after=FakeClient('Try a smaller motion.'),FakeClient('Try a smaller motion.')
    record=old.advisor_answer(Store(),'cell',1,2,config,config['advisor_proxy'],'owner',tmp_path/'old-advisor.json',before,None,decision)
    original=json.loads((tmp_path/'advisor-input.json').read_text())
    candidate=request_view(episode,config,decision,exec_version=1,policy=policy,recording={'replay_timing':timing},execution_seconds=2,diff=diff,review=review)
    assert encode(candidate)==encode(original)
    assert candidate['request_kind']==('targeted' if policy.startswith('full') else 'generic')
    result=dev_advisor_proxy.ask(candidate,config['advisor_proxy'],after)
    assert encode(before.calls)==encode(after.calls)
    assert result['hint_human_seconds']==record['hint_human_seconds']==human_seconds(candidate,result['feedback'])['hint_human_seconds']


def test_large_public_record_snapshot_does_not_consume_authored_cap(ws,monkeypatch,tmp_path):
    from benchmarks.attention_harness import dev_agent_replay as replay
    monkeypatch.setattr(replay,'TREE_LIMIT',1024)
    ws.publish('executions/e000001/frames/overview.png',image_bytes()+b'x'*2048)
    cut=replay.freeze_origin(ws,'post-exec-v1')
    rebuilt=replay.provision_replay(cut,tmp_path/'replayed')
    assert (rebuilt.ws/'executions/e000001/frames/overview.png').read_bytes()==(ws.ws/'executions/e000001/frames/overview.png').read_bytes()
    atomic_write(ws.ws,'work/large.bin',b'x'*1025)
    with pytest.raises(ValueError):replay.freeze_origin(ws,'post-exec-v2')


def test_help_survives_restart_without_double_charge(ws):
    h,ledger,client=helper(ws)
    execution=seed_record(ws);h.boundary(execution,deadline=time.monotonic()+5)
    h.ask({'question':'What stopped?'},deadline=time.monotonic()+5)
    revived=Helper(ws,ledger,policy='reactive_help',config=h.config,client=client,view_builder=fake_view)
    assert revived.ask({'question':'Again?'},deadline=time.monotonic()+5)['status']=='not_eligible'
    assert ledger.snapshot()['used']['help']==1 and len(client.calls)==1
    assert ledger.snapshot()['attention']['hint_human_seconds']>0


def test_frozen_manifest_prompt_drift_and_trace_gate(tmp_path):
    from benchmarks.attention_harness.dev_agent_config import normalize
    from benchmarks.attention_harness.dev_experiment import validate_fairness
    from benchmarks.attention_harness.tests.test_dev_agent_configuration import SELECTION
    with pytest.raises(ValueError,match='full_public_trace'):normalize(dict(SELECTION,trace_visible=False))
    with pytest.raises(ValueError,match='prompt_drift'):validate_fairness(dict(dev_mode='agent',selection=SELECTION))


def test_eval_quota_and_missing_usage(ws):
    execution=seed_record(ws);events=[];checks=[]
    quota=SimpleNamespace(check=checks.append,event=events.append)
    ev=Evaluator(ws,quota=quota,command_factory=eval_factory(ws))
    assert ev.evaluate(execution,deadline=time.monotonic()+5)['passed'] is False
    assert checks==['before_eval'] and any(e['type']=='turn.completed' for e in events)
    another=seed_record(ws,'v0002','e000002')
    factory=eval_factory(ws)
    path=ws.private/'fake-eval.py';path.write_text(path.read_text().replace("'usage':{'input_tokens':11,'output_tokens':8}","'usage':None"))
    ev.command_factory=factory
    assert ev.evaluate(another,deadline=time.monotonic()+5)['passed'] is False
    assert ev.usage is None


def test_eval_deadline_kills_descendants(ws):
    execution=seed_record(ws)
    factory=eval_factory(ws)
    script=ws.private/'fake-eval.py'
    script.write_text('#!/usr/bin/python3\nimport subprocess,time\nfrom pathlib import Path\n'
        "p=subprocess.Popen(['/usr/bin/python3','-c','import time;time.sleep(60)'])\n"
        "Path('child.pid').write_text(str(p.pid))\ntime.sleep(60)\n")
    ev=Evaluator(ws,command_factory=factory)
    assert ev.evaluate(execution,deadline=time.monotonic()+2)['status']=='unavailable'
    pid=int((ws.private/'eval/v0001/ws/child.pid').read_text())
    until=time.monotonic()+2
    while time.monotonic()<until:
        try:state=(Path('/proc')/str(pid)/'stat').read_text().split()[2]
        except FileNotFoundError:break
        if state=='Z':break
        time.sleep(.01)
    else:pytest.fail('Eval child survived process group kill')


def test_completion_records_eval_before_any_proactive_help(ws):
    h,ledger,client=helper(ws,'trace_aware_hint_only')
    # A helper whose boundary would request assistance if called.
    def forbidden(*a,**kw):raise AssertionError('help after run completed')
    h.boundary=forbidden
    session=fake_session(ws,["tool('submit'); time.sleep(5)"])
    result=drive(ws,session,ledger,executor=Executor((1,)),prober=FakeProber(),evaluator=FakeEvaluator(),helper=h)
    assert result['reason']=='native_success' and ledger.snapshot()['used']['eval_runs']==1
    assert ledger.snapshot()['used']['help']==0 and not client.calls


def test_bad_advisor_reply_retains_charge(ws):
    h,ledger,client=helper(ws)
    execution=seed_record(ws);h.boundary(execution,deadline=time.monotonic()+5)
    def bad(*a,**kw):raise dev_advisor_proxy.AdvisorUnavailable('bad reply')
    h.ask_provider=bad
    reply=h.ask({'question':'What stopped?'},deadline=time.monotonic()+5)
    assert reply['status']=='unavailable' and ledger.snapshot()['used']['help']==1
    assert reply['hint_human_seconds']>0
    assert h.ask({'question':'Retry?'},deadline=time.monotonic()+5)['status']=='not_eligible'


def test_production_wires_real_eval_helper_and_frozen_model(tmp_path,monkeypatch):
    from benchmarks.attention_harness import dev_agent_loop as loop,dev_agent_isolation as isolation
    config=dict(CONFIG,task_id='fake',task_prompt='Move object.',model_source='codex',advisor_proxy=dev_advisor_proxy.default_config(model_source='codex'))
    manifest=dict(configs={'fake:102':config},selection={'help_budget':1},dev_model='gpt-6-sol',eval_model='gpt-6-astra',
        dev_reasoning_effort='high',quota_stop_percent=90,initial_sources={'fake':{'source':SOURCE.decode()}},
        policies={'reactive_help':{}},demos={'fake':{'content':'Example'}},**manifest_fields())
    updates=[]
    class Store:
        path=tmp_path/'ledger.sqlite'
        def cell(self,key):return dict(task_id='fake',seed=102,policy_id='reactive_help')
        def update_cell(self,key,**kw):updates.append(kw)
        def version(self,*a):pass
    class Listener:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def bind(self,*a):pass
        def listen(self):pass
        def getsockname(self):return ('127.0.0.1',12345)
    monkeypatch.setattr(loop.socket,'socket',Listener)
    monkeypatch.setattr(isolation,'verify_runtime',lambda *a,**kw:None)
    monkeypatch.setattr(isolation,'codex_identity',lambda:dict(version='fixture'))
    def checked_drive(workspace,session,ledger,**kw):
        assert type(kw['evaluator']) is Evaluator and type(kw['helper']) is Helper
        assert kw['evaluator'].model=='gpt-6-astra' and session.model=='gpt-6-sol'
        assert kw['evaluator'].prompt==manifest['eval_agent_prompt']
        assert kw['helper'].policy=='reactive_help'
        return dict(state='completed',reason='fixture',resources=ledger.snapshot())
    monkeypatch.setattr(loop,'drive',checked_drive)
    result=loop.run_agent_cell(Store(),{'artifact_dir':str(tmp_path/'run')},manifest,'cell')
    assert result['reason']=='fixture',result


def test_full_text_and_relative_citations_are_not_numeric_projected(ws):
    class PublicExecutor:
        def execute(self,*a,**kw):return dict(status='completed',stdout='Step 1: read sources/helper.py; then proceed.\n'*500,
            stderr='Warning: grasp not confirmed.\n',trace=[{'status':'rejected','reason':'Pose error: 0.1 m; retry safely.'}],state_log=[{'time':0,'position':[1,2,3]}])
        def cancel(self):pass
    b=Broker(ws,executor=PublicExecutor(),prober=FakeProber(),evaluator=FakeEvaluator(),agent_mode=True)
    try:
        b.accept(request(op='submit'));r=settled(b)
        assert r['status']=='completed'
        assert (ws.ws/'executions/e000001/stdout.txt').read_text()==PublicExecutor().execute()['stdout']
        assert json.loads((ws.ws/'executions/e000001/trace.json').read_text())[0]['reason']=='Pose error: 0.1 m; retry safely.'
    finally:b.close()


def test_restart_resume_includes_pending_feedback(ws):
    ledger=AgentLedger(ws.private/'ledger.sqlite','cell')
    session=fake_session(ws,["assert 'Try a smaller approach;' in prompt; assert 'Delivered repair' in prompt; tool('submit'); time.sleep(5)"])
    session.thread_id='fake-thread-exact';session._save()
    atomic_write(ws.private,'loop.json',encode(dict(delivered=0,idle=0,events=[{'eval':VERDICT,'help':{'content':'Delivered repair'}}])))
    result=drive(ws,session,ledger,executor=Executor((1,)),prober=FakeProber(),evaluator=FakeEvaluator())
    assert result['reason']=='native_success'
    assert 'Delivered repair' in session.messages[0]


def test_exhausted_and_malformed_help_make_no_provider_call(ws):
    h,ledger,client=helper(ws)
    h.boundary(seed_record(ws),deadline=time.monotonic()+5)
    for question in ('','x'*2001,'native_success?'):
        assert h.ask({'question':question},deadline=time.monotonic()+5)['status']=='policy_denied'
    assert ledger.snapshot()['used']['help']==0 and not client.calls
    # Exhaust the bounded ledger through already settled interventions.
    for i in range(10):
        ledger.reserve('spent'+str(i),'help',{'help':1});ledger.claim('spent'+str(i));ledger.settle('spent'+str(i),{})
    assert h.ask({'question':'Help?'},deadline=time.monotonic()+5)['status']=='not_eligible'
    assert not client.calls
