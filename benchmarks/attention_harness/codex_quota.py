"""Best-effort snapshot of the local Codex subscription window."""
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

WEEKLY_READING_MAX_AGE = 10 * 60
# Concurrent sessions can report slightly lagging usage. Take a conservative
# maximum only within ten minutes of the newest observation, even if all are stale.
QUOTA_OBSERVATION_LAG_SECONDS = 10 * 60
# The window is one week, so a log untouched for longer cannot hold the current reading.
# Logs older than this are skipped first and read only when the recent ones hold no reading.
RECENT_LOG_SECONDS = 8 * 24 * 3600


def latest_quota_readings(readings):
    """Restrict one reset window to observations near its newest timestamp."""
    timed=[r for r in readings if type(r.get('observed_at')) in (int,float)
           and math.isfinite(r['observed_at'])]
    if not timed:return readings
    newest=max(r['observed_at'] for r in timed)
    return [r for r in timed if r['observed_at']>=newest-QUOTA_OBSERVATION_LAG_SECONDS]


def valid_reset(value):
    return type(value) in (int,float) and math.isfinite(value) and value>0


def current_window_readings(readings,*,now=None):
    """Select the latest known reset window; legacy receipts stay separate."""
    known=[r for r in readings if valid_reset(r.get('resets_at'))]
    if not known:return readings
    if now is not None:
        fresh=[r for r in known if type(r.get('observed_at')) in (int,float)
               and math.isfinite(r['observed_at']) and 0<=now-r['observed_at']<=WEEKLY_READING_MAX_AGE]
        known=fresh or known
    reset=max(r['resets_at'] for r in known)
    readings=known
    return [r for r in readings if r.get('resets_at')==reset]


def read_quota(home=None,*,newest_session=False,now=None,include_observed_at=False):
    if not newest_session:
        recent=_read_quota(home,now=now,include_observed_at=True,recent_only=True)
        # Accept the short read only for a reading with a known time; anything else gets the full read.
        if valid_reset(recent.get('resets_at')) and recent.get('observed_at') is not None:
            if not include_observed_at:recent.pop('observed_at')
            return recent
    return _read_quota(home,newest_session=newest_session,now=now,include_observed_at=include_observed_at)


def _read_quota(home=None,*,newest_session=False,now=None,include_observed_at=False,recent_only=False):
    root = Path(home).expanduser() if home is not None else Path.home()
    cutoff = min(time.time(),now if now is not None else math.inf)-RECENT_LOG_SECONDS
    newest = None
    readings=[]
    result = {"used_percent": None, "window_minutes": None, "resets_at": None,
              "plan_type": None, "credits_balance": None}
    try:
        paths = root.joinpath('.codex', 'sessions').rglob('*.jsonl' if newest_session else 'rollout-*.jsonl')
        if newest_session:
            paths=list(paths);paths=[max(paths,key=lambda p:(p.stat().st_mtime_ns,str(p)))] if paths else []
        for path in paths:
            try:
                if recent_only and path.stat().st_mtime<cutoff:continue
                with path.open() as stream:
                    for line in stream:
                        try:
                            row = json.loads(line)
                            limits = row.get('payload', {}).get('rate_limits') or row.get('rate_limits')
                            if not isinstance(limits, dict):
                                continue
                            stamp = row.get('timestamp') or ''
                            primary = next((limits.get(key) for key in ('primary','secondary')
                                            if isinstance(limits.get(key),dict) and limits[key].get('window_minutes')==10080),{})
                            credits = limits.get('credits') or {}
                            reading = {"used_percent": primary.get('used_percent'),
                                      "window_minutes": primary.get('window_minutes'),
                                      "resets_at": primary.get('resets_at'),
                                      "plan_type": limits.get('plan_type'),
                                      "credits_balance": credits.get('balance')}
                            if newest is None or stamp>=newest:newest=stamp;result=reading
                            if (type(reading['used_percent']) in (int,float)
                                    and math.isfinite(reading['used_percent']) and 0<=reading['used_percent']<=100):
                                readings.append((stamp,reading))
                        except (ValueError, TypeError, AttributeError):
                            continue
            except (OSError, UnicodeError):
                continue
    except (OSError, ValueError):
        pass
    # Keep the legacy newest-event behavior when no reset identity is available.
    known=[]
    for stamp,r in readings:
        if not valid_reset(r['resets_at']):continue
        observed=None
        try:
            date=datetime.fromisoformat(stamp.replace('Z','+00:00'))
            if date.tzinfo is not None:observed=date.timestamp()
        except (ValueError,TypeError,AttributeError,OverflowError):pass
        known.append(dict(r,observed_at=observed))
    if known:
        window=current_window_readings(known,now=time.time() if now is None else now)
        result=max(latest_quota_readings(window),
                   key=lambda r:(r['used_percent'],r['observed_at'] or 0))
        if not include_observed_at:result.pop('observed_at')
    return result


def quota_mark():
    return {"used_percent": read_quota()['used_percent'],
            "timestamp": datetime.now(timezone.utc).isoformat()}


def run_quota_usage(cell, read_ref):
    """Project only the first and last recorded Codex quota percentages."""
    refs = [version.get('model_output') for version in cell.get('versions', [])]
    for job in cell.get('jobs', []):
        receipt = job.get('receipt') or {}
        refs.extend(receipt.get(key) for key in ('response', 'advisor_answer', 'eval_review', 'review'))
    calls = []
    for ref in refs:
        if not ref:
            continue
        try:
            record = read_ref(ref)
        except Exception:
            continue
        if not isinstance(record, dict) or record.get('model_source') != 'codex':
            continue
        marks = []
        for key in ('quota_before', 'quota_after'):
            mark = record.get(key)
            if (isinstance(mark, dict) and isinstance(mark.get('timestamp'), str)
                    and mark['timestamp']
                    and isinstance(mark.get('used_percent'), (int, float))
                    and not isinstance(mark['used_percent'], bool)
                    and math.isfinite(mark['used_percent']) and 0 <= mark['used_percent'] <= 100):
                marks.append(mark)
            else:
                marks.append(None)
        if marks[0] or marks[1]:
            calls.append((min(mark['timestamp'] for mark in marks if mark), marks))
    if not calls:
        return None
    calls.sort(key=lambda call: call[0])
    first, last = calls[0][1][0], calls[-1][1][1]
    if first is None or last is None:
        return None
    return {'before': first['used_percent'], 'after': last['used_percent']}


if __name__ == '__main__':
    print(json.dumps(read_quota(), sort_keys=True))
