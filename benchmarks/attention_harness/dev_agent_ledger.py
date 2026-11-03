"""Agent accounting in separate tables; legacy resource dictionaries never change."""
from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3
import time

LIMITS = dict(formal_versions=10, moving_executions=40, read_only_probes=300,
              eval_runs=10, help=1)
ALIASES = dict(versions='formal_versions', executions='moving_executions', probes='read_only_probes')
EXECUTION_LIMITS = dict(sdk_calls=320, control_steps=9600, wall_seconds=1920)


class AgentLedger:
    def __init__(self, path, cell, *, help_limit=1, clock=time.time, validation=False, budget=None):
        if type(help_limit) is not int or not 0 <= help_limit <= 10:
            raise ValueError('invalid_help_budget')
        self.path, self.cell, self.clock = Path(path), cell, clock
        self.limits = {**LIMITS, 'help': help_limit}
        wall_seconds=21600
        if budget is not None:
            # A frozen, reduced Agent budget (manifest agent_limits). Never above the envelope.
            from .dev_agent_config import agent_limits
            if validation or agent_limits(budget) is None:raise ValueError('invalid_agent_limits')
            self.limits.update(formal_versions=budget['versions'], eval_runs=budget['versions'])
            wall_seconds=budget['wall_seconds']
        if validation:
            from .memory_agent_validation import ARM_BUDGET
            if help_limit!=0:raise ValueError('agent validation help must be zero')
            self.limits={k:ARM_BUDGET[k] for k in (*LIMITS,'trial_runs')}
            wall_seconds=ARM_BUDGET['active_wall_seconds']
        self.wall_seconds=wall_seconds
        with self.tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS v2_agent_runs (cell TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS v2_agent_jobs (cell TEXT, id TEXT, state TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(cell,id))')
            db.execute('CREATE TABLE IF NOT EXISTS v2_agent_quota (seq INTEGER PRIMARY KEY, cell TEXT, payload TEXT NOT NULL)')
            row = db.execute('SELECT payload FROM v2_agent_runs WHERE cell=?', (cell,)).fetchone()
            if row:
                if json.loads(row[0])['limits'] != self.limits or json.loads(row[0])['wall_seconds']!=wall_seconds: raise ValueError('immutable_agent_budget')
            else:
                self._put_run(db, dict(limits=self.limits, deadline=None, started_at=None,
                    paused_at=None, paused_seconds=0, wall_seconds=wall_seconds))

    @contextmanager
    def tx(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally: db.close()

    def _run(self, db):
        return json.loads(db.execute('SELECT payload FROM v2_agent_runs WHERE cell=?', (self.cell,)).fetchone()[0])

    def _put_run(self, db, row):
        db.execute('INSERT OR REPLACE INTO v2_agent_runs VALUES (?,?)', (self.cell, json.dumps(row, sort_keys=True)))

    def _jobs(self, db):
        return [dict(json.loads(p), id=k, state=s) for k,s,p in db.execute(
            'SELECT id,state,payload FROM v2_agent_jobs WHERE cell=? ORDER BY rowid', (self.cell,))]

    def jobs(self):
        with self.tx() as db: return self._jobs(db)

    def reserve(self, jid, kind, resources, *, execution=False):
        if any(k not in {*self.limits, 'model_turns'} or type(v) is not int or v < 0 for k,v in resources.items()):
            raise ValueError('invalid_resources')
        with self.tx() as db:
            rows = self._jobs(db)
            old = next((r for r in rows if r['id']==jid), None)
            if old:
                if old['resources'] != resources or old['kind'] != kind: raise ValueError('reservation_conflict')
                return old
            run = self._run(db)
            if run['paused_at'] is not None: raise ValueError('operator_paused')
            if run['deadline'] is not None and self.clock() >= run['deadline']: raise ValueError('wall_stop')
            used = {k: sum(r['resources'].get(k,0) for r in rows if r['state']!='released') for k in self.limits}
            if any(used[k]+resources.get(k,0)>cap for k,cap in self.limits.items()): raise ValueError('budget_exhausted')
            if used['eval_runs']+resources.get('eval_runs',0)>used['formal_versions']+resources.get('formal_versions',0):
                raise ValueError('eval_without_version')
            value = dict(kind=kind, resources=resources, reserved_at=self.clock(),
                         execution_limits=EXECUTION_LIMITS if execution else None)
            db.execute('INSERT INTO v2_agent_jobs VALUES (?,?,?,?)', (self.cell,jid,'reserved',json.dumps(value)))
            return dict(value,id=jid,state='reserved')

    def release_unclaimed(self, jid):
        """Cancel an intent only while the ledger proves it was never claimed."""
        with self.tx() as db:
            row=next(r for r in self._jobs(db) if r['id']==jid)
            if row['state']!='reserved':raise ValueError('release_requires_unclaimed')
            self._put_job(db,jid,'released',row)

    def reopen_unclaimed(self, jid):
        """New reservation epoch for a released, never-dispatched help intent."""
        with self.tx() as db:
            rows=self._jobs(db);row=next(r for r in rows if r['id']==jid)
            if row['state']!='released' or row.get('claimed_at') is not None or row['kind']!='help':
                raise ValueError('reopen_requires_unclaimed_help')
            run=self._run(db)
            if run['paused_at'] is not None:raise ValueError('operator_paused')
            if run['deadline'] is not None and self.clock()>=run['deadline']:raise ValueError('wall_stop')
            used={k:sum(r['resources'].get(k,0) for r in rows if r['state']!='released') for k in self.limits}
            if any(used[k]+row['resources'].get(k,0)>cap for k,cap in self.limits.items()):
                raise ValueError('budget_exhausted')
            row.update(reserved_at=self.clock(),reservation_epoch=row.get('reservation_epoch',0)+1)
            self._put_job(db,jid,'reserved',row)

    def claim(self, jid):
        with self.tx() as db:
            row = next(r for r in self._jobs(db) if r['id']==jid)
            if row['state'] != 'reserved': raise ValueError('claim_requires_reservation')
            run = self._run(db)
            if run['paused_at'] is not None: raise ValueError('operator_paused')
            now = self.clock()
            if run['deadline'] is None:
                run.update(started_at=now, deadline=now+run['wall_seconds']); self._put_run(db,run)
            if now >= run['deadline']: raise ValueError('wall_stop')
            row.update(claimed_at=now)
            self._put_job(db,jid,'claimed',row)

    def _put_job(self, db, jid, state, row):
        row = {k:v for k,v in row.items() if k not in ('state','id')}
        db.execute('UPDATE v2_agent_jobs SET state=?,payload=? WHERE cell=? AND id=?',
                   (state,json.dumps(row,sort_keys=True),self.cell,jid))

    def settle(self, jid, receipt, *, usage=None, unknown=False):
        with self.tx() as db:
            row = next(r for r in self._jobs(db) if r['id']==jid)
            if row['state'] not in ('claimed','unknown'): raise ValueError('settle_requires_claim')
            if usage is not None:
                if not isinstance(usage,dict) or any(type(v) is not int or v<0 for v in usage.values()):
                    raise ValueError('invalid_usage')
            row.update(receipt=receipt, usage=usage if usage is not None else 'unknown', settled_at=self.clock())
            self._put_job(db,jid,'unknown' if unknown else 'settled',row)

    def record_execution(self, jid, usage):
        """Known physical usage or explicit unknown, retaining per-execution holds."""
        usage=usage or {}
        actual={}
        for name,cap in EXECUTION_LIMITS.items():
            value=usage.get(name)
            if value is None:actual[name]='unknown';continue
            if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=cap:
                raise ValueError('execution_budget_exceeded:'+name)
            if name!='wall_seconds' and type(value) is not int:raise ValueError('invalid_execution_count')
            actual[name]=value
        with self.tx() as db:
            row=next(r for r in self._jobs(db) if r['id']==jid)
            if row['state']!='claimed' or row['execution_limits']!=EXECUTION_LIMITS:
                raise ValueError('execution_requires_claim')
            row['execution_usage']=actual
            row['execution_held']={k:EXECUTION_LIMITS[k] if v=='unknown' else 0 for k,v in actual.items()}
            self._put_job(db,jid,'claimed',row)

    def recover(self):
        """Never retry dispatched work. Unclaimed intents alone may be released."""
        with self.tx() as db:
            for row in self._jobs(db):
                if row['state']=='claimed': self._put_job(db,row['id'],'unknown',row)
                elif row['state']=='reserved': self._put_job(db,row['id'],'released',row)
            return any(r['state']=='unknown' for r in self._jobs(db))

    def park_request(self,jid):
        with self.tx() as db:
            row=next(r for r in self._jobs(db) if r['id']==jid)
            if row['state']!='claimed':raise ValueError('pause_requires_claim')
            self._put_job(db,jid,'paused',row)

    def resume_request(self,jid):
        with self.tx() as db:
            row=next(r for r in self._jobs(db) if r['id']==jid)
            if row['state']!='paused':raise ValueError('resume_requires_paused_request')
            self._put_job(db,jid,'claimed',row)

    def pause(self):
        with self.tx() as db:
            run=self._run(db)
            if run['paused_at'] is None:
                run['paused_at']=self.clock(); self._put_run(db,run)

    def resume(self):
        with self.tx() as db:
            run=self._run(db)
            if run['paused_at'] is not None:
                elapsed=max(0,self.clock()-run['paused_at'])
                if run['deadline'] is not None: run['deadline']+=elapsed
                run['paused_seconds']+=elapsed; run['paused_at']=None; self._put_run(db,run)

    def snapshot(self):
        with self.tx() as db:
            run=self._run(db); rows=self._jobs(db)
        used={k:sum(r['resources'].get(k,0) for r in rows if r['state'] not in ('reserved','released')) for k in self.limits}
        held={k:sum(r['resources'].get(k,0) for r in rows if r['state']=='reserved') for k in self.limits}
        now=run['paused_at'] if run['paused_at'] is not None else self.clock()
        remaining=max(0,run['deadline']-now) if run['deadline'] is not None else run['wall_seconds']
        return dict(run, used=used, held=held, remaining={k:self.limits[k]-used[k]-held[k] for k in self.limits}, wall_remaining=remaining,
                    model_turns=sum(r['resources'].get('model_turns',0) for r in rows if r['state']!='released'),
                    attention=dict(help_count=used['help'],hint_human_seconds=sum(
                        (r.get('receipt') or {}).get('hint_human_seconds',0) for r in rows
                        if r['kind']=='help' and r['state'] in ('settled','unknown'))))

    def quota(self, sample):
        with self.tx() as db:
            db.execute('INSERT INTO v2_agent_quota(cell,payload) VALUES (?,?)', (self.cell,json.dumps(sample,sort_keys=True)))


class LedgerBudget:
    """D1 broker interface backed by durable, independently keyed requests."""
    def __init__(self, ledger): self.ledger=ledger; self.request_id=None
    @property
    def limits(self): return {k:self.ledger.limits[v] for k,v in ALIASES.items()}
    @property
    def counts(self):
        snap=self.ledger.snapshot()
        return {k:snap['used'][v]+snap['held'][v] for k,v in ALIASES.items()}
    @property
    def deadline(self): return time.monotonic()+self.ledger.snapshot()['wall_remaining']
    def charge(self, **resources):
        if 'trial_runs' in self.ledger.limits and resources.get('executions') and not resources.get('versions'):
            resources=dict(resources,trial_runs=1)
        self.ledger.reserve('request:'+self.request_id,'request',
            {ALIASES.get(k,k):v for k,v in resources.items()},execution=bool(resources.get('executions')))
    def claim(self, rid): self.ledger.claim('request:'+rid)
    def settle(self, rid, response):
        self.ledger.settle('request:'+rid,response,unknown=response['status']=='needs_review')
