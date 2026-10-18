"""New isolated SQLite ledger; goal and cell reservations share one transaction."""
import json,sqlite3,time,math
from contextlib import contextmanager
from pathlib import Path
from .core.dev_store import DevConflict,DevBudgetExhausted
GOAL='3a82df131d394774bc1ae9d881895133'
RESOURCES=('dev_calls','advisor_calls','eval_calls','provider_tokens','input_tokens','output_tokens','executions','sdk_calls','control_steps','help_credits','help_count','help_wait_seconds','model_seconds','execution_seconds','hint_human_seconds')
# Resources introduced after the first ledgers: a budget/limit dict may omit them (read as 0).
OPTIONAL=('eval_calls','hint_human_seconds')
HUMAN_SECONDS=('hint_human_seconds',)
LIMITS=dict(dev_calls=28,advisor_calls=14,eval_calls=28,provider_tokens=500000,input_tokens=500000,output_tokens=500000,executions=28,sdk_calls=2240,control_steps=67200,help_credits=28,help_count=28,help_wait_seconds=7200,model_seconds=7200,execution_seconds=7200,hint_human_seconds=7200)
def fill_optional(b):
    """Read a budget/limit dict written before the optional resources existed: absent ones are 0."""
    return {**{k:0 for k in OPTIONAL if k not in b},**{k:v for k,v in b.items() if k!='review_human_seconds'}}
def complete_resources(b):
    """True when b names every required resource (optional ones may be absent) and nothing unknown."""
    return set(RESOURCES)-set(OPTIONAL)<=set(b)<=set(RESOURCES)|{'review_human_seconds'}
def attention_cost(used,*,request_weight=1.0,second_weight=1/60):
    """Advisor hint request units and logical human seconds; Eval is compute only."""
    seconds=used.get('hint_human_seconds',0)
    return request_weight*used.get('advisor_calls',0)+second_weight*seconds
def encoded(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,allow_nan=False)
def cost(x):
    if not isinstance(x,dict) or any(k not in RESOURCES and k!='review_human_seconds' or type(v) not in (int,float) or not math.isfinite(v) or v<0 for k,v in x.items()):raise ValueError('finite nonnegative resource cost required')
    if any(type(v) is not int for k,v in x.items() if k not in {'help_credits','help_wait_seconds','model_seconds','execution_seconds','review_human_seconds','hint_human_seconds'}):raise ValueError('integer resource count required')
    return x
def resource_equal(left,right):
    # Receipt order can differ from chronological settlement; compare floating
    # seconds at numerical precision while all count/token totals stay exact.
    seconds={'model_seconds','execution_seconds','help_wait_seconds','help_credits','review_human_seconds','hint_human_seconds'}
    return all(math.isclose(left.get(k,0),right.get(k,0),rel_tol=0,abs_tol=1e-9) if k in seconds else left.get(k,0)==right.get(k,0) for k in RESOURCES)
def experiment_goal(experiment):
    legacy=(experiment.get('selection',{}).get('configuration') or {}).get('goal_id')
    explicit=experiment.get('goal_id')
    if explicit and legacy and explicit!=legacy:raise DevConflict('experiment goal identity conflict')
    return explicit or legacy or GOAL


class DevV2Store:
    def __init__(self,path,configuration=None):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.goal_id=GOAL
        with self.tx() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS v2_goals(id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE IF NOT EXISTS v2_experiments(id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE IF NOT EXISTS v2_cells(id TEXT PRIMARY KEY,exp TEXT,payload TEXT); CREATE TABLE IF NOT EXISTS v2_jobs(id TEXT PRIMARY KEY,cell TEXT,version INTEGER,kind TEXT,state TEXT,payload TEXT,UNIQUE(cell,version,kind)); CREATE TABLE IF NOT EXISTS v2_versions(cell TEXT,version INTEGER,payload TEXT,PRIMARY KEY(cell,version)); CREATE TABLE IF NOT EXISTS v2_events(seq INTEGER PRIMARY KEY,at REAL,kind TEXT,payload TEXT);')

        if configuration is not None:
            import re
            if (not isinstance(configuration,dict) or set(configuration)!={'goal_id','limits','wall_seconds'}
                    or not re.fullmatch(r'[A-Za-z0-9_.-]{8,100}',configuration['goal_id'])
                    or configuration['goal_id']==GOAL):raise ValueError('independent new goal configuration required')
            self.goal_id=configuration['goal_id']
            self.ensure_goal(self.goal_id,configuration['limits'],configuration['wall_seconds'])
        else:
            with self.tx() as db:
                legacy={json.loads(p)['selection']['configuration']['goal_id']
                    for p, in db.execute('SELECT payload FROM v2_experiments')
                    if isinstance(json.loads(p).get('selection',{}).get('configuration'),dict)}
                if len(legacy)==1:self.goal_id=next(iter(legacy))

    @contextmanager
    def tx(self):
        db=sqlite3.connect(self.path,timeout=30,isolation_level=None)
        try:
            db.execute('PRAGMA busy_timeout=30000');db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()
    @staticmethod
    def get(db,table,key):
        r=db.execute('SELECT payload FROM v2_'+table+' WHERE id=?',(key,)).fetchone()
        if not r and table=='goals' and key==GOAL:
            value=dict(limits=LIMITS,used={k:0 for k in RESOURCES},real_started_at=None,wall_limit=7200,opening=[])
            db.execute('INSERT INTO v2_goals VALUES (?,?)',(GOAL,encoded(value)))
            return value
        if not r:raise DevConflict('unknown '+table+' record')
        return json.loads(r[0])
    @staticmethod
    def put(db,table,key,value):
        db.execute('INSERT INTO v2_'+table+'(id,payload) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(key,encoded(value)))
    @staticmethod
    def event(db,kind,value):db.execute('INSERT INTO v2_events(at,kind,payload) VALUES (?,?,?)',(time.time(),kind,encoded(value)))
    def ensure_goal(self,goal_id,limits,wall_limit):
        cost(limits)
        if not isinstance(goal_id,str) or not goal_id or not complete_resources(limits) or type(wall_limit) not in (int,float) or not math.isfinite(wall_limit) or wall_limit<=0:raise ValueError('complete goal budget required')
        with self.tx() as db:
            row=db.execute('SELECT payload FROM v2_goals WHERE id=?',(goal_id,)).fetchone()
            if row:
                old=json.loads(row[0])
                if fill_optional(old['limits'])!=fill_optional(limits) or old['wall_limit']!=wall_limit:raise DevConflict('immutable goal budget conflict')
            else:self.put(db,'goals',goal_id,dict(limits=limits,used={k:0 for k in RESOURCES},real_started_at=None,wall_limit=wall_limit,opening=[]))
    @staticmethod
    def cell_goal(db,c):return experiment_goal(DevV2Store.get(db,'experiments',c['experiment_id']))
    def start_cell_clock(self,key):
        """Begin actual feature work once, before even a Memory authority query."""
        with self.tx() as db:
            c=self.get(db,'cells',key);e=self.get(db,'experiments',c['experiment_id'])
            gid=self.cell_goal(db,c);g=self._goal(db,gid)
            if c['state']!='running' or e.get('cancelled'):raise DevConflict('feature work cannot start after stop')
            if g.get('real_finished_at') or g['wall_remaining']<=0:raise DevBudgetExhausted('goal wall budget expired before feature work')
            if g['real_started_at'] is None:
                raw=self.get(db,'goals',gid);raw['real_started_at']=time.time();self.put(db,'goals',gid,raw)
                self.event(db,'planner_clock_started',dict(cell=key,goal_id=gid,real_started_at=raw['real_started_at']))
            return self._goal(db,gid)
    def goal_for_cell(self,key):
        with self.tx() as db:return self._goal(db,self.cell_goal(db,self.get(db,'cells',key)))
    def goal(self,goal_id=None):
        with self.tx() as db:return self._goal(db,goal_id or self.goal_id)
    def goal_jobs(self,db,goal_id):
        rows=db.execute("SELECT j.state,j.payload,e.payload FROM v2_jobs j JOIN v2_cells c ON j.cell=c.id JOIN v2_experiments e ON COALESCE(c.exp,json_extract(c.payload,'$.experiment_id'))=e.id ORDER BY j.rowid")
        return [(state,json.loads(p)) for state,p,e in rows if experiment_goal(json.loads(e))==goal_id]
    def _goal(self,db,goal_id=GOAL):
        g=self.get(db,'goals',goal_id);held={k:0 for k in RESOURCES}
        for state,j in self.goal_jobs(db,goal_id):
            if state not in {'reserved','claimed','needs_review'}:continue
            p=encoded(j)
            for k,v in json.loads(p)['reservation'].items():held[k]+=v
        g['reserved']=held;g['remaining']={k:max(0,g['limits'].get(k,0)-g['used'].get(k,0)-held[k]) for k in RESOURCES}
        g['wall_used']=max(0,(g.get('real_finished_at') or time.time())-g['real_started_at']) if g['real_started_at'] else 0
        g['wall_remaining']=max(0,g['wall_limit']-g['wall_used'])
        if g.get('authorized_until') is not None:g['wall_remaining']=min(g['wall_remaining'],max(0,g['authorized_until']-time.time()))
        return g
    def opening(self,ref):
        from .dev_loop import sha
        if sha(ref['uri'])!=ref['sha256']:raise DevConflict('opening receipt digest mismatch')
        r=json.loads(Path(ref['uri']).read_bytes())
        with self.tx() as db:
            g=self.get(db,'goals',GOAL)
            if any(x==ref for x in g['opening']):return
            if any(x['uri']==ref['uri'] for x in g['opening']):raise DevConflict('opening receipt conflict')
            amount=cost({'executions':r['executions'],'sdk_calls':r.get('sdk_calls') or 0,'control_steps':r.get('control_steps') or 0,'execution_seconds':r['execution_seconds']})
            if r.get('reservation_retained'):raise DevConflict('unresolved opening requires review, no automatic refund')
            if any(g['used'][k]+v>g['limits'][k] for k,v in amount.items()):raise DevBudgetExhausted('opening exceeds original budget')
            for k,v in amount.items():g['used'][k]=g['used'].get(k,0)+v
            g['real_started_at']=min(g['real_started_at'] or r['real_started_at'],r['real_started_at']);g['opening'].append(ref)
            self.put(db,'goals',GOAL,g);self.event(db,'opening',{'ref':ref,'actual':amount})
    def reserve(self,cell,version,kind,reservation):
        cost(reservation)
        if kind not in {'model','advisor','demo','human','cache','execute','eval'}:raise ValueError('unsupported resource job')
        with self.tx() as db:
            c=self.get(db,'cells',cell)
            row=db.execute('SELECT payload FROM v2_jobs WHERE cell=? AND version=? AND kind=?',(cell,version,kind)).fetchone()
            if row:
                j=json.loads(row[0])
                if j['reservation']!=reservation:raise DevConflict('reservation id reused')
                return j
            exp=self.get(db,'experiments',c['experiment_id'])
            if c['state']!='running' or exp.get('cancelled'):raise DevConflict('cell cannot dispatch')
            goal_id=self.cell_goal(db,c);g=self._goal(db,goal_id)
            if g.get('real_finished_at'):raise DevBudgetExhausted('validation goal closed; original expenditure cannot be reset')
            if g['wall_remaining']<=0:raise DevBudgetExhausted('original goal wall budget expired')
            held={k:0 for k in RESOURCES}
            for p, in db.execute("SELECT payload FROM v2_jobs WHERE cell=? AND state IN ('reserved','claimed','needs_review')",(cell,)):
                for k,v in json.loads(p)['reservation'].items():held[k]+=v
            if any(v>g['remaining'][k] or c['used'].get(k,0)+held[k]+v>c['budget'].get(k,0) for k,v in reservation.items()):raise DevBudgetExhausted('goal/cell budget exhausted before dispatch')
            jid=f'{cell}:{version}:{kind}';j={'job_id':jid,'cell':cell,'version':version,'kind':kind,'goal_id':goal_id,'reservation':reservation,'reserved_at':time.time()}
            db.execute('INSERT INTO v2_jobs VALUES (?,?,?,?,?,?)',(jid,cell,version,kind,'reserved',encoded(j)))
            self.event(db,'reserved',j);return j
    def claim(self,jid,owner,*,approval_id=None):
        with self.tx() as db:
            row=db.execute('SELECT state,payload FROM v2_jobs WHERE id=?',(jid,)).fetchone()
            if not row:raise DevConflict('unknown job')
            state,p=row;j=json.loads(p);c=self.get(db,'cells',j['cell']);exp=self.get(db,'experiments',c['experiment_id'])
            if state!='reserved' or c.get('owner')!=owner or c['state']!='running' or exp.get('cancelled'):raise DevConflict('claim already dispatched/stopped/wrong owner')
            goal_id=self.cell_goal(db,c);g=self._goal(db,goal_id)
            if g.get('real_finished_at'):raise DevBudgetExhausted('validation goal closed before claim')
            if g['wall_remaining']<=0:raise DevBudgetExhausted('goal wall expired before claim')
            if not g['real_started_at']:
                raw=self.get(db,'goals',goal_id);raw['real_started_at']=time.time();self.put(db,'goals',goal_id,raw)
            if j['kind']=='execute' and c.get('planner_features'):
                from .dev_planner import PlannerControl,binding
                from .dev_loop import sha
                version=json.loads(db.execute('SELECT payload FROM v2_versions WHERE cell=? AND version=?',(j['cell'],j['version'])).fetchone()[0])
                if sha(version['source']['uri'])!=version['source']['sha256']:raise DevConflict('approved source changed')
                scope=binding(j['cell'],j['version'],Path(version['source']['uri']).read_text(),json.loads((Path(c['artifact_dir'])/'config.json').read_bytes()))
                if scope:
                    if not approval_id:raise DevConflict('motion execution requires human approval')
                    found=db.execute('SELECT payload FROM v2_planner_requests WHERE id=?',(approval_id,)).fetchone()
                    if not found:raise DevConflict('unknown approval')
                    row=PlannerControl.__new__(PlannerControl);row.store=self
                    approval=row.refresh(db,json.loads(found[0]))
                    if approval['state']!='approved' or encoded(approval['binding'])!=encoded(scope):raise DevConflict('approval expired/changed/consumed')
                    approval.update(state='consumed',consumed_by=jid,consumed_at=time.time())
                    row.save(db,approval);self.event(db,'planner_approval_consumed',approval)
            j.update(owner=owner,claimed_at=time.time());db.execute("UPDATE v2_jobs SET state='claimed',payload=? WHERE id=?",(encoded(j),jid));self.event(db,'claimed',j)
    def settle(self,jid,actual,receipt):
        cost(actual)
        with self.tx() as db:
            self.settle_in_transaction(db,jid,actual,receipt)
    def settle_in_transaction(self,db,jid,actual,receipt):
        """Also used by orphan-wait reconciliation under its owner lock."""
        cost(actual)
        state,p=db.execute('SELECT state,payload FROM v2_jobs WHERE id=?',(jid,)).fetchone();j=json.loads(p)
        if state=='settled':
            if j['actual']!=actual or j['receipt']!=receipt:raise DevConflict('conflicting settlement')
            return
        if j['kind']=='execute' and isinstance(receipt,dict) and receipt.get('diagnostic'):
            if not receipt.get('diagnostic_budget_override'):raise DevConflict('diagnostic execution requires budget evidence')
            c=self.get(db,'cells',j['cell']);exp=self.get(db,'experiments',c['experiment_id'])
            if exp['selection']['mode']!='diagnostic':raise DevConflict('diagnostic execution cannot enter normal Dev ledger')
        if state not in {'claimed','needs_review'} or set(actual)!=set(j['reservation']) or any(actual[k]>v for k,v in j['reservation'].items()):raise DevConflict('missing/excess usage; retain reservation')
        c=self.get(db,'cells',j['cell']);goal_id=self.cell_goal(db,c);g=self.get(db,'goals',goal_id)
        for k,v in actual.items():c['used'][k]=c['used'].get(k,0)+v;g['used'][k]=g['used'].get(k,0)+v
        j.update(actual=actual,receipt=receipt,settled_at=time.time())
        db.execute("UPDATE v2_jobs SET state='settled',payload=? WHERE id=?",(encoded(j),jid));self.put(db,'cells',j['cell'],c);self.put(db,'goals',goal_id,g);self.event(db,'settled',j)
    def recover_evals(self,db,goal_id=None):
        # An interrupted optional opinion retains its full reserved cost, but
        # never creates the quarantine used for unknown native/model dispatch.
        from .dev_loop import owner_alive
        from .v2_evaluator import empty_review,write_review
        for state,payload in db.execute("SELECT state,payload FROM v2_jobs WHERE kind='eval' AND state IN ('reserved','claimed')").fetchall():
            j=json.loads(payload)
            if goal_id is not None and self.cell_goal(db,self.get(db,'cells',j['cell']))!=goal_id:continue
            if j.get('owner') and owner_alive(j['owner']):continue
            if state=='reserved':
                db.execute("UPDATE v2_jobs SET state='released' WHERE id=?",(j['job_id'],));continue
            if j.get('owner') and owner_alive(j['owner']):continue
            if 'help_count' not in j['reservation']:
                # New automatic Eval is a mandatory completion signal. A lost
                # claimed call cannot be fabricated or silently skipped.
                j['reason']='automatic Eval interrupted; cost retained for review'
                db.execute("UPDATE v2_jobs SET state='needs_review',payload=? WHERE id=?",(encoded(j),j['job_id']))
                c=self.get(db,'cells',j['cell']);c.update(state='needs_review',reason=j['reason']);self.put(db,'cells',j['cell'],c)
                continue
            c=self.get(db,'cells',j['cell']);directory=Path(c['artifact_dir'])/f"v{j['version']}"
            try:
                if not (directory/'evaluator-review.json').exists():write_review(directory,empty_review({},'failed','Review worker interrupted; reserved usage retained'))
            except OSError:pass
            self.settle_in_transaction(db,j['job_id'],j['reservation'],{'review_error':'worker_interrupted','usage_accounting':'reservation_retained'})
    def unknown(self,jid,reason,receipt=None):
        with self.tx() as db:
            state,p=db.execute('SELECT state,payload FROM v2_jobs WHERE id=?',(jid,)).fetchone();j=json.loads(p)
            if state not in {'claimed','needs_review'}:raise DevConflict('unknown requires claimed job')
            if j.get('review_evidence') and receipt and j['review_evidence']!=receipt:raise DevConflict('review evidence immutable')
            j.update(reason=reason,review_evidence=receipt or j.get('review_evidence'))
            db.execute("UPDATE v2_jobs SET state='needs_review',payload=? WHERE id=?",(encoded(j),jid));c=self.get(db,'cells',j['cell']);c.update(state='needs_review',reason=reason);self.put(db,'cells',j['cell'],c);self.event(db,'unknown',j)
    def release_unclaimed(self,jid):
        with self.tx() as db:
            n=db.execute("UPDATE v2_jobs SET state='released' WHERE id=? AND state='reserved'",(jid,)).rowcount
            if not n:raise DevConflict('claimed reservations cannot be refunded')
    def cell(self,key):
        with self.tx() as db:
            c=self.get(db,'cells',key)
            c['jobs']=[{**json.loads(p),'state':s} for s,p in db.execute('SELECT state,payload FROM v2_jobs WHERE cell=? ORDER BY version,kind',(key,))]
            c['versions']=[json.loads(p) for p, in db.execute('SELECT payload FROM v2_versions WHERE cell=? ORDER BY version',(key,))]
            return c
    def update_cell(self,key,**changes):
        with self.tx() as db:
            c=self.get(db,'cells',key);c.update(changes,updated_at=time.time());self.put(db,'cells',key,c);self.event(db,'cell_updated',{'cell':key,**changes})
    def version(self,key,record):
        if record.get('formal_eligible') is not False:raise ValueError('Dev never formal eligible')
        with self.tx() as db:
            row=db.execute('SELECT payload FROM v2_versions WHERE cell=? AND version=?',(key,record['version'])).fetchone()
            if row:
                if row[0]!=encoded(record):raise DevConflict('immutable version conflict')
                return
            n=db.execute('SELECT COALESCE(MAX(version),0) FROM v2_versions WHERE cell=?',(key,)).fetchone()[0]
            if record['version']!=n+1:raise DevConflict('versions must be consecutive')
            db.execute('INSERT INTO v2_versions VALUES (?,?,?)',(key,record['version'],encoded(record)))
    def reserve_check(self,key,reservation):
        cost(reservation)
        with self.tx() as db:
            c=self.get(db,'cells',key);g=self._goal(db,self.cell_goal(db,c))
            held={k:0 for k in RESOURCES}
            for state,p in db.execute("SELECT state,payload FROM v2_jobs WHERE cell=?",(key,)):
                if state in {'reserved','claimed','needs_review'}:
                    for k,v in json.loads(p)['reservation'].items():held[k]+=v
            if g.get('real_finished_at') or g['wall_remaining']<=0 or any(v>g['remaining'][k] or c['used'].get(k,0)+held[k]+v>c['budget'].get(k,0) for k,v in reservation.items()):raise DevBudgetExhausted('goal/cell budget exhausted before dispatch')
    def export_ledger(self,goal_id=None):
        with self.tx() as db:
            ids=[r[0] for r in db.execute('SELECT id FROM v2_goals ORDER BY rowid')]
            groups={}
            for gid in ids:
                jobs=[{**j,'state':state} for state,j in self.goal_jobs(db,gid)]
                g=self._goal(db,gid);recomputed={k:0 for k in RESOURCES}
                for event, in db.execute("SELECT payload FROM v2_events WHERE kind='opening'"):
                    ev=json.loads(event)
                    if ev.get('goal_id',GOAL)==gid:
                        for k,v in ev['actual'].items():recomputed[k]+=v
                for j in jobs:
                    if j['state']=='settled':
                        for k,v in j['actual'].items():recomputed[k]+=v
                groups[gid]={'goal_id':gid,'goal':g,'jobs':jobs,'recomputed_used':recomputed,'recompute_matches':resource_equal(recomputed,g['used']),'formal_eligible':False}
                keys={key for key,p in db.execute('SELECT id,payload FROM v2_cells') if self.cell_goal(db,json.loads(p))==gid}
                query_events=[(kind,json.loads(p)) for kind,p in db.execute("SELECT kind,payload FROM v2_events WHERE kind IN ('planner_memory_query_started','planner_memory_query_finished') ORDER BY seq") if json.loads(p).get('cell') in keys]
                queries=[]
                for kind,q in query_events:
                    if kind!='planner_memory_query_started':continue
                    finished=next((v for k,v in query_events if k=='planner_memory_query_finished' and (v['cell'],v['version'],v['started_at'])==(q['cell'],q['version'],q['started_at'])),None)
                    if finished:queries.append(finished)
                    else:
                        from .dev_loop import owner_alive
                        live=bool(q.get('owner') and owner_alive(q['owner']))
                        end=min(time.time(),g['real_started_at']+g['wall_limit'])
                        queries.append(dict(q,status='running' if live else 'interrupted_or_unknown',elapsed_seconds=max(0,end-q['started_at']),actual_seconds=None))
                groups[gid]['memory_queries']=queries
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_planner_work'").fetchone():
                    keys={j['cell'] for j in jobs}
                    keys.update(key for key,p in db.execute('SELECT id,payload FROM v2_cells') if self.cell_goal(db,json.loads(p))==gid)
                    work=[json.loads(p) for key,p in db.execute('SELECT cell,payload FROM v2_planner_work') if key in keys]
                    requests=[json.loads(p) for key,p in db.execute('SELECT cell,payload FROM v2_planner_requests') if key in keys]
                    groups[gid]['planner_control']={'work':work,'requests':requests,
                        'background_actual_seconds':sum(w.get('actual_seconds',0) for w in work),
                        'background_running_seconds_reserved':sum(max(0,w['deadline_epoch']-time.time()) for w in work if w['state']=='running'),
                        'accounting':'local preparation wall overlaps waits; no provider tokens or SDK costs; same goal clock'}
            gid=goal_id or (GOAL if GOAL in groups else ids[0] if ids else None)
            if goal_id and gid not in groups:raise DevConflict('unknown goal')
            selected=groups.get(gid,{'goal_id':None,'goal':None,'jobs':[],'recomputed_used':{},'recompute_matches':True,'formal_eligible':False})
            return {**selected,'goals':groups}
