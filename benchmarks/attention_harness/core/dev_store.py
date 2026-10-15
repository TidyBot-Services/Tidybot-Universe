"""Separate Dev ledger. Claims and resource reservations commit before external work.

Unknown claims are never retried or refunded automatically. No formal approvals are
created here. Versions and settlement receipts are insert-only/idempotent.
"""
import json
import math
import time
from uuid import uuid4
from functools import wraps


class DevConflict(RuntimeError):
    pass


class DevBudgetExhausted(DevConflict):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _dev_ledger(method):
    @wraps(method)
    def call(self,*args,**kwargs):
        # Formal memory/snapshot readers must not acquire new Dev schema merely
        # by constructing AttentionStore. Only explicit Dev access initializes it.
        if not getattr(self,'_dev_initialized',False):self._initialize_dev()
        return method(self,*args,**kwargs)
    return call


class DevStoreMixin:
    def _initialize_dev(self):
        with self._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS dev_sessions (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS dev_versions (session_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(session_id,version))')
            db.execute('CREATE TABLE IF NOT EXISTS dev_jobs (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, version INTEGER NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(session_id,version,kind))')
            db.execute('CREATE TABLE IF NOT EXISTS dev_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, event TEXT NOT NULL, payload TEXT NOT NULL)')
        self._dev_initialized=True

    @staticmethod
    def _dev_get(db, sid):
        row = db.execute('SELECT payload FROM dev_sessions WHERE id=?', (sid,)).fetchone()
        if not row:
            raise DevConflict('unknown Dev session')
        return json.loads(row[0])

    @staticmethod
    def _dev_save(db, s, event):
        s['updated_at'] = time.time()
        db.execute('UPDATE dev_sessions SET payload=? WHERE id=?', (encode(s), s['session_id']))
        db.execute('INSERT INTO dev_events(session_id,event,payload) VALUES (?,?,?)', (s['session_id'], event, encode(s)))

    @_dev_ledger
    def create_dev_session(self, *, locked, budget, artifact_dir, model, initial_source, request_key):
        expected = {'executions', 'model_calls', 'tokens', 'sdk_calls', 'execution_seconds', 'wall_seconds'}
        if set(budget) != expected or any(type(v) not in (int,float) or not math.isfinite(v) or v < 0 for v in budget.values()):
            raise ValueError('finite nonnegative unified Dev budget required')
        if any(type(budget[k]) is not int for k in ('executions','model_calls','tokens','sdk_calls')) or budget['wall_seconds'] <= 0:
            raise ValueError('invalid Dev count/deadline budget')
        with self._transaction() as db:
            for row in db.execute('SELECT payload FROM dev_sessions'):
                old = json.loads(row[0])
                if old['request_key'] == request_key:
                    if (old['locked'],old['budget'],old['model'],old['initial_source']) != (locked,budget,model,initial_source):
                        raise DevConflict('idempotency key reused for different Dev conditions')
                    return old
            now = time.time()
            from ..dev_attention import initial_attention
            sid = 'dev-' + uuid4().hex[:24]
            s = dict(session_id=sid, channel='dev_loop', formal_eligible=False, state='ready',
                     locked=locked, budget=budget, used={k:0 for k in budget}, model=model,
                     initial_source=initial_source, artifact_dir=str(artifact_dir)+'/'+sid,
                     request_key=request_key, created_at=now, updated_at=now,
                     deadline_at=now+budget['wall_seconds'], stop_requested=False, owner=None,
                     attention=initial_attention(now))
            db.execute('INSERT INTO dev_sessions VALUES (?,?)',(sid,encode(s)))
            self._dev_save(db,s,'created')
            return s

    @_dev_ledger
    def get_dev_session(self, sid):
        with self._connect() as db:
            s=self._dev_get(db,sid)
            s['versions']=[json.loads(r[0]) for r in db.execute('SELECT payload FROM dev_versions WHERE session_id=? ORDER BY version',(sid,))]
            s['jobs']=[{**json.loads(r[1]),'state':r[0]} for r in db.execute('SELECT state,payload FROM dev_jobs WHERE session_id=? ORDER BY version,kind',(sid,))]
            held={k:0 for k in s['budget']}
            for j in s['jobs']:
                if j['state'] in ('reserved','claimed','needs_review'):
                    for k,v in j['reservation'].items():held[k]+=v
            s['reserved']=held
            s['remaining']={k:max(0,v-s['used'][k]-held[k]) for k,v in s['budget'].items()}
            elapsed_until=time.time() if s['owner'] or s['state']=='ready' else s['updated_at']
            s['used']['wall_seconds']=max(0,elapsed_until-s['created_at'])
            s['remaining']['wall_seconds']=max(0,s['deadline_at']-time.time())
            from ..dev_attention import project_attention
            s['attention_control']=project_attention(s)
            return s

    @_dev_ledger
    def list_dev_sessions(self):
        with self._connect() as db:ids=[r[0] for r in db.execute('SELECT id FROM dev_sessions ORDER BY rowid DESC')]
        return [self.get_dev_session(sid) for sid in ids]

    @_dev_ledger
    def acquire_dev_owner(self,sid,owner):
        with self._transaction() as db:
            s=self._dev_get(db,sid)
            if s['owner'] is not None or s['state'] not in ('ready','running') or s['stop_requested']:
                raise DevConflict('Dev session already owned, stopped or awaiting review')
            s.update(owner=owner,state='running');self._dev_save(db,s,'owner_acquired')

    @_dev_ledger
    def release_dev_owner(self,sid,owner):
        with self._transaction() as db:
            s=self._dev_get(db,sid)
            if s['owner']==owner:
                s['owner']=None;self._dev_save(db,s,'owner_released')

    @_dev_ledger
    def dev_state(self,sid,state,reason=None):
        if state not in ('ready','running','stopping','stopped','budget_exhausted','needs_review','failed'):
            raise ValueError('invalid Dev state')
        with self._transaction() as db:
            s=self._dev_get(db,sid)
            if s['stop_requested'] and state not in ('stopped','needs_review','stopping'):return
            s.update(state=state,reason=reason);self._dev_save(db,s,'state_changed')

    @_dev_ledger
    def stop_dev_session(self,sid):
        with self._transaction() as db:
            s=self._dev_get(db,sid)
            unknown=db.execute("SELECT 1 FROM dev_jobs WHERE session_id=? AND state IN ('claimed','needs_review')",(sid,)).fetchone()
            s.update(stop_requested=True,state='stopping' if s['owner'] else 'needs_review' if unknown else 'stopped')
            self._dev_save(db,s,'stop_requested')
        return self.get_dev_session(sid)

    @_dev_ledger
    def resume_dev_session(self,sid):
        with self._transaction() as db:
            s=self._dev_get(db,sid)
            if s['owner'] or db.execute("SELECT 1 FROM dev_jobs WHERE session_id=? AND state IN ('claimed','needs_review')",(sid,)).fetchone():
                raise DevConflict('unresolved or active work: review evidence before resuming')
            if time.time()>=s['deadline_at']:raise DevBudgetExhausted('session wall budget expired')
            s.update(stop_requested=False,state='ready');self._dev_save(db,s,'resumed')

    @_dev_ledger
    def reserve_dev_job(self,sid,version,kind,reservation):
        if kind not in ('model','execute'):raise ValueError('invalid job kind')
        with self._transaction() as db:
            s=self._dev_get(db,sid)
            row=db.execute('SELECT payload FROM dev_jobs WHERE session_id=? AND version=? AND kind=?',(sid,version,kind)).fetchone()
            if row:return json.loads(row[0])
            if s['stop_requested'] or s['state']!='running':raise DevConflict('session cannot dispatch')
            if time.time()>=s['deadline_at']:raise DevBudgetExhausted('session wall budget expired')
            if not reservation or any(k not in s['budget'] or type(v) not in (int,float) or not math.isfinite(v) or v<0 for k,v in reservation.items()):
                raise ValueError('invalid reservation')
            held={k:0 for k in s['budget']}
            for row in db.execute("SELECT payload FROM dev_jobs WHERE session_id=? AND state IN ('reserved','claimed','needs_review')",(sid,)):
                for k,v in json.loads(row[0])['reservation'].items():held[k]+=v
            if any(s['used'][k]+held[k]+v>s['budget'][k] for k,v in reservation.items()):raise DevBudgetExhausted('unified Dev budget exhausted')
            job=dict(job_id=uuid4().hex,session_id=sid,version=version,kind=kind,reservation=reservation)
            db.execute('INSERT INTO dev_jobs VALUES (?,?,?,?,?,?)',(job['job_id'],sid,version,kind,'reserved',encode(job)))
            self._dev_save(db,s,'budget_reserved')
            return job

    @_dev_ledger
    def claim_dev_job(self,sid,jid,owner):
        with self._transaction() as db:
            s=self._dev_get(db,sid)
            if s['owner']!=owner or s['stop_requested'] or time.time()>=s['deadline_at']:raise DevConflict('owner, stop or deadline rejects dispatch')
            n=db.execute("UPDATE dev_jobs SET state='claimed' WHERE id=? AND session_id=? AND state='reserved'",(jid,sid)).rowcount
            if n!=1:raise DevConflict('job already claimed; never redispatch unknown work')
            self._dev_save(db,s,'job_claimed')

    @_dev_ledger
    def settle_dev_job(self,sid,jid,actual,receipt):
        with self._transaction() as db:
            s=self._dev_get(db,sid)
            row=db.execute('SELECT state,payload FROM dev_jobs WHERE id=? AND session_id=?',(jid,sid)).fetchone()
            if not row:raise DevConflict('unknown job')
            state,payload=row;job=json.loads(payload)
            if state=='settled':
                if job['actual']!=actual or job['receipt']!=receipt:raise DevConflict('conflicting settlement')
                return
            if state not in ('claimed','needs_review'):raise DevConflict('job not claimed')
            if set(actual)!=set(job['reservation']):raise DevConflict('complete actual resource usage required')
            if any(k not in job['reservation'] or type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=job['reservation'][k] for k,v in actual.items()):
                raise DevConflict('usage exceeds reservation or is invalid; retain for review')
            for k,v in actual.items():s['used'][k]+=v
            job.update(actual=actual,receipt=receipt)
            db.execute("UPDATE dev_jobs SET state='settled',payload=? WHERE id=?",(encode(job),jid));self._dev_save(db,s,'job_settled')

    @_dev_ledger
    def add_dev_version(self,sid,payload):
        if payload.get('formal_eligible') is not False:raise ValueError('Dev is never formal eligible')
        with self._transaction() as db:
            old=db.execute('SELECT payload FROM dev_versions WHERE session_id=? AND version=?',(sid,payload['version'])).fetchone()
            if old:
                if old[0]!=encode(payload):raise DevConflict('immutable Dev version conflict')
                return
            last=db.execute('SELECT COALESCE(MAX(version),0) FROM dev_versions WHERE session_id=?',(sid,)).fetchone()[0]
            if payload['version']!=last+1:raise DevConflict('Dev versions must be consecutive')
            db.execute('INSERT INTO dev_versions VALUES (?,?,?)',(sid,payload['version'],encode(payload)))
            self._dev_save(db,self._dev_get(db,sid),'version_saved')

    @_dev_ledger
    def mark_dev_unknown(self,sid,reason):
        with self._transaction() as db:
            s=self._dev_get(db,sid)
            db.execute("UPDATE dev_jobs SET state='needs_review' WHERE session_id=? AND state='claimed'",(sid,))
            s.update(state='needs_review',reason=reason)
            self._dev_save(db,s,'work_unknown')

    @_dev_ledger
    def retain_dev_review_evidence(self,sid,jid,receipt):
        """Attach obtained evidence without settling/refunding an unknown claim."""
        with self._transaction() as db:
            s=self._dev_get(db,sid)
            row=db.execute('SELECT state,payload FROM dev_jobs WHERE id=? AND session_id=?',(jid,sid)).fetchone()
            if not row or row[0] not in ('claimed','needs_review'):raise DevConflict('review evidence requires an unresolved claim')
            job=json.loads(row[1])
            if 'review_evidence' in job and job['review_evidence']!=receipt:raise DevConflict('immutable review evidence conflict')
            job['review_evidence']=receipt
            db.execute('UPDATE dev_jobs SET payload=? WHERE id=?',(encode(job),jid))
            self._dev_save(db,s,'review_evidence_saved')

    @_dev_ledger
    def recover_dev_sessions(self,owner_alive):
        recovered=[]
        with self._transaction() as db:
            for row in db.execute('SELECT payload FROM dev_sessions').fetchall():
                s=json.loads(row[0]);owner=s['owner']
                if not owner or owner_alive(owner):continue
                unknown=db.execute("UPDATE dev_jobs SET state='needs_review' WHERE session_id=? AND state='claimed'",(s['session_id'],)).rowcount
                s.update(owner=None,state='needs_review' if unknown else 'stopped' if s['stop_requested'] else 'ready',reason='worker_lost; no automatic redispatch' if unknown else 'safe_resume_available')
                self._dev_save(db,s,'recovered');recovered.append(s['session_id'])
        return recovered


def open_dev_v2_store(path):
    """Opt-in isolated v2 ledger; legacy six-resource sessions remain untouched."""
    from ..dev_ledger import DevV2Store
    return DevV2Store(path)
