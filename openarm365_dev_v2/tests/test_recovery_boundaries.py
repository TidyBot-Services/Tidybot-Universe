"""Controlled temporary ledgers and complete fake loop artifacts, never real runs."""
import copy
import json
from pathlib import Path
import time

import pytest
from benchmarks.attention_harness import dev_experiment as experiments, dev_v2_loop as loop
from benchmarks.attention_harness import dev_planner as planner, dev_wait_recovery as recovery
from benchmarks.attention_harness.dev_main_bridge import DevMainBridge
from benchmarks.attention_harness.dev_ledger import DevV2Store, encoded
from benchmarks.attention_harness.dev_loop import immutable, process_identity, sha
from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.parcc_client import ParccResponse
from openarm365_dev_v2.tests.fakes import Runner
from openarm365_dev_v2.tests.test_planner_features import SOURCE, SENSOR, add_version, controlled_reply
from openarm365_dev_v2.tests.test_wait_entry_recovery import DEAD


class SensorClient:
    response_observer = None
    def chat(self, **kwargs):
        content = json.dumps({'source':SENSOR,'reason':'controlled sensor-only history'})
        usage = dict(total_tokens=100,prompt_tokens=40,completion_tokens=60)
        if self.response_observer:
            self.response_observer(200,{},json.dumps(dict(model='controlled',choices=[{'message':{'content':content}}],usage=usage)).encode())
        return ParccResponse('controlled',content,None,.01,1,usage)


def pending(tmp_path, *, history=None, deadline_seconds=None):
    store = DevV2Store(tmp_path/'ledger.sqlite')
    same = history == 'same'; maximum = 2 if same else 1
    e = experiments.create(store,tmp_path/'artifacts',dict(request_key='boundary-controlled',goal_key='boundary-goal',
        tasks=['open_cabinet','pick_place_counter_to_sink'] if history in {'other','other_live'} else ['open_cabinet'],
        seeds=[125],policies=[planner.POLICY],mode='experiment',target_depth=maximum,
        target_valid_executions=maximum,max_versions=maximum,help_budget=0,planner_features={'human_wait_seconds':600}))
    manifest = json.loads(Path(e['manifest']['uri']).read_bytes())
    if history:
        # Complete normal model/version/execute path through v1, with bound
        # bundle/trace/native/sandbox/settlement. All fake, no native process.
        loop.run_cell(store,e,manifest,e['cells'][0]['key'],1,process_identity() if history=='other_live' else DEAD,client=SensorClient(),runner_factory=Runner)
        old = store.cell(e['cells'][0]['key'])
        assert len([j for j in old['jobs'] if j['kind']=='execute' and j['state']=='settled']) == 1
    c = store.cell(e['cells'][1 if history in {'other','other_live'} else 0]['key'])
    root = Path(e['artifact_dir'])/'cells'/c['key'];root.mkdir(parents=True,exist_ok=True)
    cfg = manifest['configs'][f"{c['task_id']}:{c['seed']}"]
    if not (root/'config.json').exists():immutable(root/'config.json',cfg)
    store.update_cell(c['key'],state='running',owner=DEAD,artifact_dir=str(root))
    version = 2 if same else 1
    record,d = add_version(store,c,root,version=version)
    if deadline_seconds is not None:
        with store.tx() as db:
            goal=store.get(db,'goals',e['goal_id']);goal['authorized_until']=time.time()+deadline_seconds;store.put(db,'goals',e['goal_id'],goal)
    ctl = planner.PlannerControl(store)
    r = ctl.request(c['key'],version,'approval',planner.binding(c['key'],version,SOURCE,cfg),600)
    job = store.reserve(c['key'],version,'human',{'help_wait_seconds':600});store.claim(job['job_id'],DEAD)
    store.update_cell(c['key'],planner_wait=r['request_id'])
    planner.prepare_independent(store,c['key'],version,'human',SOURCE,{},d,r['request_id'],5)
    with store.tx() as db:
        raw = store.get(db,'experiments',e['experiment_id']);raw.update(state='running',owner=DEAD);store.put(db,'experiments',e['experiment_id'],raw)
    return store,e,c,cfg,r,job,d


def no_dispatch(monkeypatch):
    def fail(*a,**k):raise AssertionError('terminal cleanup dispatched work')
    monkeypatch.setattr(loop,'provider_call',fail)
    monkeypatch.setattr('benchmarks.attention_harness.dev_main_bridge.subprocess.Popen',fail)


@pytest.mark.parametrize('history',['same','other','other_live'])
def test_full_loop_history_allows_only_current_unconsumed_action(tmp_path,monkeypatch,history):
    s,e,c,cfg,r,j,d = pending(tmp_path,history=history)
    ctl = planner.PlannerControl(s);before = s.export_ledger(e['goal_id'])
    old = [job for job in before['jobs'] if job['kind']=='execute'][0]
    oldbytes = Path(old['receipt']['bundle']['uri']).read_bytes()
    controlled_reply(s,r)
    def fail(*a,**kw):raise AssertionError('replayed model/Advisor')
    monkeypatch.setattr(loop,'provider_call',fail)
    work = ctl.snapshot(c['key'])['work']
    from benchmarks.attention_harness import dev_main_bridge as main
    import os
    calls=[]
    class Worker:
        pid=os.getpid()
        def poll(self):return 0
    def spawn(*args,**kwargs):
        calls.append(args)
        experiments.run(s,e['experiment_id'],max_cells=len(e['cells']),client=object(),runner_factory=Runner)
        return Worker()
    monkeypatch.setattr(main.subprocess,'Popen',spawn)
    b=DevMainBridge(s.path,tmp_path/'artifacts','boundary-goal')
    assert b.lifecycle(b.experiment(e['experiment_id'])['cells'][0])['can_start']
    assert b.start(e['experiment_id'])['worker_started'] and len(calls)==1
    after = s.export_ledger(e['goal_id']);current = ctl.get(r['request_id'])
    assert current['state']=='consumed' and current['consumed_by'] != old['job_id']
    assert current['created_at']==r['created_at'] and current['deadline_epoch']==r['deadline_epoch']
    assert next(x for x in after['jobs'] if x['job_id']==old['job_id'])==old
    assert Path(old['receipt']['bundle']['uri']).read_bytes()==oldbytes
    assert ctl.snapshot(c['key'])['work']==work
    assert after['recompute_matches'] and after['goal']['used']['executions']==2
    with pytest.raises(DevConflict):s.claim(current['consumed_by'],process_identity(),approval_id=r['request_id'])
    # Artifacts are retained under pytest's explicitly selected basetemp.
    (tmp_path/'controlled-result.json').write_text(json.dumps(after,indent=2))


@pytest.mark.parametrize('damage',['actual','bundle_sha','trace_identity','counter','cell_counter','missing_job','settlement','reserved','claimed','needs_review','unknown'])
def test_bad_history_never_supports_wait_recovery(tmp_path,damage):
    s,e,c,cfg,r,j,d = pending(tmp_path,history='other')
    old = next(x for x in s.export_ledger(e['goal_id'])['jobs'] if x['kind']=='execute')
    with s.tx() as db:
        if damage in {'actual','bundle_sha'}:
            old.pop('state')
            if damage=='actual':old['actual']['sdk_calls']+=1
            else:old['receipt']['bundle']['sha256']='0'*64
            db.execute('UPDATE v2_jobs SET payload=? WHERE id=?',(encoded(old),old['job_id']))
        elif damage=='trace_identity':
            ref=old['receipt']['trace'];p=Path(ref['uri']);p.chmod(0o600);v=json.loads(p.read_bytes());v['run_id']='wrong';p.write_text(json.dumps(v))
        elif damage in {'counter','cell_counter'}:
            row=s.get(db,'goals' if damage=='counter' else 'cells',e['goal_id'] if damage=='counter' else old['cell']);row['used']['executions']+=1;s.put(db,'goals' if damage=='counter' else 'cells',e['goal_id'] if damage=='counter' else old['cell'],row)
        elif damage=='missing_job':db.execute('DELETE FROM v2_jobs WHERE id=?',(old['job_id'],))
        elif damage=='settlement':db.execute("DELETE FROM v2_events WHERE kind='settled' AND json_extract(payload,'$.job_id')=?",(old['job_id'],))
        else:db.execute('UPDATE v2_jobs SET state=? WHERE id=?',(damage,old['job_id']))
    with s.tx() as db:
        with pytest.raises(DevConflict):experiments.wait_recovery(s,db,e['experiment_id'])
    with pytest.raises(DevConflict):experiments.acquire(s,e['experiment_id'],process_identity())
    assert planner.PlannerControl(s).get(r['request_id'])['state'] in {'waiting','invalidated'}
    assert not planner.PlannerControl(s).get(r['request_id']).get('consumed_by')


@pytest.mark.parametrize('ending',['expired_cancel','timeout','closed','exhausted','source','version','manifest','short_deadline'])
def test_terminal_cleanup_before_dispatch_gates_is_idempotent(tmp_path,monkeypatch,ending):
    s,e,c,cfg,r,j,d = pending(tmp_path,deadline_seconds=5 if ending=='short_deadline' else None)
    no_dispatch(monkeypatch)
    now = time.time(); end = now+610 if ending in {'expired_cancel','timeout','short_deadline'} else now+.5
    if ending=='source':
        p=d/'policy.py';p.chmod(0o600);p.write_text(SOURCE+'# source changed')
    if ending=='version':add_version(s,c,d.parent,version=2)
    if ending=='manifest':
        p=Path(e['manifest']['uri']);p.chmod(0o600);p.write_text('{}')
    with s.tx() as db:
        g=s.get(db,'goals',e['goal_id'])
        if ending in {'expired_cancel','short_deadline'}:g['authorized_until']=r['deadline_epoch']
        if ending=='closed':g['real_finished_at']=now
        if ending=='exhausted':g['used']['executions']=g['limits']['executions']
        s.put(db,'goals',e['goal_id'],g)
    monkeypatch.setattr(recovery.time,'time',lambda:end)
    if ending=='expired_cancel':experiments.cancel(s,e['experiment_id'])
    else:
        b=DevMainBridge(s.path,tmp_path/'artifacts','boundary-goal')
        with pytest.raises((DevConflict,ValueError)):b.start(e['experiment_id'])
    job=next(x for x in s.cell(c['key'])['jobs'] if x['job_id']==j['job_id'])
    assert job['state']=='settled'
    expected=min(r['deadline_epoch'],end)-r['created_at']
    assert job['actual']=={'help_wait_seconds':expected}
    ref=job['receipt']['request'];before=Path(ref['uri']).read_bytes();used=s.goal(e['goal_id'])['used']['help_wait_seconds']
    assert not any(x['kind'] in {'model','advisor','execute'} for x in s.cell(c['key'])['jobs'])
    recovery.reconcile_waits(s,e['experiment_id']);experiments.cancel(s,e['experiment_id']);experiments.cancel(s,e['experiment_id'])
    with pytest.raises(DevConflict):experiments.acquire(s,e['experiment_id'],process_identity())
    assert s.goal(e['goal_id'])['used']['help_wait_seconds']==used and s.goal(e['goal_id'])['reserved']['help_wait_seconds']==0
    assert Path(ref['uri']).read_bytes()==before
    from benchmarks.attention_harness.dev_report import analyze_cell
    # Resource recomputation remains valid even with intentionally minimal test versions.
    analysis=analyze_cell(s.cell(c['key']),c,e['manifest'])
    assert not analysis['resource_errors']


def test_earlier_approved_receipt_preserved_and_reply_time_charged(tmp_path,monkeypatch):
    s,e,c,cfg,r,j,d=pending(tmp_path);approved=controlled_reply(s,r)
    ref=immutable(d/'human-decision-receipt.json',approved);before=Path(ref['uri']).read_bytes()
    now=time.time();monkeypatch.setattr(recovery.time,'time',lambda:now+601)
    experiments.cancel(s,e['experiment_id'])
    job=s.cell(c['key'])['jobs'][0]
    assert job['actual']['help_wait_seconds']==approved['responded_at']-r['created_at']
    assert Path(ref['uri']).read_bytes()==before and job['receipt']['request']!=ref
    with pytest.raises(DevConflict):controlled_reply(s,r,False)


@pytest.mark.parametrize('live',['experiment','cell','job','scheduler'])
def test_live_owner_settles_in_own_finally_only(tmp_path,live):
    s,e,c,cfg,r,j,d=pending(tmp_path)
    with s.tx() as db:
        if live in {'experiment','cell'}:
            table='experiments' if live=='experiment' else 'cells';key=e['experiment_id'] if live=='experiment' else c['key'];row=s.get(db,table,key);row['owner']=process_identity();s.put(db,table,key,row)
        elif live=='job':
            row=json.loads(db.execute('SELECT payload FROM v2_jobs').fetchone()[0]);row['owner']=process_identity();db.execute('UPDATE v2_jobs SET payload=?',(encoded(row),))
        else:immutable(Path(e['artifact_dir'])/'scheduler-process-live.json',process_identity())
    experiments.cancel(s,e['experiment_id']);result=recovery.reconcile_waits(s,e['experiment_id'])
    assert result[0]['reason']=='live owner'
    job=s.cell(c['key'])['jobs'][0]
    assert job['state']=='claimed' and 'actual' not in job
    assert s.goal(e['goal_id'])['reserved']['help_wait_seconds']==600


@pytest.mark.parametrize('damage',['missing_request','missing_creation','missing_claim','timing','binding','owner','responded'])
def test_unknown_human_material_retains_hold_with_reason(tmp_path,damage):
    s,e,c,cfg,r,j,d=pending(tmp_path)
    with s.tx() as db:
        if damage=='missing_request':db.execute('DELETE FROM v2_planner_requests')
        elif damage in {'missing_creation','missing_claim'}:db.execute('DELETE FROM v2_events WHERE kind=?',('claimed' if damage=='missing_claim' else 'planner_request_created',))
        elif damage=='owner':
            raw=json.loads(db.execute('SELECT payload FROM v2_jobs').fetchone()[0]);raw['owner']={'pid':1};db.execute('UPDATE v2_jobs SET payload=?',(encoded(raw),))
        else:
            raw=copy.deepcopy(r)
            if damage=='timing':raw['created_at']+=10
            if damage=='binding':raw['binding']['action_version']=100
            if damage=='responded':raw.update(state='approved',response={'approved':True},responded_at=r['created_at']-1)
            planner.PlannerControl.save(db,raw)
    experiments.cancel(s,e['experiment_id'])
    job=s.cell(c['key'])['jobs'][0]
    assert job['state'] in {'needs_review','claimed'} and not job.get('actual')
    assert s.goal(e['goal_id'])['reserved']['help_wait_seconds']==600
    result=recovery.reconcile_waits(s,e['experiment_id'])
    assert 'human wait reconciliation:' in job['reason']
    if damage=='owner':assert 'unknown owner identity' in job['reason']


@pytest.mark.parametrize('kind',['model','advisor','execute'])
def test_terminal_cleanup_never_refunds_or_retries_unknown_paid_native(tmp_path,monkeypatch,kind):
    s,e,c,cfg,r,j,d=pending(tmp_path);no_dispatch(monkeypatch)
    reservation={'executions':1} if kind=='execute' else {'dev_calls':1} if kind=='model' else {'advisor_calls':0}
    if kind=='execute':add_version(s,c,d.parent,source=SENSOR,version=2)
    paid=s.reserve(c['key'],2,kind,reservation);s.claim(paid['job_id'],DEAD);s.unknown(paid['job_id'],'controlled unknown')
    before=next(x for x in s.cell(c['key'])['jobs'] if x['job_id']==paid['job_id'])
    experiments.cancel(s,e['experiment_id']);recovery.reconcile_waits(s,e['experiment_id'])
    assert next(x for x in s.cell(c['key'])['jobs'] if x['job_id']==paid['job_id'])==before
    with pytest.raises(DevConflict):experiments.acquire(s,e['experiment_id'],process_identity())


def test_receipt_before_transaction_commit_is_reused_after_crash(tmp_path,monkeypatch):
    s,e,c,cfg,r,j,d=pending(tmp_path)
    original=s.settle_in_transaction
    class Crash(BaseException):pass
    def fail(*args,**kwargs):raise Crash('controlled process death before commit')
    monkeypatch.setattr(s,'settle_in_transaction',fail)
    with pytest.raises(Crash):experiments.cancel(s,e['experiment_id'])
    path=d/'terminal-reconciliation'/'human-decision-receipt.json';before=path.read_bytes()
    assert s.cell(c['key'])['jobs'][0]['state']=='claimed'
    monkeypatch.setattr(s,'settle_in_transaction',original)
    experiments.cancel(s,e['experiment_id']);recovery.reconcile_waits(s,e['experiment_id'])
    assert s.cell(c['key'])['jobs'][0]['state']=='settled' and path.read_bytes()==before
    with s.tx() as db:
        assert db.execute("SELECT COUNT(*) FROM v2_events WHERE kind='settled'").fetchone()[0]==1


def test_parallel_stops_charge_original_wait_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    s,e,c,cfg,r,j,d=pending(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _:experiments.cancel(s,e['experiment_id']),range(2)))
    job=s.cell(c['key'])['jobs'][0]
    assert s.goal(e['goal_id'])['used']['help_wait_seconds']==job['actual']['help_wait_seconds']
    with s.tx() as db:
        assert db.execute("SELECT COUNT(*) FROM v2_events WHERE kind='settled'").fetchone()[0]==1


def test_original_wait_counter_without_settled_job_needs_review(tmp_path):
    s,e,c,cfg,r,j,d=pending(tmp_path)
    with s.tx() as db:
        goal=s.get(db,'goals',e['goal_id']);goal['used']['help_wait_seconds']=1;s.put(db,'goals',e['goal_id'],goal)
    experiments.cancel(s,e['experiment_id'])
    job=s.cell(c['key'])['jobs'][0]
    assert job['state']=='needs_review' and 'usage recompute mismatch' in job['reason']
    assert s.goal(e['goal_id'])['reserved']['help_wait_seconds']==600


@pytest.mark.parametrize('entry',['stop','reconcile','dispatch'])
def test_host_cleanup_needs_no_live_authorization_or_source_epoch(tmp_path,monkeypatch,entry):
    from argparse import Namespace
    from benchmarks.attention_harness import dev_integration as host
    s,e,c,cfg,r,j,d=pending(tmp_path);no_dispatch(monkeypatch)
    monkeypatch.setattr(host,'LEDGER',s.path);monkeypatch.setattr(host,'STATE',tmp_path)
    monkeypatch.setattr(host,'GOAL_ID',e['goal_id']);monkeypatch.setattr(host,'GOAL_KEY','boundary-goal')
    immutable(tmp_path/'prepared.json',dict(experiment_id=e['experiment_id'],port=8876))
    immutable(tmp_path/'memory.json',{})
    with s.tx() as db:
        goal=s.get(db,'goals',e['goal_id']);goal['authorized_until']=1;s.put(db,'goals',e['goal_id'],goal)
    def stale_epoch():raise DevConflict('controlled stale source epoch')
    monkeypatch.setattr(host,'verify_epoch',stale_epoch)
    args=Namespace(command=entry,output=str(tmp_path/'host-result.json'))
    if entry=='dispatch':
        with pytest.raises(DevConflict,match='stale source epoch'):host.execute(args)
    else:host.execute(args)
    job=s.cell(c['key'])['jobs'][0]
    assert job['state']=='settled' and s.goal(e['goal_id'])['reserved']['help_wait_seconds']==0
    assert planner.PlannerControl(s).get(r['request_id'])['state'] in {'cancelled','invalidated'}
    assert not any(x['kind'] in {'model','advisor','execute'} for x in s.cell(c['key'])['jobs'])


@pytest.mark.parametrize('content',['{broken', 'null'])
def test_unknown_scheduler_receipt_retains_explicit_review_hold(tmp_path,content):
    s,e,c,cfg,r,j,d=pending(tmp_path)
    (Path(e['artifact_dir'])/'scheduler-process-corrupt.json').write_text(content)
    experiments.cancel(s,e['experiment_id'])
    job=s.cell(c['key'])['jobs'][0]
    assert job['state']=='needs_review' and job['reason'].startswith('human wait reconciliation:')
    assert not job.get('actual') and s.goal(e['goal_id'])['reserved']['help_wait_seconds']==600
