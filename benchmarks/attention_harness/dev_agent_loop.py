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


def fatal_cause(broker, session_failures):
    if broker is not None and broker.quarantined:
        detail=str((broker.failure or {}).get('detail',''))
        return 'ledger_invariant' if detail.startswith('BrokerInvariant: ledger_') or 'ledger_' in detail[:40] else 'broker_invariant'
    if session_failures>=SESSION_FAILURE_LIMIT:return 'session_failure'
    return None


def bootstrap(workspace, ledger, policy):
    from .dev_agent_prompts import DEV_PROMPT
    return DEV_PROMPT+'\nHelp policy: '+policy+'. See docs/tools.md for its exact availability.\nBudgets: '+json.dumps(ledger.limits,sort_keys=True)



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
          memory=None, exporter=None):
    """Injectable offline seam; production always supplies a preflighted session."""
    stop=None;successful={};completion_failures={};delivered=0;idle=0;broker=None;events=[];session_failures=0;export_errors=[];memory_errors=[]
    state_path=workspace.private/'loop.json'
    if state_path.exists():
        state=json.loads(state_path.read_bytes());delivered=state['delivered'];idle=state['idle'];events=state['events']
    def save():atomic_write(workspace.private,'loop.json',encode(dict(delivered=delivered,idle=idle,events=events)))
    def cut(kind,response):
        number=int(response['version_id'][1:])
        label=kind+'-v'+str(number)+('-pre-help' if kind=='post-feedback' else '')
        freeze_origin(workspace,label,metadata=dict(version_id=response['version_id'],execution_id=response.get('execution_id'),
            budgets=ledger.snapshot(),session=session.checkpoint(),request_cursor=len(broker.records)),quiesce=session.quiesce)
    def execution(response,directory,result):
        if response['op']=='submit':
            if result.get('memory_revoked'):return
            try:successful[response['version_id']]=completion_reader(executor,directory,result)
            except Exception as exc:
                # Unevaluated/unreadable native: not completed, recorded privately only.
                successful[response['version_id']]=None
                completion_failures[response['version_id']]=type(exc).__name__+': '+str(exc)
    def version(response,value):
        nonlocal stop
        vid=response['version_id'];success=successful.get(vid)
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
        elif value.get('eval',{}).get('passed') is True:
            value['message']=CONTINUE_MESSAGE
            workspace.publish('versions/'+vid+'/continue.json',dict(message=CONTINUE_MESSAGE))
        if memory and stop is None:
            previous=(workspace.private/'sources'/(response['request_id']+'.py')).read_text()
            try:value['memory_message']=memory.open(int(vid[1:])+1,previous)
            except Exception as exc:
                # Memory is advice; an unavailable service delivers no notes for vN+1.
                memory_errors.append(dict(version=int(vid[1:])+1,error=type(exc).__name__+': '+str(exc)))
    def settled(response):
        nonlocal stop
        if exporter:
            # Export is a per-version step (r6 policy): a failure is recorded on the
            # cell and in the result, never a fatal stop; import simply refuses it.
            try:exporter.settle(response,memory)
            except Exception as exc:export_errors.append(dict(request_id=response['request_id'],error=type(exc).__name__+': '+str(exc)))
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
            on_execution=execution,on_settled=settled,quiesce=session.quiesce,memory=memory)
        unknown=ledger.recover()
        if broker.quarantined or unknown:raise ValueError('unresolved_agent_work')
        for path in (workspace.private/'versions').glob('*.json'):
            if json.loads(path.read_bytes()).get('native_success') is True:
                return dict(state='completed',reason='native_success',resources=ledger.snapshot())
        ledger.resume()
        initial_help=helper.initial() if helper and hasattr(helper,'initial') else None
        prompt=bootstrap(workspace,ledger,policy) if not session.thread_id else continuation()
        if memory:prompt+='\n'+memory.start()
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
            idle=idle+1 if len(broker.records)==before else 0
            save()
            if idle>=3:stop='agent_idle';break
            unseen=events[delivered:];delivered=len(events)
            prompt=('No formal submission yet; ' if not broker.histories['versions'] else '')+'Continue the task. History files are authoritative. '
            if failure:prompt='The previous turn ended with an error: '+failure[:300]+'. '+prompt
            prompt+='Unseen events: '+json.dumps(unseen,sort_keys=True)+'. Budgets left: '+json.dumps(ledger.snapshot()['remaining'],sort_keys=True)
            if memory:prompt+='\n'+memory.message
            workspace.publish('events/resume-t%06d.json'%(session.turn+1),dict(message=prompt));save()
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
    finally:
        session.cancel('controller_exit')
        if broker:broker.close()
        if stop!='operator_paused' and helper and hasattr(helper,'finish'):
            helper.finish()
            if broker:broker._regenerate_history()


def end_line(key, result):
    """The scheduler.log line: reason, the fatal cause and the verbatim error."""
    return 'agent cell %s ended: state=%s reason=%s%s%s'%(key,result['state'],result['reason'],
        ' fatal_cause='+result['fatal_cause'] if result.get('fatal_cause') else '',
        ' error='+result['error'] if result.get('error') else '')


def continuation():
    return 'Continue from the authoritative history files and work/notes.md. Inspect the latest results and budgets with devtool status.'


def run_agent_cell(store, exp, manifest, key, through_version=None, owner=None, *, client=None, runner_factory=None, outer_deadline_epoch=None):
    from .dev_agent_isolation import verify_runtime,codex_identity,IsolationUnavailable
    from .dev_agent_adapters import Executor,Prober
    from openarm365_dev_v2.dev_config import SIM_PYTHON
    cell=store.cell(key);root=Path(exp['artifact_dir'])/'cells'/key
    config=manifest['configs'][f"{cell['task_id']}:{cell['seed']}"]
    store.update_cell(key,state='running',owner=owner,artifact_dir=str(root),dev_mode='agent')
    ledger=None;setup_id=None
    try:
        ledger=AgentLedger(store.path,key,help_limit=manifest['selection']['help_budget'])
        ledger.resume()
        import uuid
        setup_id='setup:'+uuid.uuid4().hex
        ledger.reserve(setup_id,'setup',{});ledger.claim(setup_id)
        workspace=Workspace(root/'agent')
        if not (workspace.canonical/'task.json').exists():
            workspace.prepare(dict(task_id=cell['task_id'],task_prompt=config['task_prompt']),config)
            atomic_write(workspace.ws,'work/main.py',manifest['initial_sources'][cell['task_id']]['source'].encode())
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
        def control():
            with store.tx() as db:current=store.get(db,'experiments',exp['experiment_id'])
            return 'cancel' if current['cancelled'] else 'pause' if current.get('pause_requested_at') else None
        from .dev_agent_eval import Evaluator
        from .dev_agent_attention import Helper
        from .dev_agent_memory import Memory
        from .dev_agent_export import Exporter
        from .dev_v2_loop import proxy_client
        memory=Memory(store,key,workspace,config,manifest['initial_sources'][cell['task_id']]['source'],
            default=manifest['memory'],help_pricing=manifest.get('help_pricing'))
        exporter=Exporter(store,key,workspace,config,ledger)
        helper=Helper(workspace,ledger,policy=cell['policy_id'],parameters=manifest['policies'][cell['policy_id']],
            config=config,client=proxy_client(client,config),demo=manifest['demos'][cell['task_id']],quota=quota,memory=memory)
        condition=manifest.get('success_conditions',{}).get(f"{cell['task_id']}:{cell['seed']}")
        evaluator=Evaluator(workspace,model=manifest['eval_model'],quota=quota,success_condition=condition,
            prompt=manifest['eval_agent_prompt']+(manifest['eval_success_rule'] if condition else ''))
        result=drive(workspace,session,ledger,executor=Executor(config,sim_python=SIM_PYTHON,runner_factory=runner_factory),
            prober=Prober(config,sim_python=SIM_PYTHON,private_dir=workspace.private),evaluator=evaluator,helper=helper,quota=quota,policy=cell['policy_id'],
            control=control,external_deadline=outer_deadline_epoch,version_record=lambda row:store.version(key,row),
            progress=lambda row:store.update_cell(key,agent_resources=row),memory=memory,exporter=exporter)
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
            if cell.get('reason')=='operator_paused':current['paused_at']=time.time()
            if current.get('cancelled'):current['state']='cancelled'
            store.put(db,'experiments',exp_id,current)
    return snapshot(store,exp_id)
