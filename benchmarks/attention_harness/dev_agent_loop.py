"""Trusted agent controller. No private completion evidence enters the session."""
import hashlib
import json
from pathlib import Path
import socket
import sys
import time
import traceback
from .dev_agent_broker import Broker, FakeEvaluator
from .dev_agent_ledger import AgentLedger, LedgerBudget
from .dev_agent_quota import QuotaMonitor
from .dev_agent_replay import freeze_origin
from .dev_agent_session import CodexAgentSession
from .dev_agent_workspace import Workspace, atomic_write, encode

CONTINUE_MESSAGE='Eval judged this version as passed, but the run has not ended; continue.'
# The only causes allowed to end a run early (r5, 10-29). Everything that
# happens inside one version - execution, completion read, Eval, help, export,
# history - is that version's result; native unknown means "not completed".
FATAL_CAUSES=frozenset({'ledger_invariant','broker_invariant','quota_stop','quota_unavailable','wall_stop','operator_cancel',
                        # Added: the Dev model process itself failing 3 turns in a row.
                        'session_failure'})
SESSION_FAILURE_LIMIT=3
# Normal (non-fatal) stop after K consecutive executed versions where Eval passed
# but the native check did not (10-29); flagged for operator review in the UI.
DISAGREEMENT_LIMIT=2
DISK_CHECK_INTERVAL=10


def disagreement_streak(rows):
    streak=0
    for row in sorted(rows,key=lambda r:r['version']):
        streak=streak+1 if row.get('eval_verdict')=='pass' and row.get('native_success') is False else 0
    return streak


def fatal_cause(broker, session_failures):
    if broker is not None and broker.quarantined:
        detail=str((broker.failure or {}).get('detail',''))
        return 'ledger_invariant' if detail.startswith('BrokerInvariant: ledger_') or 'ledger_' in detail[:40] else 'broker_invariant'
    if session_failures>=SESSION_FAILURE_LIMIT:return 'session_failure'
    return None


def bootstrap(workspace, ledger, policy):
    from .dev_agent_prompts import DEV_PROMPT
    prompt=DEV_PROMPT
    if 'trial_runs' in ledger.limits:
        prompt=prompt.replace('10 formal versions, 40 moving executions, 300 read-only probes','1 formal version, 4 no-eval trial runs, 60 read-only probes').replace('21600 active wall seconds','1920 active wall seconds')
    return prompt+'\nHelp policy: '+policy+'. See docs/tools.md for its exact availability.\nBudgets: '+json.dumps(ledger.limits,sort_keys=True)



def trusted_completion(executor, directory, result):
    """Read ONLY the execution's private, digest-bound result reference."""
    ref=getattr(executor,'native_result_ref',None)
    if not isinstance(ref,dict):raise ValueError('completion_evidence_missing')
    path=Path(ref.get('uri',''))
    if not path.resolve().is_relative_to(directory.resolve()):raise ValueError('completion_evidence_outside_execution')
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=ref.get('sha256'):raise ValueError('completion_evidence_digest')
    value=json.loads(data)
    if value.get('evaluated') is not True:raise ValueError('completion_evidence_unavailable')
    return value.get('native_success') is True or value.get('object_in_sink') is True


def drive(workspace, session, ledger, *, executor, prober, evaluator=None, helper=None, quota=None,
          policy='reactive_help', completion_reader=trusted_completion, control=lambda:None,
          version_record=lambda row:None, progress=lambda row:None, external_deadline=None, quota_interval=60,
          memory=None, exporter=None, before_step=lambda kind:True,
          agent_policy_version='agent-policy-v1', validation=False):
    """Injectable offline seam; production always supplies a preflighted session."""
    if agent_policy_version not in ('agent-policy-v1','agent-policy-v2'):
        raise ValueError('unsupported agent policy version')
    memory_visible=True
    if agent_policy_version=='agent-policy-v2' and not validation:
        from .dev_agent_policies import memory_exposed
        memory_visible=memory_exposed(policy)
    stop=None;successful={};completion_failures={};delivered=0;idle=0;broker=None;events=[];session_failures=0;export_errors=[];memory_errors=[]
    state_path=workspace.private/'loop.json'
    if state_path.exists():
        state=json.loads(state_path.read_bytes());delivered=state['delivered'];idle=state['idle'];events=state['events']
    def save():atomic_write(workspace.private,'loop.json',encode(dict(delivered=delivered,idle=idle,events=events)))
    def finish_v2(reason):
        if agent_policy_version!='agent-policy-v2' or not helper or not hasattr(helper,'finish'):return
        cause=fatal_cause(broker,session_failures) if reason=='fatal_error' else None
        trigger=('pause' if reason=='operator_paused' else 'cancel' if reason=='operator_cancel' else
                 'session_failure' if cause=='session_failure' else 'fatal' if reason=='fatal_error' else
                 reason if reason in ('wall_stop','quota_stop','quota_unavailable') else 'run_end')
        deadline=time.monotonic()+ledger.snapshot()['wall_remaining']
        if external_deadline is not None:deadline=min(deadline,time.monotonic()+max(0,external_deadline-time.time()))
        helper.finish(reason=trigger,deadline=deadline)
        if broker:broker._regenerate_history()

    def cut(kind,response):
        number=int(response['version_id'][1:])
        if kind=='pre-submit' and validation and memory:
            from .dev_agent_broker import BrokerInvariant
            try:
                envelope=memory.validation.prepare(response)
                executor.memory_validation=envelope
                if exporter:exporter.memory_validation=envelope
            except Exception as exc:
                traceback.print_exc()
                raise BrokerInvariant('validation_dispatch_failed: '+str(exc)) from exc
        label=kind+'-v'+str(number)+('-pre-help' if kind=='post-feedback' else '')
        freeze_origin(workspace,label,metadata=dict(version_id=response['version_id'],execution_id=response.get('execution_id'),
            budgets=ledger.snapshot(),session=session.checkpoint(),request_cursor=len(broker.records),
            **(dict(agent_policy_version=agent_policy_version,policy_state_sha256=hashlib.sha256(encode(helper.state)).hexdigest(),
                    policy_counters={k:helper.state[k] for k in ('retry_failures','cumulative_failed_versions',
                        'trace_failed_versions','completed_execution_count','last_help_execution_count')})
               if agent_policy_version=='agent-policy-v2' and helper else {})),quiesce=session.quiesce)
    def execution(response,directory,result):
        if response['op']=='submit':
            if result.get('memory_revoked'):return
            try:successful[response['version_id']]=completion_reader(executor,directory,result)
            except Exception as exc:
                # Unevaluated/unreadable native: not completed, recorded privately only.
                successful[response['version_id']]=None
                completion_failures[response['version_id']]=type(exc).__name__+': '+str(exc)
            atomic_write(workspace.private,'completion/'+response['version_id']+'.json',encode(dict(success=successful[response['version_id']],error=completion_failures.get(response['version_id']))))
    def version(response,value):
        nonlocal stop
        vid=response['version_id'];success=successful.get(vid)
        saved=workspace.private/'completion'/(vid+'.json')
        if vid not in successful and saved.exists():
            previous=json.loads(saved.read_bytes());success=previous['success']
            if previous.get('error'):completion_failures[vid]=previous['error']
        row=dict(version=int(vid[1:]),version_id=vid,execution_id=response.get('execution_id'),
                 valid=value['outcome']['status']!='invalid_submission',formal_eligible=False,
                 eval=value.get('eval'),eval_verdict=('pass' if value.get('eval',{}).get('passed') is True else 'fail' if value.get('eval',{}).get('passed') is False else 'unavailable'),native_success=success,
                 agreement=(value.get('eval',{}).get('passed')==success if type(value.get('eval',{}).get('passed')) is bool and type(success) is bool else None))
        row['summary']=broker.records[response['request_id']]['request']['payload'].get('summary', '').strip()
        row['agree']=row['agreement']
        if vid in completion_failures:row.update(native='unknown',native_error=completion_failures[vid])
        row['reason']=broker.records[response['request_id']]['request']['payload'].get('reason') or value['outcome']['status']
        atomic_write(workspace.private,'versions/'+vid+'.json',encode(row))
        if exporter:exporter.capture(response,row,executor)
        else:version_record(row)
        if success:
            stop='native_success';broker.stop_reason='run_complete'
            session.cancel('run_complete')
        elif disagreement_streak(json.loads(p.read_bytes()) for p in (workspace.private/'versions').glob('v*.json'))>=DISAGREEMENT_LIMIT:
            stop='eval_native_disagreement';broker.stop_reason='eval_native_disagreement'
            session.cancel('eval_native_disagreement')
        elif value.get('eval',{}).get('passed') is True:
            value['message']=CONTINUE_MESSAGE
            workspace.publish('versions/'+vid+'/continue.json',dict(message=CONTINUE_MESSAGE))
        if memory and memory_visible and stop is None and not getattr(memory,'validation',None):
            previous=(workspace.private/'sources'/(response['request_id']+'.py')).read_text()
            try:value['memory_message']=memory.open(int(vid[1:])+1,previous,**({'deliver_notes':False} if exporter else {}))
            except Exception as exc:
                memory_errors.append(dict(version=int(vid[1:])+1,error=type(exc).__name__+': '+str(exc)))
    def settled(response):
        nonlocal stop
        if exporter:
            # Export is a per-version step (r6 policy): a failure is recorded on the
            # cell and in the result, never a fatal stop; import simply refuses it.
            try:
                record=exporter.settle(response,memory if memory_visible else None)
                if record is not None:
                    from .dev_agent_memory import stage_provisional
                    stage_provisional(exporter.store,exporter.key,record['version'],
                                      exporter.root/f"v{record['version']}",workspace,ledger)
            except Exception as exc:export_errors.append(dict(request_id=response['request_id'],error=type(exc).__name__+': '+str(exc)))
        if exporter and response['op']=='submit' and memory and memory_visible and stop is None and not getattr(memory,'validation',None):
            vid=response['version_id']
            previous=(workspace.private/'sources'/(response['request_id']+'.py')).read_text()
            try:response['result']['memory_message']=memory.open(int(vid[1:])+1,previous)
            except Exception as exc:
                # Memory is advice; an unavailable service delivers no notes for vN+1.
                memory_errors.append(dict(version=int(vid[1:])+1,error=type(exc).__name__+': '+str(exc)))
        if response['status']=='completed':
            event=dict(event_id=len(events)+1,request_id=response['request_id'],op=response['op'],
                       response='rpc/responses/'+response['request_id']+'.json')
            if response.get('result',{}).get('message'):event['message']=response['result']['message']
            for key in ('eval','help','memory_message'):
                if key in response.get('result',{}):event[key]=response['result'][key]
            if response['op']=='ask_help':event['help']=response.get('result')
            events.append(event);workspace.publish('events/evt%06d.json'%len(events),event);save()
        snap=ledger.snapshot()
        if snap['remaining']['formal_versions']<=0 or snap['remaining']['moving_executions']<=0:
            broker.stop_reason=broker.stop_reason or 'budget_exhausted'
        progress(snap)
    try:
        broker=Broker(workspace,executor=executor,prober=prober,evaluator=evaluator or FakeEvaluator(),
            agent_mode=True,helper=helper,budget=LedgerBudget(ledger),quota=quota,on_cut=cut,on_version=version,
            on_execution=execution,on_settled=settled,quiesce=session.quiesce,memory=memory if memory_visible else None,
            before_step=before_step,agent_policy_version=agent_policy_version,validation=validation)
        unknown=ledger.recover()
        if broker.quarantined or unknown:raise ValueError('unresolved_agent_work')
        for path in (workspace.private/'versions').glob('*.json'):
            if json.loads(path.read_bytes()).get('native_success') is True:
                return dict(state='completed',reason='native_success',resources=ledger.snapshot())
        ledger.resume()
        broker.resume_pending_eval()
        while broker.active is not None:
            if control()=='cancel':broker.stop_reason='operator_cancel';broker.closing.set()
            time.sleep(.02)
        if broker.stop_reason=='operator_paused':stop='operator_paused'
        initial_help=helper.initial() if helper and hasattr(helper,'initial') else None
        prompt=bootstrap(workspace,ledger,policy) if not session.thread_id else continuation()
        if validation:prompt+='\nContinue from origin/policy.py and origin/public-feedback.json; work/main.py contains the failed origin policy.'
        if memory and memory_visible:
            if agent_policy_version=='agent-policy-v1':prompt+='\n'+memory.start()
            else:
                try:prompt+='\n'+memory.start()
                except Exception as exc:
                    memory_errors.append(dict(version=1,error=type(exc).__name__+': '+str(exc)))
                    if not isinstance(exc,PermissionError) and hasattr(memory,'clear'):
                        memory.clear(1,helper.config.get('initial_source','') if helper else '')
        if session.thread_id:
            prompt+=' Unseen events: '+json.dumps(events[delivered:],sort_keys=True)+'. Budgets left: '+json.dumps(ledger.snapshot()['remaining'],sort_keys=True)
            delivered=len(events);save()
        if initial_help:prompt+='\nPre-task demo: '+json.dumps(initial_help,ensure_ascii=False)
        while stop is None:
            command=control()
            if command in ('pause','cancel'):
                stop='operator_paused' if command=='pause' else 'operator_cancel';break
            snap=ledger.snapshot()
            if snap['wall_remaining']<=0 or (external_deadline is not None and time.time()>=external_deadline):stop='wall_stop';break
            if snap['remaining']['formal_versions']<=0 or snap['remaining']['moving_executions']<=0:
                stop='budget_exhausted';break
            if quota:quota.check('before_resume' if session.thread_id else 'before_turn')
            jid='turn:t%06d'%(session.turn+1)
            ledger.reserve(jid,'model_turn',{'model_turns':1});ledger.claim(jid)
            before=len(broker.records);last_sample=time.monotonic();failure=None
            try:
                stream=session.resume(session.thread_id,prompt) if session.thread_id else session.start(prompt)
                for event in stream:
                    if event and quota:quota.event(event)
                    command=control()
                    if command in ('cancel','pause'):
                        broker.stop_reason='operator_cancel' if command=='cancel' else 'operator_paused'
                        if command=='cancel':broker.closing.set()
                        if broker.active is None or command=='cancel':
                            stop=broker.stop_reason;session.cancel(stop)
                    if quota and time.monotonic()-last_sample>=quota_interval:
                        try:quota.check('during_turn')
                        except Exception as exc:
                            broker.stop_reason='quota_stop' if str(exc)=='quota_stop' else 'quota_unavailable'
                        last_sample=time.monotonic()
                    if ledger.snapshot()['wall_remaining']<=0 or (external_deadline is not None and time.time()>=external_deadline):
                        broker.stop_reason='wall_stop';broker.closing.set()
                    broker.poll()
                    if stop is None and broker.stop_reason and broker.active is None:
                        stop=broker.stop_reason;session.cancel(stop)
                    if broker.quarantined and broker.active is None and stop is None:
                        stop='fatal_error';session.cancel(stop)
            except Exception as exc:
                failure=str(exc)
            finally:
                # A turn may finish while an accepted request is still running.
                while broker.active is not None:
                    if control()=='cancel':broker.closing.set()
                    time.sleep(.02)
                ledger.settle(jid,dict(error=failure),usage=session.usage if isinstance(session.usage,dict) else None)
            session_failures=session_failures+1 if failure and stop is None else 0
            if stop:break
            if broker.quarantined or session_failures>=SESSION_FAILURE_LIMIT:stop='fatal_error';break
            # A turn whose only requests were unchanged_source rejections made no progress.
            new=list(broker.records.values())[before:]
            idle=idle+1 if all(r['response'].get('error')=='unchanged_source' for r in new) else 0
            save()
            if idle>=3:stop='agent_idle';break
            unseen=events[delivered:];delivered=len(events)
            prompt=('No formal submission yet; ' if not broker.histories['versions'] else '')+'Continue the task. History files are authoritative. '
            if failure:prompt='The previous turn ended with an error: '+failure[:300]+'. '+prompt
            prompt+='Unseen events: '+json.dumps(unseen,sort_keys=True)+'. Budgets left: '+json.dumps(ledger.snapshot()['remaining'],sort_keys=True)
            if memory and memory_visible:prompt+='\n'+memory.message
            workspace.publish('events/resume-t%06d.json'%(session.turn+1),dict(message=prompt));save()
        finish_v2(stop)
        if stop=='operator_paused':ledger.pause()
        result=dict(state='completed' if stop=='native_success' else 'planned' if stop=='operator_paused' else 'cancelled' if stop=='operator_cancel' else 'needs_review' if stop in ('fatal_error','quota_unavailable') else 'failed',
                    reason=stop,resources=ledger.snapshot())
        if export_errors:result['export_errors']=export_errors
        if memory_errors:result['memory_errors']=memory_errors
        if stop=='fatal_error':
            # Surface the cause; a bare reason hid a probe timeout (10-28).
            cause=fatal_cause(broker,session_failures)
            if cause not in FATAL_CAUSES:raise AssertionError('fatal stop without an allowed cause')
            f=broker.failure or {}
            result['fatal_cause']=cause
            result['error']=(cause+': %s %s %s (%s)'%(f.get('op'),f.get('request_id'),f.get('error'),f.get('detail')) if broker.quarantined
                             else cause+': '+str(failure))
        return result
    except Exception as exc:
        if agent_policy_version!='agent-policy-v2':raise
        stop=('quota_stop' if str(exc)=='quota_stop' else 'quota_unavailable' if 'quota' in str(exc) else
              'wall_stop' if str(exc)=='wall_stop' else 'fatal_error')
        finish_v2(stop)
        return dict(state='failed' if stop in ('quota_stop','wall_stop') else 'needs_review',
                    reason=stop,error=str(exc),resources=ledger.snapshot(),
                    **(dict(fatal_cause='ledger_invariant' if 'ledger' in str(exc) else 'broker_invariant') if stop=='fatal_error' else {}))
    finally:
        session.cancel('controller_exit')
        if broker:broker.close()
        if agent_policy_version=='agent-policy-v1' and stop!='operator_paused' and helper and hasattr(helper,'finish'):
            helper.finish()
            if broker:broker._regenerate_history()


def advisor_client(client, config):
    # Agent runs may use any agent Advisor model; the one-call client stays locked.
    if client is None and config.get('model_source')=='codex':
        from .codex_client import CodexClient
        from .dev_agent_config import ADVISOR_MODELS
        return CodexClient(timeout_seconds=90, models=ADVISOR_MODELS)
    from .dev_v2_loop import proxy_client
    return proxy_client(client,config)


def end_line(key, result):
    """The scheduler.log line: reason, the fatal cause and the verbatim error."""
    return 'agent cell %s ended: state=%s reason=%s%s%s'%(key,result['state'],result['reason'],
        ' fatal_cause='+result['fatal_cause'] if result.get('fatal_cause') else '',
        ' error='+result['error'] if result.get('error') else '')


def continuation():
    return 'Continue from the authoritative history files and work/notes.md. Inspect the latest results and budgets with devtool status.'


def run_agent_cell(store, exp, manifest, key, through_version=None, owner=None, *, client=None, runner_factory=None, outer_deadline_epoch=None, validation=None):
    from .dev_agent_isolation import verify_runtime,codex_identity,IsolationUnavailable
    from .dev_agent_adapters import Executor,Prober
    from openarm365_dev_v2.dev_config import SIM_PYTHON
    cell=store.cell(key);root=Path(exp['artifact_dir'])/'cells'/key
    config=manifest['configs'][f"{cell['task_id']}:{cell['seed']}"]
    store.update_cell(key,state='running',owner=owner,artifact_dir=str(root),dev_mode='agent')
    ledger=None;setup_id=None
    try:
        if bool(manifest.get('validation_experiment'))!=bool(validation):raise ValueError('agent validation runtime binding required')
        if validation:
            from .memory_agent_validation import validate_manifest
            validate_manifest(manifest)
        if manifest.get('agent_policy_version')=='agent-policy-v2':
            from .dev_agent_policy_freeze import validate
            validate(manifest)
            if cell.get('agent_policy_version')!='agent-policy-v2':raise ValueError('agent_policy_cell_version_mismatch')
        ledger=AgentLedger(store.path,key,help_limit=manifest['selection']['help_budget'],validation=validation is not None)
        ledger.resume()
        import uuid
        setup_id='setup:'+uuid.uuid4().hex
        ledger.reserve(setup_id,'setup',{});ledger.claim(setup_id)
        workspace=Workspace(root/'agent')
        condition=manifest.get('success_conditions',{}).get(f"{cell['task_id']}:{cell['seed']}")
        # Dev also reads the public rule (10-29 step 6); earlier manifests kept it Eval-only.
        dev_condition=condition if manifest.get('success_condition_dev_visible') else None
        if not (workspace.canonical/'task.json').exists():
            workspace.prepare(dict(task_id=cell['task_id'],task_prompt=config['task_prompt']),
                dict(config,agent_policy_id=cell['policy_id']) if config.get('agent_policy_version')=='agent-policy-v2' else config,success_condition=dev_condition)
            atomic_write(workspace.ws,'work/main.py',manifest['initial_sources'][cell['task_id']]['source'].encode())
        if validation:
            from .memory_agent_validation import seed
            seed(workspace,validation['current'])
        # The planner uses dynamically bound loopback sockets. Bind a synthetic
        # socket for preflight; nothing contacts a deployed planner or UI.
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen()
            verify_runtime(workspace.ws,workspace.private,planner_port=listener.getsockname()[1])
        identity=codex_identity();identity_path=workspace.private/'codex-identity.json'
        if identity_path.exists() and json.loads(identity_path.read_bytes())!=identity:raise IsolationUnavailable('runtime_identity_changed')
        atomic_write(workspace.private,'codex-identity.json',encode(identity))
        ledger.settle(setup_id,dict(status='prepared'));setup_id=None
        quota=QuotaMonitor(workspace.private,ledger,stop_percent=manifest['quota_stop_percent'])
        session=CodexAgentSession(workspace.ws,workspace.private,model=manifest['dev_model'],reasoning_effort=manifest['dev_reasoning_effort'])
        last_disk_check=None
        def control():
            nonlocal last_disk_check
            now=time.monotonic()
            if last_disk_check is None or now-last_disk_check>=DISK_CHECK_INTERVAL:
                from .dev_disk import pause_if_low
                last_disk_check=now
                pause_if_low(store,exp['experiment_id'])
            with store.tx() as db:current=store.get(db,'experiments',exp['experiment_id'])
            return 'cancel' if current['cancelled'] else 'pause' if current.get('pause_requested_at') else None
        def before_step(kind):
            from .dev_disk import pause_if_low
            return not pause_if_low(store,exp['experiment_id'])
        from .dev_agent_eval import Evaluator
        from .dev_agent_attention import Helper
        from .dev_agent_memory import Memory
        from .dev_agent_export import Exporter
        memory=Memory(store,key,workspace,config,manifest['initial_sources'][cell['task_id']]['source'],
            default=manifest['memory'],help_pricing=manifest.get('help_pricing'),validation=validation)
        exporter=Exporter(store,key,workspace,config,ledger)
        agent_policy_version=config.get('agent_policy_version','agent-policy-v1')
        helper=None if validation else Helper(workspace,ledger,policy=cell['policy_id'],parameters=manifest['policies'][cell['policy_id']],
            config=(dict(config,initial_source=manifest['initial_sources'][cell['task_id']]['source'])
                    if agent_policy_version=='agent-policy-v2' else config),
            client=advisor_client(client,config),demo=manifest['demos'][cell['task_id']],quota=quota,memory=memory,agent_policy_version=agent_policy_version)
        evaluator=Evaluator(workspace,model=manifest['eval_model'],quota=quota,success_condition=condition,
            redact=dev_condition is None,
            prompt=manifest['eval_agent_prompt']+(manifest['eval_success_rule'] if condition else ''))
        execution_options={}
        if validation:
            execution_options=dict(validation_arm=validation['arm_record'],agent_configuration=dict(
                dev_model=session.model,dev_reasoning_effort=session.reasoning_effort,
                eval_model=evaluator.model,agent_policy_version=agent_policy_version))
        result=drive(workspace,session,ledger,executor=Executor(config,sim_python=SIM_PYTHON,runner_factory=runner_factory,**execution_options),
            prober=Prober(config,sim_python=SIM_PYTHON,private_dir=workspace.private),evaluator=evaluator,helper=helper,quota=quota,policy=cell['policy_id'],
            control=control,external_deadline=outer_deadline_epoch,version_record=lambda row:store.version(key,row),
            progress=lambda row:store.update_cell(key,agent_resources=row),memory=memory,exporter=exporter,before_step=before_step,
            agent_policy_version=agent_policy_version,validation=validation is not None)
        store.update_cell(key,**{k:v for k,v in result.items() if k!='resources'},agent_resources=result['resources'],owner=None)
        print(end_line(key,result),file=sys.stderr,flush=True)
        return result
    except Exception as exc:
        traceback.print_exc()
        if setup_id and ledger and any(j['id']==setup_id and j['state']=='claimed' for j in ledger.jobs()):
            ledger.settle(setup_id,dict(status='setup_failed',error=str(exc)))
        reason='quota_stop' if str(exc)=='quota_stop' else 'quota_unavailable' if 'quota' in str(exc) else 'isolation_unavailable' if isinstance(exc,IsolationUnavailable) else 'fatal_error'
        store.update_cell(key,state='failed' if reason=='quota_stop' else 'needs_review',reason=reason,error=str(exc),owner=None)
        print('agent cell %s ended: state=failed reason=%s error=%s'%(key,reason,exc),file=sys.stderr,flush=True)
        return dict(state='failed',reason=reason,error=str(exc))


def run_agent_experiment(store, exp_id, *, max_cells, client=None, runner_factory=None, **unused):
    from .dev_experiment import snapshot,verify_manifest
    from .dev_loop import process_identity,owner_alive
    from .core.dev_store import DevConflict
    exp=snapshot(store,exp_id);manifest=verify_manifest(exp)
    from .dev_disk import require_start,experiment_root
    require_start(experiment_root(exp))
    if max_cells!=1 or len(manifest['cells'])!=1:raise ValueError('agent_single_cell_required')
    owner=process_identity()
    with store.tx() as db:
        current=store.get(db,'experiments',exp_id)
        for raw, in db.execute('SELECT payload FROM v2_experiments'):
            other=json.loads(raw)
            if other.get('owner') and owner_alive(other['owner']):raise DevConflict('live serial experiment owner exists')
        if current['cancelled'] or current['state'] in ('completed','needs_review','paused'):return exp
        current.update(owner=owner,state='running');store.put(db,'experiments',exp_id,current)
    key=manifest['cells'][0]['key']
    try:run_agent_cell(store,exp,manifest,key,owner=owner,client=client,runner_factory=runner_factory,outer_deadline_epoch=unused.get('outer_deadline_epoch'))
    finally:
        cell=store.cell(key)
        with store.tx() as db:
            current=store.get(db,'experiments',exp_id)
            current.update(owner=None,state='paused' if cell.get('reason')=='operator_paused' else 'needs_review' if cell['state']=='needs_review' else 'completed',reason=cell.get('reason'))
            if cell.get('reason')=='operator_paused':
                now=time.time();current['paused_at']=now
                from .dev_ledger import experiment_goal
                gid=experiment_goal(current);goal=store.get(db,'goals',gid);goal['paused_at']=now;store.put(db,'goals',gid,goal)
            if current.get('cancelled'):current['state']='cancelled'
            store.put(db,'experiments',exp_id,current)
    return snapshot(store,exp_id)
