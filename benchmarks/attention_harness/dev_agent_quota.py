"""Fail-closed weekly quota sampling, scoped by reset identity."""
import json
import math
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from .codex_quota import WEEKLY_READING_MAX_AGE,latest_quota_readings

# The same weekly window is reported with a few seconds of resets_at jitter across
# Codex sessions (seen 2026-10-28: ...690 vs ...692); real windows are 7 days apart.
RESET_JITTER_SECONDS = 60
ROLLOUT_MAX_AGE = 3 * 86400


def read_weekly_quota(home=None, *, now=None):
    """Newest weekly window and observation, allowing ten minutes of session lag."""
    now=time.time() if now is None else now
    root=Path(home).expanduser()/'.codex' if home is not None else Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')
    rows=[]
    for path in root.joinpath('sessions').rglob('rollout-*.jsonl'):
        try:
            if now-path.stat().st_mtime>ROLLOUT_MAX_AGE:continue
            text=path.read_text(errors='ignore')
        except OSError:continue
        for line in text.splitlines():
            if '"rate_limits"' not in line:continue
            try:event=json.loads(line)
            except ValueError:continue
            try:stamp=datetime.fromisoformat(event.get('timestamp','').replace('Z','+00:00')).timestamp()
            except (ValueError,AttributeError):continue
            stack=[event]
            while stack:
                value=stack.pop()
                if isinstance(value,dict):
                    if (value.get('window_minutes')==10080 and type(value.get('used_percent')) in (int,float)
                            and type(value.get('resets_at')) in (int,float) and value['resets_at']>now):
                        rows.append(dict(used_percent=value['used_percent'],window_minutes=10080,
                                         resets_at=value['resets_at'],observed_at=stamp))
                    stack.extend(value.values())
                elif isinstance(value,list):stack.extend(value)
    if not rows:raise OSError('no current weekly quota reading')
    newest=max(r['resets_at'] for r in rows)
    window=latest_quota_readings([r for r in rows if r['resets_at']>=newest-RESET_JITTER_SECONDS])
    return dict(used_percent=max(r['used_percent'] for r in window),window_minutes=10080,
                resets_at=newest,observed_at=max(r['observed_at'] for r in window))


class QuotaUnavailable(RuntimeError): pass


class QuotaMonitor:
    def __init__(self, private, ledger, *, stop_percent=90, reader=None, clock=time.time):
        if type(stop_percent) not in (int,float) or not math.isfinite(stop_percent) or not 0<stop_percent<=100:
            raise ValueError('invalid_quota_stop_percent')
        self.private,self.ledger,self.stop_percent=private,ledger,stop_percent
        self.reader=reader or read_weekly_quota
        self.clock=clock; self.latest=None; self.stopped=False; self.failure=None
        self.lock=threading.RLock()
        prior=self.private/'quota-event.json'
        if prior.exists():self.latest=json.loads(prior.read_bytes())

    def event(self, event):
        """CLI variants wrap weekly limits in payload/rate_limits/secondary."""
        now=self.clock()
        def walk(value, stamp):
            if not isinstance(value,dict):return
            raw=value.get('timestamp')
            if isinstance(raw,str):
                try: stamp=datetime.fromisoformat(raw.replace('Z','+00:00')).timestamp()
                except ValueError:pass
            if value.get('window_minutes')==10080 and self._valid(dict(value,observed_at=stamp),now):
                candidate=dict(value,observed_at=stamp)
                with self.lock:
                    if (self.latest is None or candidate['resets_at']>self.latest['resets_at']+RESET_JITTER_SECONDS
                            or (candidate['resets_at']>=self.latest['resets_at']-RESET_JITTER_SECONDS
                                and (candidate['observed_at'],candidate['used_percent'])
                                    >=(self.latest['observed_at'],self.latest['used_percent']))):
                        self.latest=candidate
                        from .dev_agent_workspace import atomic_write,encode
                        atomic_write(self.private,'quota-event.json',encode(candidate))
            for child in value.values():walk(child,stamp)
        walk(event,now)

    @staticmethod
    def _valid(row, now):
        return (isinstance(row,dict) and row.get('window_minutes')==10080
                and all(type(row.get(k)) in (int,float) and math.isfinite(row[k]) for k in ('used_percent','resets_at','observed_at'))
                and 0<=row['used_percent']<=100 and row['resets_at']>now and row['observed_at']<=now+60)

    def sample(self, reason):
        with self.lock:
            now=self.clock(); reading=None; error=None
            try:reading=self.reader()
            except Exception:error='quota_unreadable'
            now=self.clock()
            candidates=[r for r in (reading,self.latest) if self._valid(r,now)]
            if candidates:
                reset=max(r['resets_at'] for r in candidates)
                window=latest_quota_readings([r for r in candidates if r['resets_at']>=reset-RESET_JITTER_SECONDS])
                used=max(r['used_percent'] for r in window)
                observed=max(r['observed_at'] for r in window)
                self.stopped=self.stopped or used>=self.stop_percent
                # Usage only moves when Codex runs, and every local Codex turn writes a fresh
                # reading; an old reading (e.g. during a long execution) is used but flagged.
                sample=dict(at=now,reason=reason,used_percent=used,resets_at=reset,stop_percent=self.stop_percent,
                            stopped=self.stopped,observed_at=observed,stale=now-observed>WEEKLY_READING_MAX_AGE)
            else:
                self.failure=error or 'quota_stale_or_unavailable'
                sample=dict(at=now,reason=reason,error=self.failure,stop_percent=self.stop_percent)
            with (self.private/'quota.jsonl').open('a') as stream:
                stream.write(json.dumps(sample,sort_keys=True)+'\n');stream.flush()
            self.ledger.quota(sample)
            if self.failure:raise QuotaUnavailable(self.failure)
            return sample

    def check(self, reason):
        self.sample(reason)
        if self.stopped:raise ValueError('quota_stop')
