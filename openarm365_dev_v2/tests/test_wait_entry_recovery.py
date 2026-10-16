"""Controlled temporary ledgers, injected dead owners/replies and runner only.

These tests are NOT real human/provider/native/Memory acceptance evidence.
"""
import copy
import json
import os
import time
from pathlib import Path

import pytest
from benchmarks.attention_harness import dev_experiment as experiments, dev_main_bridge as main
from benchmarks.attention_harness import dev_planner as planner, dev_v2_loop as loop
from benchmarks.attention_harness.dev_loop import process_identity, immutable
from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.dev_configuration import normalize_configuration, derive_cell_budget
from benchmarks.attention_harness.dev_ledger import DevV2Store
import hashlib
from openarm365_dev_v2.tests.test_planner_features import add_version, SOURCE, controlled_reply
from openarm365_dev_v2.tests.fakes import Runner

DEAD=dict(pid=999999999,start_ticks='0',boot_id='controlled-dead-owner')


def fixture(tmp_path):
    key='controlled-wait-recovery';gid=hashlib.sha256(('goal|'+key).encode()).hexdigest()[:32]
    budget=derive_cell_budget(normalize_configuration(dict(max_versions=1,target_depth=1,target_valid_executions=1,help_budget=0,planner_features={'human_wait_seconds':600},policies=[planner.POLICY])))
    limits={k:2*v for k,v in budget.items()};store=DevV2Store(tmp_path/'ledger.sqlite');store.ensure_goal(gid,limits,7200)
    memory=dict(base_url='http://127.0.0.1:1',store_id='controlled-authority',namespace='controlled-namespace')
    b=main.DevMainBridge(store.path,tmp_path/'artifacts',key,memory)
    body=dict(start=False,selection=dict(request_key='controlled-recovery',tasks=['pick_place_counter_to_sink','open_cabinet'],seeds=[101],policies=[planner.POLICY],mode='experiment',help_budget=0,human_wait_seconds=600,
        configuration=dict(goal_id=key,experiment_budget=limits,cell_budget=budget,max_iterations=1,wall_seconds=7200)))
    e=b.create(body);c=e['cells'][0]
    root=Path(e['artifact_dir'])/'cells'/c['key'];root.mkdir(parents=True)
    cfg=json.loads(Path(e['manifest']['uri']).read_bytes())['configs'][f"{c['task_id']}:{c['seed']}"]
    immutable(root/'config.json',cfg)
    b.store.update_cell(c['key'],state='running',owner=process_identity(),artifact_dir=str(root))
    record,directory=add_version(b.store,c,root)
    r=planner.PlannerControl(b.store).request(c['key'],1,'approval',planner.binding(c['key'],1,SOURCE,cfg),600)
    # Claim with the controlled scheduler identity from the outset. Editing only
    # the final job owner would contradict its immutable original claim event.
    b.store.update_cell(c['key'],owner=DEAD)
    j=b.store.reserve(c['key'],1,'human',{'help_wait_seconds':600});b.store.claim(j['job_id'],DEAD)
    b.store.update_cell(c['key'],planner_wait=r['request_id'])
    planner.prepare_independent(b.store,c['key'],1,'human',SOURCE,{},directory,r['request_id'],5)
    # Explicit, test-only process-loss injection. No real database is opened.
    with b.store.tx() as db:
        exp=b.store.get(db,'experiments',e['experiment_id']);exp.update(state='running',owner=DEAD);b.store.put(db,'experiments',e['experiment_id'],exp)
        raw=json.loads(db.execute('SELECT payload FROM v2_jobs WHERE id=?',(j['job_id'],)).fetchone()[0]);raw['owner']=DEAD
        db.execute('UPDATE v2_jobs SET payload=? WHERE id=?',(json.dumps(raw),j['job_id']))
    b.store.update_cell(c['key'],owner=DEAD)
    return b,e,b.store.cell(c['key']),cfg,r,directory


def view(b,e):return b.get(f"/api/dev-v2/experiments/{e['experiment_id']}/cells/{e['cells'][0]['key']}/view")[0]


def controlled_dispatch(monkeypatch,b,*,reply=None):
    calls=[];observed=[]
    def no_model(*a,**kw):raise AssertionError('recovery called a model/Advisor')
    monkeypatch.setattr(loop,'provider_call',no_model)
    old_prepare=planner.prepare_independent
    def existing_only(*a,**kw):
        before=planner.PlannerControl(b.store).snapshot(a[1])['work']
        assert len(before)==1
        result=old_prepare(*a,**kw)
        assert result['work_id']==before[0]['work_id'] and result['started_at']==before[0]['started_at']
        observed.append(view(b,b.experiment(b.store.cell(a[1])['experiment_id'])))
        if reply:reply()
        return result
    monkeypatch.setattr(planner,'prepare_independent',existing_only)
    class Worker:
        pid=os.getpid()
        def poll(self):return 0
    original_spawn=main.subprocess.Popen
    def spawn(argv,**kw):
        if '--experiment' not in argv:return original_spawn(argv,**kw)
        calls.append(argv)
        eid=argv[argv.index('--experiment')+1]
        experiments.run(b.store,eid,max_cells=2,max_slots=1,client=object(),runner_factory=Runner)
        return Worker()
    monkeypatch.setattr(main.subprocess,'Popen',spawn)
    return calls,observed


@pytest.mark.parametrize('decision',['waiting','approved','rejected','timed_out'])
def test_main_continue_resumes_exact_wait_without_model_or_preparation_replay(tmp_path,monkeypatch,decision):
    b,e,c,cfg,r,directory=fixture(tmp_path)
    ctl=planner.PlannerControl(b.store)
    if decision in {'approved','rejected'}:controlled_reply(b.store,r,decision=='approved')
    if decision=='timed_out':
        now=time.time();monkeypatch.setattr(planner.time,'time',lambda:now+601)
    before_goal=b.store.goal(b.goal_id);before_job=b.store.cell(c['key'])['jobs'][0]
    before=ctl.get(r['request_id'])
    calls,observed=controlled_dispatch(monkeypatch,b,reply=(lambda:controlled_reply(b.store,r)) if decision=='waiting' else None)
    fresh=main.DevMainBridge(b.store.path,b.artifacts,b.goal_key,b.memory)
    assert view(fresh,e)['lifecycle']['can_start']
    assert not view(fresh,e)['lifecycle']['can_create_new']
    if decision in {'rejected','timed_out'}:
        # A terminal orphan wait now finishes before any scheduler is spawned.
        with pytest.raises(DevConflict):b.post(f"/api/dev-v2/experiments/{e['experiment_id']}/run",{})
        human=next(j for j in b.store.cell(c['key'])['jobs'] if j['kind']=='human')
        assert not calls and human['state']=='settled'
        assert ctl.get(r['request_id'])['state']==decision
        assert not any(j['kind']=='execute' for j in b.store.cell(c['key'])['jobs'])
        if decision=='timed_out':assert human['actual']['help_wait_seconds']==600
        return
    result=b.post(f"/api/dev-v2/experiments/{e['experiment_id']}/run",{})
    assert result['worker_started'] and len(calls)==1
    final=b.store.cell(c['key']);after=ctl.get(r['request_id']);human=next(j for j in final['jobs'] if j['kind']=='human')
    assert after['request_id']==r['request_id'] and after['binding']==r['binding']
    assert after['created_at']==r['created_at'] and after['deadline_epoch']==r['deadline_epoch']
    assert human['claimed_at']==before_job['claimed_at'] and human['reserved_at']==before_job['reserved_at']
    assert human['owner']==process_identity() and len(human['owner_transfers'])==1
    assert human['reservation']==before_job['reservation'] and human['state']=='settled'
    assert b.store.goal(b.goal_id)['real_started_at']==before_goal['real_started_at']
    assert b.store.goal(b.goal_id)['limits']==before_goal['limits']
    assert b.store.goal(b.goal_id).get('authorized_until')==before_goal.get('authorized_until')
    assert len(planner.PlannerControl(b.store).snapshot(c['key'])['work'])==1
    assert len(final['versions'])==1 and not any(j['kind'] in {'model','advisor'} for j in final['jobs'])
    assert observed[0]['lifecycle']['waiting_owner']==process_identity()
    assert observed[0]['lifecycle']['phase']!='review'
    executions=[j for j in final['jobs'] if j['kind']=='execute']
    if decision in {'waiting','approved'}:
        assert len(executions)==1 and executions[0]['state']=='settled'
        assert after['state']=='consumed' and after['consumed_by']==executions[0]['job_id']
        assert controlled_reply(b.store,r)['state']=='consumed'
        with pytest.raises(DevConflict):b.store.claim(executions[0]['job_id'],process_identity(),approval_id=r['request_id'])
    else:
        assert not executions and after['state']==decision
        assert final['reason']=='motion approval '+decision
        if decision=='timed_out':assert human['actual']['help_wait_seconds']==600
        with pytest.raises(DevConflict):controlled_reply(b.store,r,True)
    with pytest.raises(DevConflict):b.start(e['experiment_id'])
    assert len(calls)==1


def test_approved_wait_already_settled_before_crash_is_not_charged_twice(tmp_path,monkeypatch):
    b,e,c,cfg,r,d=fixture(tmp_path);approved=controlled_reply(b.store,r)
    j=c['jobs'][0];b.store.settle(j['job_id'],{'help_wait_seconds':.25},{'planner_request_id':r['request_id'],'controlled':True})
    used=b.store.goal(b.goal_id)['used']['help_wait_seconds']
    calls,observed=controlled_dispatch(monkeypatch,b)
    assert view(b,e)['lifecycle']['can_start'];b.start(e['experiment_id'])
    assert len(calls)==1 and b.store.goal(b.goal_id)['used']['help_wait_seconds']==used
    assert planner.PlannerControl(b.store).get(r['request_id'])['state']=='consumed'


@pytest.mark.parametrize('kind,state',[('model','claimed'),('advisor','claimed'),('execute','claimed'),('model','reserved'),('execute','reserved'),('human','needs_review'),('model','unknown')])
def test_paid_unknown_and_holds_refuse_main_and_acquire_without_refund(tmp_path,kind,state):
    b,e,c,cfg,r,d=fixture(tmp_path)
    with b.store.tx() as db:
        if kind=='human':db.execute("UPDATE v2_jobs SET state='needs_review'")
        else:
            j=dict(job_id='controlled-unknown',cell=c['key'],version=2,kind=kind,owner=DEAD,reservation={'executions':1} if kind=='execute' else {'dev_calls':1},claimed_at=time.time())
            db.execute('INSERT INTO v2_jobs VALUES (?,?,?,?,?,?)',(j['job_id'],c['key'],2,kind,state,json.dumps(j)))
    with b.store.tx() as db:before={t:db.execute('SELECT * FROM v2_'+t).fetchall() for t in ('goals','cells','jobs','experiments')} 
    assert not view(b,e)['lifecycle']['can_start']
    with pytest.raises(DevConflict):b.start(e['experiment_id'])
    with pytest.raises(DevConflict):experiments.acquire(b.store,e['experiment_id'],process_identity())
    with b.store.tx() as db:assert {t:db.execute('SELECT * FROM v2_'+t).fetchall() for t in before}==before


@pytest.mark.parametrize('where',['scheduler','cell','job','receipt','cross_cell','cross_scheduler'])
def test_every_live_owner_refuses_recovery(tmp_path,where):
    b,e,c,cfg,r,d=fixture(tmp_path)
    with b.store.tx() as db:
        if where in {'cross_cell','cross_scheduler'}:
            other=copy.deepcopy(e);other.update(experiment_id='devexp-'+'a'*24,artifact_dir=str(tmp_path/'other'),owner=process_identity() if where=='cross_scheduler' else None)
            other.pop('cells');b.store.put(db,'experiments',other['experiment_id'],other)
            other_c=copy.deepcopy(c);other_c.update(key='b'*64,experiment_id=other['experiment_id'],owner=process_identity() if where=='cross_cell' else None)
            db.execute('INSERT INTO v2_cells VALUES (?,?,?)',(other_c['key'],other['experiment_id'],json.dumps(other_c)))
        elif where=='scheduler':
            row=b.store.get(db,'experiments',e['experiment_id']);row['owner']=process_identity();b.store.put(db,'experiments',e['experiment_id'],row)
        elif where=='cell':
            row=b.store.get(db,'cells',c['key']);row['owner']=process_identity();b.store.put(db,'cells',c['key'],row)
        elif where=='job':
            row=json.loads(db.execute('SELECT payload FROM v2_jobs').fetchone()[0]);row['owner']=process_identity();db.execute('UPDATE v2_jobs SET payload=?',(json.dumps(row),))
        else:immutable(Path(e['artifact_dir'])/'scheduler-process-controlled.json',process_identity())
    assert not view(b,e)['lifecycle']['can_start']
    with pytest.raises(DevConflict):b.start(e['experiment_id'])
    with pytest.raises(DevConflict):experiments.acquire(b.store,e['experiment_id'],process_identity())


@pytest.mark.parametrize('change',['cancel','invalidated','source','config','manifest','deadline','closed_goal','missing_request','binding','native_done','unknown_identity','unknown_receipt','goal_budget_drift','consumed_request'])
def test_stopped_changed_or_unknown_authority_refuses(tmp_path,change):
    b,e,c,cfg,r,d=fixture(tmp_path)
    if change=='cancel':experiments.cancel(b.store,e['experiment_id'])
    elif change=='invalidated':add_version(b.store,c,d.parent,version=2)
    elif change=='source':
        p=d/'policy.py';p.chmod(0o600);p.write_text(SOURCE+'# edited')
    elif change=='config':
        p=d.parent/'config.json';p.chmod(0o600);p.write_text('{}')
    elif change=='manifest':
        p=Path(e['manifest']['uri']);p.chmod(0o600);p.write_text('{}')
    elif change=='unknown_receipt':immutable(Path(e['artifact_dir'])/'scheduler-process-unknown.json',None)
    else:
        with b.store.tx() as db:
            if change in {'deadline','closed_goal','native_done','goal_budget_drift'}:
                g=b.store.get(db,'goals',b.goal_id)
                if change=='deadline':g['authorized_until']=1
                if change=='closed_goal':g['real_finished_at']=time.time()
                if change=='native_done':g['used']['executions']=1
                if change=='goal_budget_drift':g['limits']['provider_tokens']+=1
                b.store.put(db,'goals',b.goal_id,g)
            elif change=='missing_request':db.execute('DELETE FROM v2_planner_requests')
            elif change=='binding':
                bad=copy.deepcopy(r);bad['binding']['action_version']=99;planner.PlannerControl.save(db,bad)
            elif change=='consumed_request':
                bad=copy.deepcopy(r);bad.update(state='consumed',consumed_by='controlled-prior-execute');planner.PlannerControl.save(db,bad)
            elif change=='unknown_identity':
                j=json.loads(db.execute('SELECT payload FROM v2_jobs').fetchone()[0]);j['owner']={'pid':999999999};db.execute('UPDATE v2_jobs SET payload=?',(json.dumps(j),))
    if change=='source':
        with pytest.raises(DevConflict,match='display evidence drift'):view(b,e)
    else:assert not view(b,e)['lifecycle']['can_start']
    with pytest.raises((DevConflict,ValueError)):b.start(e['experiment_id'])
    with pytest.raises((DevConflict,ValueError)):experiments.acquire(b.store,e['experiment_id'],process_identity())
    if change in {'cancel','invalidated'}:
        assert planner.PlannerControl(b.store).get(r['request_id'])['state']==('cancelled' if change=='cancel' else 'invalidated')
        with pytest.raises(DevConflict):controlled_reply(b.store,r)


def test_cli_dispatch_uses_same_source_epoch_and_original_main_entry(tmp_path,monkeypatch):
    from argparse import Namespace
    from benchmarks.attention_harness import dev_integration as host
    b,e,c,cfg,r,d=fixture(tmp_path);controlled_reply(b.store,r)
    monkeypatch.setattr(host,'LEDGER',b.store.path);monkeypatch.setattr(host,'STATE',tmp_path)
    monkeypatch.setattr(host,'GOAL_ID',b.goal_id);monkeypatch.setattr(host,'GOAL_KEY',b.goal_key)
    immutable(tmp_path/'prepared.json',{'experiment_id':e['experiment_id'],'port':8876})
    immutable(tmp_path/'memory.json',b.memory);immutable(tmp_path/'epoch.json',host.epoch())
    calls,observed=controlled_dispatch(monkeypatch,b)
    host.execute(Namespace(command='dispatch',output=str(tmp_path/'dispatch.json')))
    assert len(calls)==1 and json.loads((tmp_path/'dispatch.json').read_bytes())['worker_started']
    assert planner.PlannerControl(b.store).get(r['request_id'])['state']=='consumed'


def test_second_recovery_keeps_original_claim_deadline_and_single_wait_charge(tmp_path,monkeypatch):
    b,e,c,cfg,r,d=fixture(tmp_path);j=c['jobs'][0]
    experiments.acquire(b.store,e['experiment_id'],DEAD)
    with b.store.tx() as db:
        raw=json.loads(db.execute('SELECT payload FROM v2_jobs WHERE id=?',(j['job_id'],)).fetchone()[0]);raw['owner']=DEAD
        db.execute('UPDATE v2_jobs SET payload=? WHERE id=?',(json.dumps(raw),j['job_id']))
        exp=b.store.get(db,'experiments',e['experiment_id']);exp['owner']=DEAD;b.store.put(db,'experiments',e['experiment_id'],exp)
    b.store.update_cell(c['key'],owner=DEAD)
    calls,observed=controlled_dispatch(monkeypatch,b,reply=lambda:controlled_reply(b.store,r))
    b.start(e['experiment_id']);human=next(x for x in b.store.cell(c['key'])['jobs'] if x['kind']=='human')
    assert len(human['owner_transfers'])==2 and human['claimed_at']==j['claimed_at']
    assert human['reserved_at']==j['reserved_at'] and human['reservation']==j['reservation']
    assert planner.PlannerControl(b.store).get(r['request_id'])['deadline_epoch']==r['deadline_epoch']
    assert b.store.goal(b.goal_id)['used']['help_wait_seconds']==human['actual']['help_wait_seconds']


def test_model_quota_exhaustion_does_not_require_a_new_model_for_approved_action(tmp_path,monkeypatch):
    b,e,c,cfg,r,d=fixture(tmp_path);controlled_reply(b.store,r)
    # Controlled resource boundary only; no paid usage is represented as real.
    with b.store.tx() as db:
        g=b.store.get(db,'goals',b.goal_id);g['used']['dev_calls']=g['limits']['dev_calls'];b.store.put(db,'goals',b.goal_id,g)
    calls,observed=controlled_dispatch(monkeypatch,b);b.start(e['experiment_id'])
    assert len(calls)==1 and planner.PlannerControl(b.store).get(r['request_id'])['state']=='consumed'


def test_receipt_written_before_wait_settlement_expires_without_overwrite_or_motion(tmp_path,monkeypatch):
    b,e,c,cfg,r,d=fixture(tmp_path);approved=controlled_reply(b.store,r)
    canonical=immutable(d/'human-decision-receipt.json',approved)
    original=Path(canonical['uri']).read_bytes();now=time.time();monkeypatch.setattr(planner.time,'time',lambda:now+601)
    calls,observed=controlled_dispatch(monkeypatch,b)
    with pytest.raises(DevConflict):b.start(e['experiment_id'])
    final=b.store.cell(c['key']);human=next(j for j in final['jobs'] if j['kind']=='human')
    assert not calls
    assert human['state']=='settled' and human['receipt']['state']=='timed_out'
    assert Path(canonical['uri']).read_bytes()==original
    assert human['receipt']['request']['uri']!=canonical['uri']
    assert not any(j['kind']=='execute' for j in final['jobs'])
    assert human['actual']['help_wait_seconds']==approved['responded_at']-r['created_at']


def test_settled_approved_handoff_crash_before_cell_entry_can_recover_again(tmp_path,monkeypatch):
    b,e,c,cfg,r,d=fixture(tmp_path);controlled_reply(b.store,r)
    j=c['jobs'][0];b.store.settle(j['job_id'],{'help_wait_seconds':.25},{'planner_request_id':r['request_id']})
    experiments.acquire(b.store,e['experiment_id'],process_identity())
    assert b.store.cell(c['key'])['state']=='planned'
    with b.store.tx() as db:
        row=json.loads(db.execute('SELECT payload FROM v2_jobs').fetchone()[0]);row['owner']=DEAD;db.execute('UPDATE v2_jobs SET payload=?',(json.dumps(row),))
        exp=b.store.get(db,'experiments',e['experiment_id']);exp['owner']=DEAD;b.store.put(db,'experiments',e['experiment_id'],exp)
    b.store.update_cell(c['key'],owner=DEAD)
    assert view(b,e)['lifecycle']['can_start']
    calls,observed=controlled_dispatch(monkeypatch,b);b.start(e['experiment_id'])
    human=next(j for j in b.store.cell(c['key'])['jobs'] if j['kind']=='human')
    assert len(human['owner_transfers'])==2 and human['actual']['help_wait_seconds']==.25
    assert planner.PlannerControl(b.store).get(r['request_id'])['state']=='consumed'


def test_acquisition_is_atomic_and_does_not_reopen_terminal_sibling(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    b,e,c,cfg,r,d=fixture(tmp_path)
    sibling=e['cells'][1];b.store.update_cell(sibling['key'],state='failed',owner=DEAD,reason='controlled_prior_terminal')
    def acquire_once():
        try:experiments.acquire(b.store,e['experiment_id'],process_identity());return 'acquired'
        except DevConflict:return 'refused'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:acquire_once(),range(2)))
    assert sorted(results)==['acquired','refused']
    assert b.store.cell(sibling['key'])['state']=='failed'
    assert len(b.store.cell(c['key'])['jobs'][0]['owner_transfers'])==1
