"""Best-effort snapshot of the local Codex subscription window."""
import json
import math
from datetime import datetime, timezone
from pathlib import Path


def read_quota(home=None):
    root = Path(home).expanduser() if home is not None else Path.home()
    newest = None
    result = {"used_percent": None, "window_minutes": None, "resets_at": None,
              "plan_type": None, "credits_balance": None}
    try:
        paths = root.joinpath('.codex', 'sessions').rglob('rollout-*.jsonl')
        for path in paths:
            try:
                with path.open() as stream:
                    for line in stream:
                        try:
                            row = json.loads(line)
                            limits = row.get('payload', {}).get('rate_limits') or row.get('rate_limits')
                            if not isinstance(limits, dict):
                                continue
                            stamp = row.get('timestamp') or ''
                            if newest is not None and stamp < newest:
                                continue
                            primary = limits.get('primary') or {}
                            credits = limits.get('credits') or {}
                            newest = stamp
                            result = {"used_percent": primary.get('used_percent'),
                                      "window_minutes": primary.get('window_minutes'),
                                      "resets_at": primary.get('resets_at'),
                                      "plan_type": limits.get('plan_type'),
                                      "credits_balance": credits.get('balance')}
                        except (ValueError, TypeError, AttributeError):
                            continue
            except (OSError, UnicodeError):
                continue
    except (OSError, ValueError):
        pass
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
