"""Model→contained native execution→public feedback→full-source revision."""
import difflib,json,os,re,threading,time,inspect,subprocess,sys,signal
from pathlib import Path
from .dev_loop import immutable as write_immutable,sha,public_feedback,numbers,process_identity,validation_diagnostic
from .dev_help import public_screen,decide,AdvisorProvider,DemoProvider,HumanProvider,policy_parameters
from .dev_tasks import get_task
from .core.dev_store import DevConflict,DevBudgetExhausted
from .parcc_client import ParccClient,ParccResponse,ParccError
from .dev_planner import PlannerActionBlocked
from .formal_runner_boundary import FormalRunRequest,run_with_formal_boundary
from openarm365_dev_v2.dev_config import ROOT,SIM_PYTHON,ROBocasa,ROBOSUITE,ASSETS
from openarm365_dev_v2.runner import OpenArm365DevRunnerV2
from openarm365_dev.policy_validation import validate_generated_policy

def immutable(path,value):
    path=Path(path)
    if path.exists():
        old=path.read_text() if isinstance(value,str) else json.loads(path.read_bytes())
        if old!=value:raise DevConflict('immutable recovery artifact conflict: '+str(path))
        return {'uri':str(path.resolve()),'sha256':sha(path)}
    return write_immutable(path,value)

def checked(ref):
    if sha(ref['uri'])!=ref['sha256']:raise DevConflict('immutable evidence digest mismatch')
    return Path(ref['uri'])
def job_for(cell,version,kind):return next((j for j in cell['jobs'] if j['version']==version and j['kind']==kind),None)
def screen_model_packet(packet):
    # The single named AI-opinion field is explicit protocol metadata. Screen its
    # contents normally; do not loosen public_screen's restrictions for any text.
    copy=dict(packet)
    if 'public_feedback' in copy:
        feedback=dict(copy['public_feedback'])
        review=feedback.pop('human_proxy_review',None)
        if review is not None:
            # Text-only reviews (no recording viewed) carry passed=None and mode='text_only'; the 2-key vision form stays valid.
            keys=set(review) if isinstance(review,dict) else set()
            if keys not in ({'passed','feedback'},{'passed','feedback','mode'}) or not isinstance(review['feedback'],str) or review.get('mode','vision') not in ('vision','text_only') or (type(review['passed']) is not bool and not (review['passed'] is None and review.get('mode')=='text_only')):raise ValueError('invalid AI review opinion')
            public_screen(review)
        copy['public_feedback']=feedback
    public_screen(copy)

def provider_call(store,key,version,kind,messages,owner,directory,client=None):
    # Only public packet/source fields are accepted before any reservation.
    for m in messages:
        if m['role']=='user':screen_model_packet(json.loads(m['content'])) if kind=='model' else public_screen(json.loads(m['content']))
    cell=store.cell(key);runtime=cell.get('runtime',{})
    existing=job_for(cell,version,kind)
    if existing and existing['state']=='settled':
        receipt=existing['receipt'];output=json.loads(checked(receipt['response']).read_bytes())
        checked(receipt['request'])
        if cell.get('planner_features') and kind=='advisor':
            from .dev_planner import PlannerControl,advisor_scope
            control=PlannerControl(store);scope=advisor_scope(store,key,version,messages)
            row=control.request(key,version,'advisor',scope,runtime.get('model_reserve_seconds',120))
            row=control.finish_advisor(row['request_id'],response=receipt['response'],current_scope=scope)
            if not row.get('accepted'):raise PlannerActionBlocked('Advisor response no longer valid: '+row['state'])
        return output['content'],receipt['request'],receipt['response'],receipt['usage']
    if existing and existing['state'] in {'claimed','needs_review'}:raise DevConflict('existing claim cannot be redispatched')
    from .dev_experiment import MODEL
    max_tokens=cell.get('probe_max_tokens',MODEL['max_tokens'] if kind=='model' else 2048)
    input_bound=sum(len(m['content'].encode()) for m in messages)+1024
    if runtime and input_bound>runtime['max_input_bytes_per_request']:raise DevBudgetExhausted('input_bound_exceeded: '+str(input_bound))
    seconds=runtime.get('model_reserve_seconds',120)
    reservation={'dev_calls' if kind=='model' else 'advisor_calls':1,'provider_tokens':input_bound+max_tokens,
                 'input_tokens':input_bound,'output_tokens':max_tokens,'model_seconds':seconds}
    if kind=='advisor':reservation.update(help_count=1,help_credits=1,help_wait_seconds=seconds)
    store.reserve_check(key,reservation) if not existing else None
    request=immutable(directory/(kind+'-input.json'),{**MODEL,'max_tokens':max_tokens,'messages':messages,'token_reservation':reservation})
    # Credential loading belongs to the configured runtime; never artifact/log it.
    if client is None:
        credential=os.getenv('PARCC_API_KEY') or os.getenv('LITELLM_KEY')
        if not credential:
            path=Path('/home/truares/.litellm-key')
            if path.is_file():credential=path.read_text().strip()
        provider=ParccClient(api_key=credential,max_attempts=1,timeout_seconds=min(seconds,store.goal_for_cell(key)['wall_remaining']))
    else:provider=client
    job=store.reserve(key,version,kind,reservation)
    if job.get('claimed_at') or job.get('actual'):raise DevConflict('existing model claim cannot be redispatched')
    wire=[]
    def observed(status,headers,raw):
        row={'http_status':status,'request_id':headers.get('x-request-id'),'body':json.loads(raw)};wire.append(row)
        immutable(directory/(kind+'-provider-response.json'),row)
    if hasattr(provider,'response_observer'):provider.response_observer=observed
    try:store.claim(job['job_id'],owner)
    except BaseException:
        if job_for(store.cell(key),version,kind)['state']=='reserved':store.release_unclaimed(job['job_id'])
        raise
    begun=time.monotonic()
    features=cell.get('planner_features');wait_row=None;prep_thread=None;prep_errors=[]
    try:
        if features and kind=='advisor':
            from .dev_planner import PlannerControl,prepare_independent,advisor_scope
            scope=advisor_scope(store,key,version,messages)
            wait_row=PlannerControl(store).request(key,version,'advisor',scope,seconds)
            public_input=json.loads(messages[-1]['content'])
            def prepare():
                try:prepare_independent(store,key,version,'advisor',public_input['previous_complete_source'],public_input['public_feedback'],directory,wait_row['request_id'],features['background_seconds'])
                except BaseException as exc:prep_errors.append(type(exc).__name__)
            prep_thread=threading.Thread(target=prepare,name='planner-public-preparation');prep_thread.start()
        try:
            if features and client is None:
                response=contained_provider(store,key,kind,request,directory,min(time.time()+seconds,time.time()+store.goal_for_cell(key)['wall_remaining'],wait_row['deadline_epoch'] if wait_row else float('inf')))
                provider_path=directory/(kind+'-provider-response.json')
                if provider_path.exists():wire.append(json.loads(provider_path.read_bytes()))
            else:response=provider.chat(model=MODEL['model'],messages=messages,max_tokens=max_tokens,temperature=0,reasoning_effort=MODEL['reasoning_effort'])
        except ParccError:
            if not wire or wire[-1]['http_status']!=200 or type(wire[-1]['body'].get('usage',{}).get('total_tokens')) is not int:raise
            response=ParccResponse(MODEL['model'],'',None,time.monotonic()-begun,1,wire[-1]['body']['usage'])
        output_path=directory/(kind+'-output.json')
        output={'uri':str(output_path.resolve()),'sha256':sha(output_path)} if features and client is None and output_path.exists() else immutable(output_path,response.artifact())
        usage=response.usage
        total=usage.get('total_tokens');input_tokens=usage.get('prompt_tokens');output_tokens=usage.get('completion_tokens')
        if any(type(v) is not int or v<0 for v in [total,input_tokens,output_tokens]) or total!=input_tokens+output_tokens:raise DevConflict('complete actual usage missing/inconsistent; no invented cost')
        actual={**reservation,'provider_tokens':total,'input_tokens':input_tokens,'output_tokens':output_tokens,'model_seconds':time.monotonic()-begun}
        if kind=='advisor':actual['help_wait_seconds']=actual['model_seconds']
        store.settle(job['job_id'],actual,dict(request=request,response=output,usage=usage,provider_response={'uri':str(directory/(kind+'-provider-response.json')),'sha256':sha(directory/(kind+'-provider-response.json'))} if wire else None))
        if wait_row:
            row=PlannerControl(store).finish_advisor(wait_row['request_id'],response=output,current_scope=scope)
            if not row.get('accepted'):raise PlannerActionBlocked('Advisor reply rejected after '+row['state'])
        return response.content,request,output,usage
    except BaseException as exc:
        current=job_for(store.cell(key),version,kind)
        if current['state']!='settled':store.unknown(job['job_id'],f'{type(exc).__name__}: {exc}',{'request':request,'provider_response':wire[-1] if wire else None})
        raise
    finally:
        if prep_thread:prep_thread.join()
        if prep_errors:
            with store.tx() as db:store.event(db,'planner_background_error',{'cell':key,'version':version,'errors':prep_errors})


def contained_provider(store,key,kind,request,directory,deadline):
    # The provider child owns no simulator and receives no response-dependent
    # background task. Termination leaves a held claim, never an automatic retry.
    log=(directory/(kind+'-worker.log')).open('ab')
    process=None
    try:
        process=subprocess.Popen([sys.executable,'-B','-m','benchmarks.attention_harness.dev_provider_worker','--request',request['uri'],'--directory',str(directory),'--kind',kind,'--deadline',str(deadline)],cwd=ROOT,env=__import__('benchmarks.attention_harness.dev_capabilities',fromlist=['provider_environment']).provider_environment(ROOT),stdout=log,stderr=log,start_new_session=True)
        immutable(directory/(kind+'-owned-process.json'),process_identity(process.pid))
        while process.poll() is None:
            cell=store.cell(key)
            with store.tx() as db:exp=store.get(db,'experiments',cell['experiment_id'])
            if exp.get('cancelled') or time.time()>=deadline:raise DevConflict('provider interrupted/deadline; paid request retained for review')
            time.sleep(.05)
        if process.returncode:raise DevConflict('provider child failed; paid request retained for review')
        value=json.loads((directory/(kind+'-output.json')).read_bytes())
        return ParccResponse(value['model'],value['content'],None,value['latency_seconds'],value['attempts'],value['usage'],value.get('request_id'))
    finally:
        if process:
            if process.poll() is None:
                try:os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    process.wait(timeout=2)
            with store.tx() as db:store.event(db,'planner_provider_cleanup',{'cell':key,'kind':kind,'pid':process.pid,'returncode':process.returncode,'reaped':process.poll() is not None})
        log.close()

def feedback_for(cell):
    if not cell['versions']:return {},cell.get('initial_source',''),0,[]
    if cell.get('runtime',{}).get('public_failure_rule'):
        prior=[];failures=0
        for v in cell['versions']:
            job=job_for(cell,v['version'],'execute')
            if job and job['state']=='settled':f=json.loads(checked(job['receipt']['public_feedback']).read_bytes())
            else:f={'execution_status':'failed' if not v['valid'] else 'not_dispatched','public_error':v['validation_error'] or '', 'source_unexecuted':v['valid'],'action_trace':[]}
            public_screen(f)
            # No completion signal is registered in this public SDK trace.
            failed=not v['valid'] or f.get('execution_status') in {'failed','timeout','timed_out'} or bool(f.get('public_error')) or any(e.get('error') or e.get('status')=='failed' for e in f.get('action_trace',[])) or cell['runtime']['public_failure_rule']['no_declared_completion_is_failure']
            f={**f,'public_failure':failed,'public_failure_rule':'attentionbench.public-failure.v1'}
            prior.append(f);failures=failures+1 if failed else 0
        return prior[-1],checked(cell['versions'][-1]['source']).read_text(),failures,prior[:-1]
    last=cell['versions'][-1];prior=[];failures=0
    for v in cell['versions']:
        job=job_for(cell,v['version'],'execute')
        if job and job['state']=='settled':
            f=json.loads(checked(job['receipt']['public_feedback']).read_bytes());public_screen(f);prior.append(f)
            failures=failures+1 if f.get('execution_status') in {'failed','timeout','timed_out'} else 0
        elif not v['valid']:failures+=1
    execution=job_for(cell,last['version'],'execute')
    if execution and execution['state']=='settled':feedback=prior[-1]
    else:feedback={'execution_status':'failed','public_error':last['validation_error'],'action_trace':[]}
    return feedback,checked(last['source']).read_text(),failures,prior[:-1]

def model_messages(config,previous,feedback,attention,remaining,trace_visible=True,source_size_limit_bytes=None):
    public_screen(previous);screen_model_packet({'public_feedback':feedback});public_screen(attention)
    # Explicit public task + SDK packet; resource paths and evaluator excluded.
    t=get_task(config['task_id']);public_cfg={k:config[k] for k in ['task_id','official_env','task_prompt','seed','robot','execution_arm','controller','layout_id','style_id','scene_id','horizon','sdk_call_limit','attempt_wall_seconds','perception_mode']}
    f=dict(feedback)
    if not trace_visible:f.pop('action_trace',None)
    packet={'locked_task':public_cfg,'previous_complete_source':previous,'public_feedback':f,'attention_control':attention,'remaining_budget':remaining}
    if source_size_limit_bytes:packet['source_size_limit_bytes']=source_size_limit_bytes
    screen_model_packet(packet)
    opinion=''
    if 'human_proxy_review' in f:
        textonly=f['human_proxy_review'].get('mode')=='text_only'
        opinion=('\nTEXT-ONLY review of your previous attempt (nobody viewed the recording; simulated-human opinion, may be wrong): ' if textonly else '\nHumanProxy review of your previous attempt, which you requested (simulated-human opinion, may be wrong): ')+json.dumps(f['human_proxy_review'],ensure_ascii=False)
    return [{'role':'system','content':t.system_prompt()+opinion},{'role':'user','content':json.dumps(packet,ensure_ascii=False,sort_keys=True)}]

def hp_result(text,record):
    if not isinstance(text,str) or not text.strip():raise ValueError('empty HumanProxy hint')
    public_screen(text);return {'provider':'human_proxy','content':text,'scope':'public repair hint from a simulated human, no approval','mode':record['mode']}

def attention(store,key,version,feedback,previous,failures,prior,m,owner,directory,client):
    cell=store.cell(key);policy=cell['policy_id'];params=m['policies'].get(policy,policy_parameters(policy));remaining=max(0,cell['budget']['help_count']-cell['used']['help_count'])
    decision_path=directory/'decision.json'
    memory=m['memory']
    if cell.get('planner_features') and cell['planner_features']['memory']:
        from .dev_planner_memory import load_memory
        config=m['configs'][f"{cell['task_id']}:{cell['seed']}"]
        memory,_=load_memory(store,key,version,previous,config,cell['planner_features']['memory'],directory)
    from . import human_proxy as hp
    config=m['configs'][f"{cell['task_id']}:{cell['seed']}"];proxy=hp.from_config(config)
    if proxy and cell.get('planner_features'):raise DevConflict('HumanProxy requests are not supported together with the planner wait-work features')
    last_dir=directory.parent/f'v{version-1}'
    if decision_path.exists():decision=json.loads(decision_path.read_bytes())
    else:
        common=dict(demo_available=remaining>0,memory=memory,trace_visible=m['trace_visible'],review_available=proxy is not None)
        round1_path=directory/'decision-round1.json'
        decision=json.loads(round1_path.read_bytes()) if round1_path.exists() else decide(policy,params,version,feedback,failures,prior,help_remaining=int(remaining),**common)
        if decision['action']=='request' and decision['request_type']=='review':
            # Round 1 asked to see the result of the last version: run the review (charged to the shared request
            # budget), then decide again with its pass/fail outcome as the only new policy input.
            if proxy is None:raise DevConflict('review requested without a HumanProxy')
            immutable(round1_path,decision)
            record=proxy_answer(store,key,'review',version-1,version-1,config,proxy,owner,last_dir/'evaluator-review.json',m,client)
            outcome={'passed':record['passed'] is True,'feedback':record['feedback'] if record['status']=='completed' else ''}
            now=store.cell(key);remaining2=max(0,now['budget']['help_count']-now['used']['help_count'])
            decision=decide(policy,params,version,feedback,failures,prior,help_remaining=int(remaining2),requests_made=('review',),review=outcome,**{**common,'demo_available':remaining2>0})
            decision['preceded_by']='review';remaining=remaining2
    ref=immutable(directory/'decision.json',decision);help_record=None
    action=decision['action']
    help_path=directory/'help.json'
    if help_path.exists():
        saved=json.loads(help_path.read_bytes());help_record={'uri':str(help_path.resolve()),'sha256':sha(help_path)}
        job=next((j for j in cell['jobs'] if j['version']==version and j['kind'] in {'demo','advisor','human','cache'}),None)
        if job and job['state']=='settled':
            packet={k:v for k,v in decision.items() if k!='context'};packet['help']={k:saved[k] for k in ['provider','content','rejected'] if k in saved}
            if cell.get('planner_features'):packet['memory']=memory['entries']
            public_screen(packet)
            return packet,ref,help_record
    if action=='use_demo':
        demo=DemoProvider().result(m['demos'][cell['task_id']]);job=store.reserve(key,version,'demo',{'help_count':1,'help_credits':1,'help_wait_seconds':0})
        if not job.get('actual'):store.claim(job['job_id'],owner)
        help_record=immutable(directory/'help.json',demo);store.settle(job['job_id'],{'help_count':1,'help_credits':1,'help_wait_seconds':0},{'help':help_record,'source':demo['source']})
    elif action=='request' and proxy is not None:
        if decision['request_type']!='hint':raise DevConflict('only a hint can follow a review')
        hypothesis=cell['versions'][-1]['reason'] if cell['versions'] else None
        record=proxy_answer(store,key,'hint',version-1,version,config,proxy,owner,directory/'human-proxy-hint.json',m,client,hypothesis)
        text=record['feedback'] if record['status']=='completed' else ''
        try:hint=hp_result(text,record)
        except ValueError:hint={'provider':'human_proxy','content':'','rejected':True,'reason':'public help boundary rejected response'}
        help_record=immutable(directory/'help.json',{**hint,'human_proxy':{'uri':str(directory/'human-proxy-hint.json'),'sha256':sha(directory/'human-proxy-hint.json')}})
    elif action=='request':
        if decision['request_type']!='hint':raise DevConflict('review request needs a HumanProxy')
        provider=AdvisorProvider();msgs=provider.messages(cell['task_id'],previous,feedback,m['trace_visible'])
        if cell.get('planner_features'):
            advisor_packet=json.loads(msgs[-1]['content']);advisor_packet['memory']=memory['entries']
            msgs[-1]['content']=json.dumps(advisor_packet,ensure_ascii=False,sort_keys=True)
        text,request,response,usage=provider_call(store,key,version,'advisor',msgs,owner,directory,client)
        # Model help is content only; never authorization.
        try:hint=provider.result(text)
        except ValueError as exc:
            # Known paid response was rejected; no injection persisted in visible help.
            hint={'provider':'advisor','content':'','rejected':True,'reason':'public help boundary rejected response'}
        help_record=immutable(directory/'help.json',{**hint,'request':request,'response':response,'usage':usage})
    elif action=='retrieve_memory':
        help_record=immutable(directory/'help.json',{'provider':'cache','content':decision['memory']})
        job=store.reserve(key,version,'cache',{'help_count':1,'help_credits':1,'help_wait_seconds':0})
        if not job.get('claimed_at') and not job.get('actual'):store.claim(job['job_id'],owner)
        store.settle(job['job_id'],job['reservation'],{'help':help_record})
    packet={k:v for k,v in decision.items() if k!='context'}
    if help_record:
        saved=json.loads(checked(help_record).read_bytes());packet['help']={k:saved[k] for k in ['provider','content','rejected'] if k in saved}
    if cell.get('planner_features'):packet['memory']=memory['entries']
    public_screen(packet)
    return packet,ref,help_record

def execute(store,key,version,record,config,owner,directory,runner_factory,client=None,*,memory_validation=None):
    cell=store.cell(key);goal=store.goal_for_cell(key);cleanup=cell.get('runtime',{}).get('startup_cleanup_seconds',25);seconds=min(config['attempt_wall_seconds'],goal['wall_remaining']-cleanup,cell['budget']['execution_seconds']-cell['used']['execution_seconds']-cleanup)
    if seconds<=20:raise DevBudgetExhausted('insufficient execution and cleanup time')
    reservation={'executions':1,'sdk_calls':config['sdk_call_limit'],'control_steps':config['horizon'],'execution_seconds':seconds+cleanup}
    memory_path=Path(directory)/'memory-input.json'
    if cell.get('planner_features',{}).get('memory') and memory_path.is_file():
        from .dev_planner_memory import DevMemory,client_for
        settings=cell['planner_features']['memory']
        DevMemory(client_for(settings),settings,config).recheck(json.loads(memory_path.read_bytes()))
    approval_id=None
    if cell.get('planner_features'):
        from .dev_planner import wait_for_approval
        approval_id=wait_for_approval(store,key,version,checked(record['source']).read_text(),config,owner,directory,cell['planner_features'],feedback_for(cell)[0],real_action=runner_factory is None)
    if approval_id:
        seconds=min(seconds,store.goal_for_cell(key)['wall_remaining']-cleanup)
        if seconds<=20:raise DevBudgetExhausted('insufficient execution time after human wait')
        reservation['execution_seconds']=seconds+cleanup
    if cell.get('planner_features',{}).get('memory') and memory_path.is_file():
        DevMemory(client_for(settings),settings,config).recheck(json.loads(memory_path.read_bytes()))
    def observe(pid):
        identity=process_identity(pid);proc=Path('/proc')/str(pid)
        canary={'identity':identity,'cmdline':(proc/'cmdline').read_bytes().replace(b'\x00',b' ').decode(),'cwd':str((proc/'cwd').resolve()),'exe':str((proc/'exe').resolve())}
        if 'openarm365_dev/' in canary['cmdline'] or 'openarm365_dev.owner' in canary['cmdline']:raise DevConflict('old native owner prohibited')
        immutable(directory/f'owned-process-{pid}.json',canary)
    def episode(path):store.update_cell(key,active_version=version,active_episode=path)
    factory=runner_factory or OpenArm365DevRunnerV2
    runner=factory(service_source_root=ROOT/'openarm365_dev_v2',sim_python=SIM_PYTHON,sim_source_root=ROBocasa,task_source_root=ROBOSUITE,asset_manifest=ASSETS,record_replay=True,independent_evaluation=True,service_process_observer=observe,episode_observer=episode,**({'memory_validation':memory_validation} if memory_validation else {}))
    canary={'module':type(runner).__module__,'file':inspect.getfile(type(runner)),'service_source_root':str(ROOT/'openarm365_dev_v2'),'simulated':runner_factory is not None}
    if runner_factory is None and (type(runner) is not OpenArm365DevRunnerV2 or type(runner).__module__!='openarm365_dev_v2.runner'):raise DevConflict('v2 runner required')
    immutable(directory/'runner-canary.json',canary)
    done=threading.Event()
    def watch():
        while not done.wait(.1):
            with store.tx() as db:exp=store.get(db,'experiments',cell['experiment_id'])
            if exp['cancelled'] or store.goal_for_cell(key)['wall_remaining']<=0:runner.cancel_event.set()
    watcher=threading.Thread(target=watch,daemon=True)
    config_path=Path(cell['artifact_dir'])/'config.json'
    request=FormalRunRequest(suite='robocasa365',task_id=config['task_id'],seed=config['seed'],policy_code_path=Path(record['source']['uri']),policy_sha256=record['source']['sha256'],config_path=config_path,config_sha256=sha(config_path),artifact_root=directory/'executions',overall_deadline_seconds=seconds+cleanup,attempt_deadline_seconds=seconds,sdk_call_limit=config['sdk_call_limit'],run_id='run:'+key,attempt_id=f'attempt:{key}:{version-1}',entry_lock=None)
    job=store.reserve(key,version,'execute',reservation)
    try:store.claim(job['job_id'],owner,approval_id=approval_id)
    except (DevConflict,DevBudgetExhausted):
        current=job_for(store.cell(key),version,'execute')
        if current['state']=='reserved':store.release_unclaimed(job['job_id'])
        if approval_id:raise PlannerActionBlocked('motion approval invalidated before dispatch')
        raise
    watcher.start()
    try:
        result=run_with_formal_boundary(request,runner=runner)
        bundle=immutable(directory/'bundle.json',{**result,'channel':'dev_v2','formal_eligible':False,'dev_version':version,'policy_id':cell['policy_id'],'config_lock':{'uri':str(config_path),'sha256':sha(config_path)}})
        if result.get('infrastructure_failure') or result.get('outcome_unknown'):raise DevConflict('native infrastructure/dispatch unknown; no revision/retry')
        trace=json.loads(checked(result['artifacts']['trace']).read_bytes());projected={'schema_version':'attentionbench.dev-public-feedback.v1','execution_status':'condition_invalid','public_error':'scene_contract_mismatch','action_trace':[]} if result['status']=='condition_invalid' else public_feedback(trace)
        episode_root=Path(result['artifact_dir'])
        for label in ['initial','final']:
            path=episode_root/(label+'_public_observation.json')
            if path.exists():
                obs=json.loads(path.read_bytes());public_screen(obs)
                projected[label+'_observation']={'robot':{k:numbers(v) for k,v in obs['robot'].items()},'objects':{k:numbers(v) for k,v in obs['perception']['values'].items()}}
        public_screen(projected);feedback=immutable(directory/'public-feedback.json',projected)
        receipt=json.loads(checked(result['artifacts']['sandbox_receipt']).read_bytes())
        actual={'executions':1,'sdk_calls':receipt['sdk_call_budget']['dispatched'],'control_steps':receipt['control_steps'],'execution_seconds':receipt['elapsed_seconds']}
        store.settle(job['job_id'],actual,{'bundle':bundle,'scene_condition':result.get('scene_condition'),'public_feedback':feedback,'trace':result['artifacts']['trace'],'native_result':result['artifacts']['native_result'],'recording':result['recording'],'execution_status':result['status']})
        memory_path=directory/'memory-input.json'
        if cell.get('planner_features') and memory_path.exists():
            from .dev_planner_memory import journal_result
            journal_result(store,key,version,json.loads(memory_path.read_bytes()),record['source'],feedback,directory)
        if result['status']=='condition_invalid':store.update_cell(key,state='condition_invalid',reason='scene_contract_mismatch',scene_condition=result.get('scene_condition'))
    except BaseException as exc:
        current=job_for(store.cell(key),version,'execute')
        if current['state']!='settled':store.unknown(job['job_id'],f'{type(exc).__name__}: {exc}',{'bundle':bundle} if 'bundle' in locals() else None)
        raise
    finally:
        done.set();watcher.join(2);store.update_cell(key,active_episode=None,active_version=None)
    # No automatic review: a review happens only when the policy requests it (see attention()).

class HumanProxyInvalid(Exception):
    """Vision was unavailable after retries: the cell is marked condition_invalid, never answered text-only."""

def proxy_client(client):
    if client is not None:return client
    credential=os.getenv('PARCC_API_KEY') or os.getenv('LITELLM_KEY')
    if not credential:
        path=Path('/home/truares/.litellm-key')
        if path.is_file():credential=path.read_text().strip()
    return ParccClient(api_key=credential,max_attempts=1,timeout_seconds=90)

def proxy_answer(store,key,kind,exec_version,job_version,config,proxy,owner,artifact,m,client,hypothesis=None):
    """One HumanProxy request (review or hint), reserved, claimed and settled in the shared ledger.

    The recording and frames are those of version exec_version. Review jobs use kind 'eval' (the legacy ledger name
    for review requests) and hint jobs use kind 'advisor'; both draw on help_count/help_credits, so review and hint
    share one request budget, and each records its own logical human seconds. Returns the saved record."""
    from . import human_proxy as hp
    from .v2_evaluator import build_eval_evidence,assert_public
    job_kind='eval' if kind=='review' else 'advisor'
    cell=store.cell(key);artifact=Path(artifact)
    existing=job_for(cell,job_version,job_kind)
    if existing and existing['state']=='settled':
        record=json.loads(artifact.read_bytes())
        if record.get('status')=='invalid_vision_unavailable':raise HumanProxyInvalid(record['error'])
        return record
    if existing and existing['state'] in {'claimed','needs_review'}:raise DevConflict('existing claim cannot be redispatched')
    execute_job=job_for(cell,exec_version,'execute')
    if not execute_job or execute_job['state']!='settled':raise DevConflict('a request needs a settled execution to look at')
    bundle=json.loads(checked(execute_job['receipt']['bundle']).read_bytes());episode_root=Path(bundle['artifact_dir'])
    evidence=build_eval_evidence(episode_root,config)
    if kind=='hint' and hypothesis:
        try:public_screen(hypothesis);assert_public(hypothesis);evidence['agent_hypothesis']=hypothesis[:1500]
        except (ValueError,AssertionError):evidence['omissions'].append('agent hypothesis rejected by public evidence boundary')
    bound=hp.worst_case_input_bound(evidence);seconds=cell.get('runtime',{}).get('model_reserve_seconds',hp.MODEL_RESERVE_SECONDS)
    recording_seconds=(execute_job.get('actual') or {}).get('execution_seconds',0)
    logical=hp.logical_seconds(kind,recording_seconds,proxy)
    human_key='review_human_seconds' if kind=='review' else 'hint_human_seconds'
    reservation={'eval_calls' if kind=='review' else 'advisor_calls':1,'provider_tokens':bound+hp.MAX_TOKENS,'input_tokens':bound,'output_tokens':hp.MAX_TOKENS,
                 'model_seconds':seconds,'help_count':1,'help_credits':1,'help_wait_seconds':seconds,human_key:logical}
    if store.goal_for_cell(key)['wall_remaining']<seconds:raise DevBudgetExhausted('insufficient request time budget')
    store.reserve_check(key,reservation) if not existing else None
    job=store.reserve(key,job_version,job_kind,reservation)
    store.claim(job['job_id'],owner)
    allow_text=m['selection']['mode'] in ('interactive','diagnostic')
    cache_dir=Path(cell['artifact_dir']).parent.parent/'human-proxy-cache'
    try:
        record=hp.ask(kind,evidence,proxy,proxy_client(client),cache_dir=cache_dir,allow_text_only=allow_text)
    except hp.HumanProxyUnavailable as exc:
        record={'schema_version':hp.SCHEMA,'kind':kind,'status':'invalid_vision_unavailable','error':str(exc)[:300],'passed':False,'feedback':'','mode':'vision','model':proxy['model'],'frames':[{k:f[k] for k in ('filename','sha256','view') if k in f} for f in evidence['frames']],'usage':{}}
        immutable(artifact,record)
        # Attempts may have been paid for; the reservation is retained and the cell can no longer yield formal data.
        store.settle(job['job_id'],job['reservation'],{'review':{'uri':str(artifact),'sha256':sha(artifact)},'usage_accounting':'reservation_retained','invalid':'human_proxy_vision_unavailable'})
        raise HumanProxyInvalid(record['error'])
    except Exception:
        # Unknown cost is charged at reservation and the job stays settled-with-error: no silent retry.
        store.settle(job['job_id'],job['reservation'],{'review_error':'provider_failure','usage_accounting':'reservation_retained'})
        raise
    record['requested']=True;record['request_type']=kind
    record['logical_human_seconds']=logical;record['recording_seconds']=recording_seconds
    record['answer_latency_seconds']=proxy['answer_latency_seconds']
    if kind=='hint':
        try:public_screen(record['feedback'])
        except ValueError:record.update(status='rejected',feedback='')
    immutable(artifact,record)
    actual=dict(reservation);u=record['usage'];values=[u.get(k) for k in ('total_tokens','prompt_tokens','completion_tokens')]
    if record['cache_hit']:actual.update(provider_tokens=0,input_tokens=0,output_tokens=0,model_seconds=0,help_wait_seconds=0)
    else:
        if all(type(v) is int and v>=0 for v in values) and values[0]==values[1]+values[2] and all(v<=reservation[k] for v,k in zip(values,('provider_tokens','input_tokens','output_tokens'))):
            actual.update(provider_tokens=values[0],input_tokens=values[1],output_tokens=values[2])
        actual['model_seconds']=min(seconds,record['latency_seconds']);actual['help_wait_seconds']=actual['model_seconds']
    store.settle(job['job_id'],actual,{'review':{'uri':str(artifact),'sha256':sha(artifact)},'cache_hit':record['cache_hit'],'usage_accounting':'cache_replay' if record['cache_hit'] else 'measured' if actual['provider_tokens']!=reservation['provider_tokens'] else 'reservation_retained'})
    return record

def requested_review(cell):
    """The review of the last version, only if the policy requested it and it was answered and usable."""
    if not cell['versions']:return None
    last=cell['versions'][-1];path=Path(last['source']['uri']).parent/'evaluator-review.json'
    if not path.is_file():return None
    try:
        value=json.loads(path.read_bytes())
        if value.get('schema_version')!='attentionbench.human-proxy.v1' or value.get('requested') is not True or value.get('status')!='completed':return None
        review={k:value[k] for k in ('passed','feedback')}
        if value.get('mode')=='text_only':review['mode']='text_only'
        public_screen(review)
        if not isinstance(review['feedback'],str) or (type(review['passed']) is not bool and not (review['passed'] is None and review.get('mode')=='text_only')):return None
        return review
    except (OSError,ValueError,KeyError):return None

def run_cell(store,exp,m,key,through_version,owner,*,client=None,runner_factory=None):
    from .dev_experiment import verify_manifest
    cell=store.cell(key);root=Path(exp['artifact_dir'])/'cells'/key;root.mkdir(parents=True,exist_ok=True)
    config=m['configs'][f"{cell['task_id']}:{cell['seed']}"];source=m['initial_sources'][cell['task_id']]['source']
    created=cell.get('created_at') or time.time()
    store.update_cell(key,state='running',owner=owner,created_at=created,artifact_dir=str(root),initial_source=source)
    if not (root/'config.json').exists():immutable(root/'config.json',config)
    if not (root/'session-lock.json').exists():immutable(root/'session-lock.json',{'cell':key,'manifest':exp['manifest'],'config':{'uri':str(root/'config.json'),'sha256':sha(root/'config.json')},'initial_source_sha256':m['initial_sources'][cell['task_id']]['sha256'],'budget':cell['budget'],'model':m['model'],'formal_eligible':False})
    try:
        if cell.get('planner_features'):store.start_cell_clock(key)
        while True:
            verify_manifest(exp);cell=store.cell(key)
            with store.tx() as db:current=store.get(db,'experiments',cell['experiment_id'])
            if cell['state']=='condition_invalid':return
            if current['cancelled']:store.update_cell(key,state='cancelled',reason='operator_cancel');return
            # Task boundary only. Any interactive switch makes a mixed session.
            if cell.get('pending_policy'):
                store.update_cell(key,policy_id=cell['pending_policy'],pending_policy=None,policy_segments=cell['policy_segments']+[cell['pending_policy']])
                cell=store.cell(key)
            if 'goal_id' in m and len(cell['versions'])>=m['target_depth'] and sum(j['kind']=='execute' and j['state']=='settled' for j in cell['jobs'])>=m['target_valid_executions'] and not any(j['kind']=='model' and j['state']=='settled' and j['version']>len(cell['versions']) for j in cell['jobs']):
                store.update_cell(key,state='completed',reason='target_G_E_reached');return
            last=cell['versions'][-1] if cell['versions'] else None
            if last and last['valid'] and ('goal_id' not in m or sum(j['kind']=='execute' and j['state']=='settled' for j in cell['jobs'])<m['target_valid_executions']):
                job=job_for(cell,last['version'],'execute')
                if not job:
                    execute(store,key,last['version'],last,config,owner,root/f"v{last['version']}",runner_factory,client);continue
                if job['state']!='settled':raise DevConflict('unsettled execution never redispatched')
            count=len(cell['versions'])
            if 'goal_id' in m:
                executions=sum(j['kind']=='execute' and j['state']=='settled' for j in cell['jobs'])
                complete=count>=m['target_depth'] and executions>=m['target_valid_executions']
                pending=any(j['kind']=='model' and j['state']=='settled' and j['version']>count for j in cell['jobs'])
                if not pending and (complete or count>=m['max_versions']):
                    store.update_cell(key,state='completed' if complete else 'failed',reason='target_G_E_reached' if complete else 'max_versions_reached');return
                if not pending and count>=through_version:store.update_cell(key,state='planned',reason='generation_layer_boundary');return
            if 'goal_id' not in m and count>=through_version:
                store.update_cell(key,state='completed' if count>=2 else 'planned',reason='fixed_iteration_boundary');return
            if not ('goal_id' in m and pending) and (store.goal_for_cell(key)['remaining']['dev_calls']<1 or (store.goal_for_cell(key)['remaining']['executions']<1 and ('goal_id' not in m or executions<m['target_valid_executions']))):raise DevBudgetExhausted('original goal count budget exhausted before generation')
            version=count+1;directory=root/f'v{version}';directory.mkdir(exist_ok=True)
            feedback,previous,failures,prior=feedback_for(cell)
            if not last:previous=source
            packet,decision_ref,help_ref=attention(store,key,version,feedback,previous,failures,prior,m,owner,directory,client)
            if packet['action']=='stop':store.update_cell(key,state='failed',reason=packet['reason']);return
            remaining={k:max(0,cell['budget'][k]-store.cell(key)['used'][k]) for k in cell['budget']}
            if 'goal_id' in m:
                for resource in ['model_seconds','execution_seconds','help_wait_seconds']:
                    remaining[resource]=max(0,cell['budget'][resource]-sum(j['reservation'].get(resource,0) for j in store.cell(key)['jobs'] if j['state']=='settled'))
            review=requested_review(store.cell(key))
            if review is not None:feedback={**feedback,'human_proxy_review':review}
            messages=model_messages(config,previous,feedback,packet,remaining,m['trace_visible'],m.get('source_size_limit_bytes'))
            if cell.get('planner_features') and cell['planner_features']['memory'] and not (job_for(store.cell(key),version,'model') or {}).get('actual'):
                from .dev_planner_memory import load_memory
                load_memory(store,key,version,previous,config,cell['planner_features']['memory'],directory)
            text,request,response,usage=provider_call(store,key,version,'model',messages,owner,directory,client)
            text=text.strip()
            if text.startswith('```'):text=re.sub(r'^```(?:json)?\s*|\s*```$','',text)
            try:
                obj=json.loads(text);new_source=obj['source'];reason=obj['reason']
                if not isinstance(new_source,str) or not isinstance(reason,str):raise ValueError('source/reason must be strings')
                public_screen(new_source);public_screen(reason)
                if m.get('source_size_limit_bytes') and len(new_source.encode())>m['source_size_limit_bytes']:raise ValueError('source_size_limit_exceeded')
            except Exception as exc:new_source=previous;reason='Provider output invalid; complete previous source retained without execution';error=f'JSON/source format invalid: {type(exc).__name__}'
            else:
                try:validate_generated_policy(new_source);error=None
                except Exception as exc:error=validation_diagnostic(new_source,exc,config={'schema_version':'attentionbench.openarm365-dual-dev-config.v1','robot':'OpenArm365','execution_arm':'both'})
            # Diagnostics are authored-code errors only; reject contaminated text.
            if error:
                try:public_screen(error)
                except ValueError:error='Generated code violates the public SDK validator'
            src=immutable(directory/'policy.py',new_source);diff=immutable(directory/'diff.patch',''.join(difflib.unified_diff(previous.splitlines(True),new_source.splitlines(True),fromfile=f'v{version-1}',tofile=f'v{version}')))
            record={'version':version,'source':src,'diff':diff,'reason':reason,'valid':error is None,'validation_error':error,'model_input':request,'model_output':response,'usage':usage,'decision':decision_ref,'help':help_ref,'manifest':exp['manifest'],'config_sha256':sha(root/'config.json'),'formal_eligible':False}
            immutable(directory/'version.json',record);store.version(key,record)
            if cell.get('planner_features'):
                from .dev_planner import PlannerControl
                PlannerControl(store).associate_generation(key,version,request,src)
                memory_path=directory/'memory-input.json'
                if memory_path.exists():
                    with store.tx() as db:store.event(db,'planner_memory_generation',{'cell':key,'version':version,'memory_packet':{'uri':str(memory_path),'sha256':sha(memory_path)},'model_input':request,'result_source':src})
            if cell.get('planner_features') and cell['planner_features']['memory'] and help_ref:
                from .dev_planner_memory import stage_candidate
                feedback_ref=immutable(directory/'candidate-public-feedback.json',feedback)
                stage_candidate(store,key,version,help_ref,src,feedback_ref,directory)
    except HumanProxyInvalid as exc:
        store.update_cell(key,state='condition_invalid',reason='human_proxy_vision_unavailable: '+str(exc)[:200])
    except PlannerActionBlocked as exc:
        store.update_cell(key,state='cancelled' if 'cancelled' in str(exc) else 'failed',reason=str(exc))
    except DevBudgetExhausted as exc:
        cell=store.cell(key);store.update_cell(key,state='failed' if cell['versions'] else 'not_started',reason=str(exc))
    except BaseException as exc:
        # Already claimed failures are held by provider_call/execute; no retries.
        unresolved=any(j['state'] in {'claimed','needs_review'} for j in store.cell(key)['jobs'])
        store.update_cell(key,state='needs_review' if unresolved else 'planned',reason=f'{type(exc).__name__}: {exc}');raise
    finally:store.update_cell(key,owner=None)
