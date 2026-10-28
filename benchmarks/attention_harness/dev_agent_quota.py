"""Fail-closed weekly quota sampling, scoped by reset identity."""
import json
import math
import threading
import time
from datetime import datetime
from .codex_quota import read_quota, WEEKLY_READING_MAX_AGE


class QuotaUnavailable(RuntimeError): pass


class QuotaMonitor:
    def __init__(self, private, ledger, *, stop_percent=90, reader=None, clock=time.time):
        if type(stop_percent) not in (int,float) or not math.isfinite(stop_percent) or not 0<stop_percent<=100:
            raise ValueError('invalid_quota_stop_percent')
        self.private,self.ledger,self.stop_percent=private,ledger,stop_percent
        self.reader=reader or (lambda:read_quota(include_observed_at=True))
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
                    if self.latest is None or (candidate['resets_at'],stamp)>=(self.latest['resets_at'],self.latest['observed_at']):
                        self.latest=candidate
                        from .dev_agent_workspace import atomic_write,encode
                        atomic_write(self.private,'quota-event.json',encode(candidate))
            for child in value.values():walk(child,stamp)
        walk(event,now)

    @staticmethod
    def _valid(row, now):
        return (isinstance(row,dict) and row.get('window_minutes')==10080
                and all(type(row.get(k)) in (int,float) and math.isfinite(row[k]) for k in ('used_percent','resets_at','observed_at'))
                and 0<=row['used_percent']<=100 and row['resets_at']>now
                and 0<=now-row['observed_at']<=WEEKLY_READING_MAX_AGE)

    def sample(self, reason):
        with self.lock:
            now=self.clock(); reading=None; error=None
            try:reading=self.reader()
            except Exception:error='quota_unreadable'
            now=self.clock()
            candidates=[r for r in (reading,self.latest) if self._valid(r,now)]
            if candidates:
                reset=max(r['resets_at'] for r in candidates)
                used=max(r['used_percent'] for r in candidates if r['resets_at']==reset)
                self.stopped=self.stopped or used>=self.stop_percent
                sample=dict(at=now,reason=reason,used_percent=used,resets_at=reset,stop_percent=self.stop_percent,stopped=self.stopped)
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
