"""Pre-started execution worker (try speed-up 2, item 10 "prespawn" route; user OK 11-14).

A fresh worker for the NEXT execution is started while the Dev agent is thinking. It does
the slow, execution-independent part (imports, scene build + reset, planner service start
and prepare), reports {'kind': 'prespawned'}, then waits for exactly one bind message that
carries the execution's real deadline. Nothing is reused: every execution still gets its
own new worker and planner service, and any problem discards the pre-started worker.

The bind deadline may only tighten the launch deadline, never extend it.
"""
import json
import math
import select
import time


class PrespawnError(RuntimeError):
    pass


def parse_bind(line, launch_deadline, now):
    try:
        message = json.loads(line)
    except ValueError as exc:
        raise PrespawnError('prespawn: bind message is not JSON') from exc
    if not isinstance(message, dict) or message.get('kind') != 'bind' or set(message) != {'kind', 'deadline'}:
        raise PrespawnError('prespawn: expected exactly one bind message')
    deadline = message['deadline']
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
        raise PrespawnError('prespawn: bind deadline must be a finite number')
    if deadline > launch_deadline:
        raise PrespawnError('prespawn: bind deadline may not extend the launch deadline')
    if deadline <= now:
        raise PrespawnError('prespawn: bind deadline already passed')
    return float(deadline)


def wait_for_bind(stream, launch_deadline, stop, *, clock=time.monotonic, poll=.5):
    """Block until the bind line arrives; returns the (tightened) execution deadline."""
    while True:
        if stop.is_set():
            raise PrespawnError('prespawn: stopped before bind')
        left = launch_deadline - clock()
        if left <= 0:
            raise PrespawnError('prespawn: launch deadline passed before bind')
        ready, _, _ = select.select([stream], [], [], min(poll, left))
        if not ready:
            continue
        line = stream.readline()
        if not line:
            raise PrespawnError('prespawn: owner closed the channel before bind')
        return parse_bind(line, launch_deadline, clock())


def tighten(planner, deadline):
    """Apply the execution deadline to the planner service owner and client (shorten only)."""
    for holder in (getattr(planner, 'owner', None), getattr(planner, 'client', None)):
        if holder is not None and hasattr(holder, 'deadline'):
            holder.deadline = min(holder.deadline, deadline)
