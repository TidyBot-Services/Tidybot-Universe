"""Best-effort snapshot of the local Codex subscription window."""
import json
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


if __name__ == '__main__':
    print(json.dumps(read_quota(), sort_keys=True))
