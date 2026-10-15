"""Recover abandoned UI launches; absence must be positively established locally."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from collections.abc import Callable

from .core.store import AttentionStore


def process_namespace() -> dict[str, str] | None:
    try:
        return {'pid_namespace': os.readlink('/proc/self/ns/pid'),
                'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
    except OSError:
        return None


def process_alive(pid: int) -> bool:
    """Permission errors and invalid identities are unknown, hence treated as live.

    This probe is valid only for processes launched in this PID namespace. It
    must never be used as a host-service idle guard from an isolated namespace.
    """
    if type(pid) is not int or pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def recover_lost_runs(store: AttentionStore, *,
                      alive: Callable[[int], bool] = process_alive,
                      clock: Callable[[], float] = time.time) -> list[str]:
    recovered = []
    for launch in store.list_launches():
        run_id = launch.get('run_id')
        run = store.get_run(run_id) if run_id else None
        # A missing PID or a terminal receipt is not evidence of scheduler loss.
        if (launch.get('state') != 'started' or run is None
                or run['status'] != 'running' or 'pid' not in launch
                or process_namespace() is None
                or launch.get('process_namespace') != process_namespace()):
            continue
        if alive(launch['pid']):
            continue
        now = clock()
        counters = store.executor_counters(run_id)
        with store._transaction() as db:
            # Recheck terminal and run state under the write lock. All cleanup
            # and the launch receipt commit together, including concurrent polls.
            if db.execute('SELECT 1 FROM events WHERE event_key=?',
                          (f'launch-terminal:{launch["launch_id"]}',)).fetchone():
                continue
            row = db.execute('SELECT payload FROM runs WHERE id=?', (run_id,)).fetchone()
            current = json.loads(row[0])
            if current['status'] != 'running':
                continue

            def event(key, entity, kind, payload, entity_type='audit'):
                db.execute('INSERT INTO events(event_key,entity_type,entity_id,event_type,payload) '
                           'VALUES (?,?,?,?,?)', (key, entity_type, entity, kind,
                                                 json.dumps(payload, sort_keys=True)))

            for request_row in db.execute(
                    "SELECT id,payload FROM requests WHERE json_extract(payload,'$.run_id')=?",
                    (run_id,)).fetchall():
                request_id, raw = request_row
                request = json.loads(raw)
                if request['state'] in {'pending', 'fallback'}:
                    request['state'] = 'cancelled'
                    store._update_with_event(db, 'requests', request_id, request,
                        f'recovery-cancel:{request_id}', 'request.cancelled', 'request')
                    event(f'recovery-time:{request_id}', request_id, 'request.recovered',
                          {'request_id': request_id, 'run_id': run_id, 'cancelled_at': now,
                           'reason': 'scheduler_lost'})
                start = db.execute('SELECT payload FROM events WHERE event_key=?',
                                   (f'waiting-start:{request_id}',)).fetchone()
                end = db.execute('SELECT 1 FROM events WHERE event_key=?',
                                 (f'waiting-end:{request_id}',)).fetchone()
                if start and not end:
                    window = json.loads(start[0])
                    window.update(ended_at=max(now, window['started_at']), end_counters=counters,
                        attempt_dispatches=counters['attempt_dispatches']-window['baseline']['attempt_dispatches'],
                        sdk_motion_commands=(None if counters['unmeasured_attempts'] != window['baseline']['unmeasured_attempts']
                            else counters['sdk_motion_commands']-window['baseline']['sdk_motion_commands']))
                    event(f'waiting-end:{request_id}', request_id, 'waiting.ended', window)
            holds = db.execute("SELECT id,credits FROM budget_reservations WHERE run_id=? AND status='reserved'", (run_id,)).fetchall()
            db.execute("UPDATE budget_reservations SET status='released' WHERE run_id=? AND status='reserved'", (run_id,))
            for reservation_id, credits in holds:
                event(f'terminal-release:{reservation_id}', run_id, 'budget.released',
                      {'reservation_id': reservation_id, 'credits': credits, 'reason': 'scheduler_lost'}, 'run')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='resource_reservations'").fetchone():
                db.execute("UPDATE resource_reservations SET status='released' WHERE run_id=? AND status='reserved'", (run_id,))
            current['status'] = 'failed'
            store._update_with_event(db, 'runs', run_id, current,
                                     f'recovery-failed:{run_id}', 'run.failed', 'run')
            event(f'recovery-reason:{run_id}', run_id, 'work.failed',
                  {'run_id': run_id, 'work_id': launch['launch_id'], 'state': 'failed',
                   'reason': 'scheduler_lost', 'ended_at': now}, 'work')
            event(f'launch-terminal:{launch["launch_id"]}', launch['launch_id'], 'launch.failed',
                  {'launch_id': launch['launch_id'], 'run_id': run_id, 'state': 'failed',
                   'exit_code': -1, 'reason': 'scheduler_lost', 'ended_at': now,
                   'formal_eligible': False}, 'launch')
        recovered.append(run_id)
    return recovered
