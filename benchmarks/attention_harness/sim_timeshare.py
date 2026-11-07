"""Remote only: one simulator token per host, taken for one execution at a time.

Several Remote runs may be open at once. Each Try or formal version takes the token right before its simulator starts and
gives it back as soon as that execution ends. The token is the host's sim.lock (the same file other tools on the host
lock), so only one execution anywhere on the host moves a robot. Who gets it next is first come, first served by
execution: a run that gives the token back and wants it again joins the end of the line, behind everyone already
waiting. Read-only probes never take it.

The line is a small SQLite file next to the lock. A ticket whose process has died is dropped, so a crashed run never
blocks the line. Local 8769 runs never construct this.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import time

from .dev_loop import owner_alive, process_identity

POLL_SECONDS = .5
KEPT = []  # Locks held on purpose until the process exits.


class SimWaitCancelled(Exception):
    """The run was stopped while it waited for the robot; nothing was executed."""


class Grant:
    """The token for one execution. keep() holds the lock until this process exits: for a simulator that may still be running."""
    def __init__(self):self.kept = False
    def keep(self):self.kept = True


class SimToken:
    def __init__(self, lock_path, *, label, clock=time.time, poll=POLL_SECONDS, identity=None):
        self.lock_path = Path(lock_path);self.label, self.clock, self.poll = label, clock, poll
        self.queue_path = self.lock_path.with_name(self.lock_path.name + '.queue.sqlite3')
        self.identity = identity or process_identity()
        if self.identity is None:raise ValueError('sim_token_identity_unavailable')
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tickets (seq INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL, owner TEXT NOT NULL, requested_at REAL NOT NULL, granted_at REAL)')

    @contextmanager
    def tx(self):
        db = sqlite3.connect(self.queue_path, timeout=30)
        try:
            db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except BaseException:
            db.rollback();raise
        finally:db.close()

    def _drop_dead(self, db):
        for seq, owner in db.execute('SELECT seq, owner FROM tickets').fetchall():
            if not owner_alive(json.loads(owner)):db.execute('DELETE FROM tickets WHERE seq=?', (seq,))

    def line(self):
        """Everyone holding or waiting for the token, first in line first."""
        with self.tx() as db:
            self._drop_dead(db)
            return [dict(seq=s, label=l, requested_at=r, granted_at=g) for s, l, r, g in db.execute('SELECT seq, label, requested_at, granted_at FROM tickets ORDER BY seq')]

    def _try_lock(self):
        handle = open(self.lock_path, 'a')
        try:fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close();return None
        return handle

    @contextmanager
    def hold(self, *, cancelled=lambda:False, on_wait=lambda ahead:None, on_granted=lambda waited:None):
        """Wait in line, hold the token for one execution, give it back. on_wait(ahead) runs while waiting, with how many are ahead
        (the holder counts). Raises SimWaitCancelled if cancelled() turns true before the token is granted."""
        requested = self.clock()
        with self.tx() as db:
            seq = db.execute('INSERT INTO tickets (label, owner, requested_at) VALUES (?,?,?)', (self.label, json.dumps(self.identity, sort_keys=True), requested)).lastrowid
        handle = None;grant = Grant()
        try:
            last = None
            while True:
                if cancelled():raise SimWaitCancelled('stopped while waiting for the robot')
                with self.tx() as db:
                    self._drop_dead(db)
                    ahead = db.execute('SELECT COUNT(*) FROM tickets WHERE seq<?', (seq,)).fetchone()[0]
                    if ahead == 0:
                        handle = self._try_lock()
                        if handle is not None:
                            db.execute('UPDATE tickets SET granted_at=? WHERE seq=?', (self.clock(), seq))
                            break
                        ahead = 1  # Something outside the line (another tool on this host) holds the lock.
                if ahead != last:
                    on_wait(ahead);last = ahead
                time.sleep(self.poll)
            on_granted(self.clock() - requested)
            yield grant
        finally:
            if handle is not None and grant.kept:
                KEPT.append(handle)  # Never closed here: the OS lets go when this process exits, after its simulator.
            elif handle is not None:
                fcntl.flock(handle, fcntl.LOCK_UN);handle.close()
            with self.tx() as db:db.execute('DELETE FROM tickets WHERE seq=?', (seq,))


def write_state(private, value):
    """What the run page and the lobby read about this run and the robot: waiting (with how many ahead), executing, or nothing."""
    from .dev_agent_workspace import atomic_write, encode
    if value is None:
        try:os.unlink(Path(private) / 'sim-token.json')
        except FileNotFoundError:pass
        return
    atomic_write(Path(private), 'sim-token.json', encode(value))


def read_state(private):
    path = Path(private) / 'sim-token.json'
    try:value = json.loads(path.read_bytes())
    except (OSError, ValueError):return None
    if not isinstance(value, dict) or value.get('state') not in ('waiting', 'executing'):return None
    # A run that died mid-execution leaves its file behind; only a live owner's state is true.
    if not isinstance(value.get('owner'), dict) or not owner_alive(value['owner']):return None
    return {k: v for k, v in value.items() if k in ('state', 'ahead', 'since', 'waited_seconds')}
