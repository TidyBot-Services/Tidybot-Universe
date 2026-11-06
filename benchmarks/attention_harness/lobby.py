"""Single-robot demo queue. All transitions use one SQLite transaction and an injected clock."""
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import random
import re
import secrets
import sqlite3
import statistics
import threading
import time

from .core.policies import POLICY_IDS

ACTIVE = ('running', 'waiting_human')
TERMINAL = ('finished', 'failed', 'cancelled')
SPEEDS = (1, 5, 20, 60)
# The demo keeps a short rolling history instead of growing for ever.
HISTORY_LIMIT = 8
SEED_HISTORY = 6
ANIMALS = ('Otter', 'Panda', 'Fox', 'Owl', 'Badger', 'Lynx', 'Robin', 'Heron')
SENSITIVE = re.compile(r'(?i)\b(?:bearer\s+\S+|(?:api[_-]?key|access[_-]?code|(?:control[_-]?)?token|password|secret)\s*[=:]\s*\S+|sk-[A-Za-z0-9_-]+)|(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])|(?<![\w:])(?:[a-f0-9]{0,4}:){2,}[a-f0-9:]*(?![\w:])')


class LobbyError(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


def safe_text(value):
    return SENSITIVE.sub('[redacted]', value) if isinstance(value, str) else value


def experiment_id(run_id):
    return 'devexp-' + hashlib.sha256(run_id.encode()).hexdigest()[:24]


class Lobby:
    def __init__(self, state_dir, sources, *, clock=time.time, speed=20, seed=1,
                 invitees=0, per_person_running=1, per_person_queued=1, help_wait_seconds=600):
        if not state_dir or speed not in SPEEDS or type(speed) is not int:
            raise ValueError('invalid lobby configuration')
        if any(type(v) is not int or v < 1 for v in (per_person_running, per_person_queued, help_wait_seconds)):
            raise ValueError('invalid lobby limits')
        self.path = Path(state_dir) / 'lobby.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock, self.sources, self.seed = clock, sources, seed
        self.invitees = invitees
        self.limits = dict(per_person_running=per_person_running, per_person_queued=per_person_queued,
                           help_wait_seconds=help_wait_seconds)
        self.lock = threading.RLock()
        with self.tx() as db:
            db.execute('PRAGMA journal_mode=WAL')
            schema = '''CREATE TABLE IF NOT EXISTS meta(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS viewers(id TEXT PRIMARY KEY,nickname TEXT UNIQUE NOT NULL,scripted INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS runs(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,type TEXT NOT NULL,run_id TEXT NOT NULL,nickname TEXT NOT NULL,task_title TEXT NOT NULL,at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS replies(run_id TEXT NOT NULL,attempt INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(run_id,attempt));'''
            for statement in schema.split(';'):
                if statement.strip():db.execute(statement)
            if self._meta(db) is None:
                now = self.clock()
                self._save_meta(db, dict(real_start=now, sim_start=now, speed=speed, last_real=now))
                rng = random.Random(seed)
                for index in range(invitees):
                    owner = 'invitee-' + str(index)
                    db.execute('INSERT INTO viewers VALUES(?,?,1)', (owner, f'{rng.choice(ANIMALS)}-{index+1}'))
                self._populate(db, now, history=True)
                self._advance(db, now)

    @contextmanager
    def tx(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            try:
                # journal_mode must be set before BEGIN.
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    @staticmethod
    def _meta(db):
        row = db.execute("SELECT payload FROM meta WHERE id='clock'").fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _save_meta(db, value):
        db.execute("INSERT OR REPLACE INTO meta VALUES('clock',?)", (json.dumps(value),))

    @staticmethod
    def _sim(meta, real):
        return meta['sim_start'] + (real - meta['real_start']) * meta['speed']

    @staticmethod
    def _rows(db):
        return [json.loads(r[0]) for r in db.execute('SELECT payload FROM runs ORDER BY seq')]

    @staticmethod
    def _save(db, run):
        db.execute('UPDATE runs SET payload=? WHERE id=?', (json.dumps(run), run['run_id']))

    @staticmethod
    def _event(db, kind, run, now):
        db.execute('INSERT INTO events(type,run_id,nickname,task_title,at) VALUES(?,?,?,?,?)',
                   (kind, run['run_id'], run['nickname'], run['task_title'], now))

    def identify(self, cookie=None, *, create=True):
        valid = isinstance(cookie, str) and re.fullmatch(r'[a-f0-9]{32}', cookie)
        owner = hashlib.sha256(cookie.encode()).hexdigest() if valid else None
        with self.tx() as db:
            row = db.execute('SELECT nickname FROM viewers WHERE id=? AND scripted=0', (owner,)).fetchone()
            if row:return dict(id=owner, nickname=row[0]), None
            if not create:return None, None
            cookie = secrets.token_hex(16)
            owner = hashlib.sha256(cookie.encode()).hexdigest()
            number = db.execute('SELECT count(*) FROM viewers').fetchone()[0] + 1
            nickname = f'{secrets.choice(ANIMALS)}-{number}'
            db.execute('INSERT INTO viewers VALUES(?,?,0)', (owner, nickname))
            return dict(id=owner, nickname=nickname), cookie

    def _new(self, db, owner, task_key, strategy, dev_source, share, now):
        matches = [s for s in self.sources.values() if s['task_key'] == task_key]
        if not matches:raise LobbyError('no_demo_source')
        source = matches[self._turn(db) % len(matches)]
        viewer = db.execute('SELECT nickname,scripted FROM viewers WHERE id=?', (owner,)).fetchone()
        if viewer is None:raise LobbyError('not_owner', 403)
        rows = [r for r in self._rows(db) if r['owner'] == owner]
        queued = sum(r['state'] == 'queued' for r in rows)
        active = sum(r['state'] in ACTIVE for r in rows)
        # A queued run is promoted only when its owner's active quota permits it.
        if queued >= self.limits['per_person_queued'] or active > self.limits['per_person_running']:
            raise LobbyError('already_queued')
        while True:
            run_id = 'sim-' + secrets.token_hex(4)
            if not db.execute('SELECT 1 FROM runs WHERE id=?', (run_id,)).fetchone():break
        run = dict(run_id=run_id, experiment_id=experiment_id(run_id), owner=owner,
            nickname=viewer[0], scripted=bool(viewer[1]), source_id=source['source_id'],
            task_key=task_key, task_title=source['title'], strategy=strategy, dev_source=dev_source,
            share_replies=share, state='queued', result=None, current=0, limit=source['limit'],
            created_at=now, started_at=None, ended_at=None, elapsed=0, work_start=None,
            waiting=None, help_requests=0, answered_attempts=[])
        db.execute('INSERT INTO runs(id,payload) VALUES(?,?)', (run_id, json.dumps(run)))
        self._event(db, 'joined', run, now)
        return run

    def _turn(self, db):
        """Recordings are handed out in the configured order, so wins and failures alternate."""
        row = db.execute("SELECT payload FROM meta WHERE id='turn'").fetchone()
        number = json.loads(row[0]) if row else self.seed
        db.execute("INSERT OR REPLACE INTO meta VALUES('turn',?)", (json.dumps(number+1),))
        return number

    def _prune(self, db):
        """Drop the oldest finished runs beyond the window; each person's latest run stays."""
        done = sorted((r for r in self._rows(db) if r['state'] in TERMINAL), key=lambda r:(r['ended_at'] or 0, r['run_id']))
        extra = len(done) - HISTORY_LIMIT
        if extra <= 0:return
        keep = {}
        for run in done:
            if not run['scripted']:keep[run['owner']] = run['run_id']
        for run in [r for r in done if r['run_id'] not in keep.values()][:extra]:
            for table, column in (('runs','id'),('replies','run_id'),('events','run_id')):
                db.execute(f'DELETE FROM {table} WHERE {column}=?', (run['run_id'],))

    def _populate(self, db, now, *, history=False):
        if not self.sources:return
        tasks = sorted({s['task_key'] for s in self.sources.values()})
        viewers = [r[0] for r in db.execute('SELECT id FROM viewers WHERE scripted=1 ORDER BY id').fetchall()]
        for turn in range(SEED_HISTORY if history and viewers else 0):
            index = turn % len(viewers)
            owner = viewers[index]
            task = tasks[(self.seed + index + turn) % len(tasks)]
            run = self._new(db, owner, task, 'autonomous', 'lab_codex', False, now)
            source = self.sources[run['source_id']]
            duration = source['duration'] / self._meta(db)['speed']
            ended = now - 60 - turn * 420
            run.update(state='finished', result=source['result'], current=len(source['attempt_done_at']),
                       started_at=ended - duration, ended_at=ended, created_at=ended - duration)
            self._save(db, run)
            self._event(db, 'finished', run, ended)
        for index, owner in enumerate(viewers):
            rows = [r for r in self._rows(db) if r['owner'] == owner]
            if any(r['state'] in (*ACTIVE, 'queued') for r in rows):continue
            task = tasks[(self.seed + index + len(rows)) % len(tasks)]
            self._new(db, owner, task, 'autonomous', 'lab_codex', False, now)

    def _reply(self, db, run, outcome, category, content, now):
        attempt = run['waiting']['attempt']
        payload = dict(attempt=attempt, outcome=outcome, category=category, content=content, at=now)
        db.execute('INSERT OR IGNORE INTO replies VALUES(?,?,?)', (run['run_id'], attempt, json.dumps(payload)))
        run['answered_attempts'].append(attempt)
        run.update(state='running', waiting=None, work_start=self._sim(self._meta(db), now))
        self._save(db, run)
        self._event(db, 'help_answered', run, now)

    def _advance(self, db, now):
        meta = self._meta(db)
        now = max(float(now), meta['last_real'])
        cursor = meta['last_real']
        while True:
            rows = self._rows(db)
            run = next((r for r in rows if r['state'] in ACTIVE), None)
            if run is None:
                self._populate(db, cursor)
                run = next((r for r in self._rows(db) if r['state'] == 'queued'), None)
                if run is None:break
                cursor = max(cursor,run['created_at'])
                run.update(state='running', started_at=cursor, work_start=self._sim(meta, cursor))
                self._save(db, run)
                self._event(db, 'started', run, cursor)
            source = self.sources[run['source_id']]
            if run['state'] == 'waiting_human':
                wait = run['waiting']
                if run['scripted']:
                    due_sim = wait['sim_at'] + wait['delay']
                    due = meta['real_start'] + (due_sim-meta['sim_start'])/meta['speed']
                else:due = wait['asked_at'] + self.limits['help_wait_seconds']
                if due > now:break
                cursor = max(cursor, due)
                self._reply(db, run, 'answered' if run['scripted'] else 'skipped', None, '', cursor)
                continue
            count = run['current']
            if count >= len(source['attempt_done_at']):
                tail = max(0, source['duration'] - run['elapsed'])
                due_sim = run['work_start'] + tail
            else:due_sim = run['work_start'] + max(0, source['attempt_done_at'][count]-run['elapsed'])
            due = meta['real_start'] + (due_sim-meta['sim_start'])/meta['speed']
            if due > now:break
            cursor = max(cursor, due)
            if count >= len(source['attempt_done_at']):
                run.update(state='finished', result=source['result'], ended_at=cursor)
                self._save(db, run)
                self._event(db, 'finished', run, cursor)
                continue
            count += 1
            run.update(current=count, elapsed=source['attempt_done_at'][count-1], work_start=self._sim(meta, cursor))
            self._event(db, 'attempt_done', run, cursor)
            help_row = source.get('helps', {}).get(str(count))
            if help_row and count not in run['answered_attempts']:
                run.update(state='waiting_human', help_requests=run['help_requests']+1,
                    waiting=dict(attempt=count, question=help_row['question'], failure=help_row['failure'],
                                 asked_at=cursor, sim_at=self._sim(meta, cursor), delay=help_row['delay']))
                self._event(db, 'help_asked', run, cursor)
            self._save(db, run)
        self._prune(db)
        meta['last_real'] = now
        self._save_meta(db, meta)

    def tick(self, now=None):
        with self.tx() as db:self._advance(db, self.clock() if now is None else now)

    def enqueue(self, viewer, *, task_key, strategy='autonomous', dev_source='lab_codex', share_replies=True):
        if not isinstance(strategy,str) or strategy not in (*POLICY_IDS,'agent_initiated_help'):
            raise LobbyError('invalid_body', 400)
        if not isinstance(dev_source,str) or dev_source not in ('lab_codex', 'lab_glm', 'own_key') or type(share_replies) is not bool:
            raise LobbyError('invalid_body', 400)
        with self.tx() as db:
            now = self.clock()
            run = self._new(db, viewer['id'], task_key, strategy, dev_source, share_replies, now)
            return self._card(db, run, viewer, now)

    def _owned(self, db, run_id, viewer):
        row = db.execute('SELECT payload FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:raise LobbyError('not_found', 404)
        run = json.loads(row[0])
        if not viewer or run['owner'] != viewer['id']:raise LobbyError('not_owner', 403)
        return run

    def cancel(self, run_id, viewer):
        with self.tx() as db:
            now = self.clock()
            run = self._owned(db, run_id, viewer)
            self._advance(db, now)
            run = self._owned(db, run_id, viewer)
            if run['state'] not in TERMINAL:
                run.update(state='cancelled', ended_at=now, waiting=None)
                self._save(db, run)
                self._event(db, 'cancelled', run, now)
                self._prune(db)
            return self._card(db, run, viewer, now)

    def reply(self, run_id, viewer, *, outcome, category=None, content=''):
        if (outcome not in ('answered', 'skipped') or category not in (None, 'position', 'speed', 'object', 'reach', 'other')
                or not isinstance(content, str) or len(content) > 4000 or (outcome == 'answered' and not content.strip())
                or safe_text(content) != content):
            raise LobbyError('invalid_body', 400)
        with self.tx() as db:
            now = self.clock()
            self._advance(db, now)
            run = self._owned(db, run_id, viewer)
            if run['state'] != 'waiting_human':raise LobbyError('not_waiting')
            self._reply(db, run, outcome, category, content, now)
            return self._card(db, run, viewer, now)

    def share(self, run_id, viewer, share):
        if type(share) is not bool:raise LobbyError('invalid_body', 400)
        with self.tx() as db:
            run = self._owned(db, run_id, viewer)
            run['share_replies'] = share
            self._save(db, run)
            return self._card(db, run, viewer, self.clock())

    def replies(self, run_id, viewer):
        with self.tx() as db:
            row = db.execute('SELECT payload FROM runs WHERE id=?', (run_id,)).fetchone()
            if not row:raise LobbyError('not_found', 404)
            run = json.loads(row[0])
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM replies WHERE run_id=? ORDER BY attempt', (run_id,))]

    def set_speed(self, speed):
        if type(speed) is not int or speed not in SPEEDS:raise LobbyError('invalid_speed', 400)
        with self.tx() as db:
            now = self.clock()
            self._advance(db, now)
            meta = self._meta(db)
            now = max(now, meta['last_real'])
            meta.update(sim_start=self._sim(meta, now), real_start=now, speed=speed)
            self._save_meta(db, meta)
            return dict(speed=speed, sim_now=meta['sim_start'])

    def records(self):
        with self.tx() as db:return self._rows(db)

    def find(self, eid):
        return next((r for r in self.records() if r['experiment_id'] == eid), None)

    def _card(self, db, run, viewer, now):
        rows = self._rows(db)
        queue = [r for r in rows if r['state'] == 'queued']
        position = next((i+1 for i, r in enumerate(queue) if r['run_id'] == run['run_id']), None)
        meta = self._meta(db)
        durations = [(r['ended_at']-r['started_at']) for r in rows if r['state'] == 'finished'][-20:]
        median = statistics.median(durations) if durations else self.sources[run['source_id']]['duration']/meta['speed']
        active = next((r for r in rows if r['state'] in ACTIVE), None)
        remaining = 0
        if active:
            elapsed = active['elapsed']
            if active['state'] == 'running':elapsed += max(0, self._sim(meta, now)-active['work_start'])
            remaining = max(0, self.sources[active['source_id']]['duration']-elapsed)/meta['speed']
            if active['state'] == 'waiting_human':
                wait = active['waiting']
                remaining += (max(0, wait['delay']-(self._sim(meta, now)-wait['sim_at']))/meta['speed'] if active['scripted']
                              else max(0, self.limits['help_wait_seconds']-(now-wait['asked_at'])))
        mine = bool(viewer and run['owner'] == viewer['id'])
        waiting = run['waiting']
        return {**{k:run[k] for k in ('run_id', 'experiment_id', 'nickname', 'task_key', 'task_title', 'state', 'result',
                    'started_at', 'ended_at', 'help_requests', 'strategy', 'dev_source', 'share_replies')},
                'mine':mine, 'attempt':dict(current=run['current'], limit=run['limit']),
                'recording':dict(id=run['source_id'], attempts=len(self.sources[run['source_id']]['attempt_done_at'])),
                'queue_position':position, 'eta_start_seconds':int(math.floor((remaining+(position-1)*median)/30+0.5)*30) if position else None,
                'help_waiting_seconds':max(0, now-waiting['asked_at']) if waiting else None,
                'waiting':{k:waiting[k] for k in ('attempt','question','failure','asked_at')} if waiting else None}

    def card(self, run_id, viewer):
        with self.tx() as db:
            run = next((r for r in self._rows(db) if r['run_id'] == run_id), None)
            if run is None:raise LobbyError('not_found', 404)
            return self._card(db, run, viewer, self.clock())

    def snapshot(self, viewer):
        with self.tx() as db:
            now = self.clock()
            self._advance(db, now)
            rows = self._rows(db)
            cards = [self._card(db, r, viewer, now) for r in rows]
            active = next((c for c in cards if c['state'] in ACTIVE), None)
            meta = self._meta(db)
            events = [dict(zip(('id','type','nickname','task_title','at'), r)) for r in db.execute(
                'SELECT id,type,nickname,task_title,at FROM events ORDER BY at DESC,id DESC LIMIT 30')]
            return dict(demo=True, demo_info=dict(recordings=len(self.sources), history_limit=HISTORY_LIMIT, tasks=sorted({s['task_key'] for s in self.sources.values()})), clock=dict(speed=meta['speed'], sim_now=self._sim(meta, max(now,meta['last_real']))),
                me=dict(nickname=viewer['nickname']) if viewer else None,
                robot=dict(state='busy' if active else 'idle', running=active),
                queue=[c for c in cards if c['state']=='queued'],
                recent=sorted([c for c in cards if c['state'] in TERMINAL], key=lambda c:c['ended_at'], reverse=True)[:20],
                counts=dict(all=len(rows), running=sum(c['state'] in ACTIVE for c in cards),
                    needs_you=sum(c['state']=='waiting_human' and c['mine'] for c in cards), queued=sum(c['state']=='queued' for c in cards),
                    failed=sum(c['state']=='failed' or c['result'] in ('failed','limit') for c in cards), succeeded=sum(c['result']=='solved' for c in cards)),
                limits=self.limits.copy(), events=events)
