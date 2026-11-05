"""Who may log in and who owns which run. The gateway keeps no keys and no run data, only these rows."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import secrets
import sqlite3
import threading
import time

SCHEMA = '''
CREATE TABLE IF NOT EXISTS invites(id TEXT PRIMARY KEY, code_hash TEXT UNIQUE NOT NULL, user_id TEXT, created_at REAL NOT NULL, expires_at REAL NOT NULL, revoked_at REAL);
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, created_at REAL NOT NULL, revoked_at REAL);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runs(experiment_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, host_id TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS runs_user ON runs(user_id);
'''


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    def __init__(self, path, *, clock=time.time):
        self.path = Path(path);self.clock = clock;self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.tx() as db:db.executescript(SCHEMA)
        self.path.chmod(0o600)

    @contextmanager
    def tx(self):
        with self.lock:
            db = sqlite3.connect(self.path);db.row_factory = sqlite3.Row
            try:
                with db:yield db
            finally:db.close()

    def create_invite(self, *, days=14):
        """The code exists in clear only in this return value; rows keep its hash."""
        code = secrets.token_urlsafe(24);invite = secrets.token_hex(4);now = self.clock()
        with self.tx() as db:db.execute('INSERT INTO invites VALUES(?,?,NULL,?,?,NULL)', (invite, digest(code), now, now + days * 86400))
        return invite, code

    def invites(self):
        with self.tx() as db:return [dict(r) for r in db.execute('SELECT id,user_id,created_at,expires_at,revoked_at FROM invites ORDER BY created_at')]

    def revoke_invite(self, invite):
        with self.tx() as db:
            row = db.execute('SELECT user_id FROM invites WHERE id=?', (invite,)).fetchone()
            if row is None:return False
            db.execute('UPDATE invites SET revoked_at=? WHERE id=?', (self.clock(), invite))
            if row['user_id']:db.execute('DELETE FROM sessions WHERE user_id=?', (row['user_id'],))
            return True

    def redeem(self, code):
        """One code is one person: the first login creates the user, later logins are that same user."""
        now = self.clock()
        with self.tx() as db:
            row = db.execute('SELECT * FROM invites WHERE code_hash=?', (digest(code),)).fetchone()
            if row is None or row['revoked_at'] is not None or row['expires_at'] <= now:return None
            if row['user_id']:
                user = db.execute('SELECT revoked_at FROM users WHERE id=?', (row['user_id'],)).fetchone()
                return row['user_id'] if user and user['revoked_at'] is None else None
            user_id = 'u-' + secrets.token_hex(8)
            db.execute('INSERT INTO users VALUES(?,?,NULL)', (user_id, now))
            db.execute('UPDATE invites SET user_id=? WHERE id=?', (user_id, row['id']))
            return user_id

    def revoke_user(self, user_id):
        with self.tx() as db:
            db.execute('UPDATE users SET revoked_at=? WHERE id=?', (self.clock(), user_id))
            db.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))

    def new_session(self, user_id, *, hours):
        token = secrets.token_urlsafe(32);csrf = secrets.token_urlsafe(32)
        with self.tx() as db:db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (digest(token), user_id, csrf, self.clock() + hours * 3600))
        return token, csrf

    def session(self, token):
        with self.tx() as db:
            db.execute('DELETE FROM sessions WHERE expires_at<=?', (self.clock(),))
            row = db.execute('SELECT user_id,csrf FROM sessions WHERE token_hash=?', (digest(token),)).fetchone()
            return dict(row) if row else None

    def end_session(self, token):
        with self.tx() as db:db.execute('DELETE FROM sessions WHERE token_hash=?', (digest(token),))

    def record_run(self, experiment_id, user_id, host_id):
        with self.tx() as db:db.execute('INSERT OR IGNORE INTO runs VALUES(?,?,?,?)', (experiment_id, user_id, host_id, self.clock()))

    def owns(self, user_id, experiment_id):
        with self.tx() as db:return db.execute('SELECT 1 FROM runs WHERE experiment_id=? AND user_id=?', (experiment_id, user_id)).fetchone() is not None

    def runs_of(self, user_id):
        with self.tx() as db:return {r['experiment_id'] for r in db.execute('SELECT experiment_id FROM runs WHERE user_id=?', (user_id,))}

    def runs_today(self, user_id):
        with self.tx() as db:
            return db.execute('SELECT count(*) FROM runs WHERE user_id=? AND created_at>?', (user_id, self.clock() - 86400)).fetchone()[0]
