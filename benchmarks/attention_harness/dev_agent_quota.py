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


def quota_stop_reason(exc):
    reason=str(exc)
    return reason if reason in ('quota_stop','quota_unreadable_after_probe') else 'quota_unavailable'


def probe_weekly_quota(private, on_event):
    """One short, isolated Codex turn; keep its rollout for quota/token recovery."""
    from .dev_agent_session import CodexAgentSession
    root=private/'quota-probe'
    ws=root/'ws';ws.mkdir(parents=True,exist_ok=True)
    session=CodexAgentSession(ws,root,model='gpt-6.1-sol',reasoning_effort='low')
    deadline=time.monotonic()+60
    try:
        for event in session.start('Reply with only OK. Do not use tools, read files or perform any other work.'):
            if event:on_event(event)
            if time.monotonic()>=deadline:raise TimeoutError('quota_probe_timeout')
    finally:session.cancel('quota_probe_finished')


def latest_probe_failure(root, *, since=0):
    """Public reason only, from the newest quota sample; never initialize a run DB."""
    import sqlite3
    latest={}
    for path in Path(root).glob('devexp-*/ledger.sqlite3'):
        try:
            with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
                row=db.execute('SELECT payload FROM v2_agent_quota ORDER BY seq DESC LIMIT 1').fetchone()
            sample=json.loads(row[0]) if row else {}
            if sample.get('at',0)>max(since,latest.get('at',0)):latest=sample
        except (sqlite3.Error,OSError,ValueError):continue
    return latest.get('error') if latest.get('error')=='quota_unreadable_after_probe' else None


# User 11-08: one run may use at most this many points of the weekly Codex quota.
# User 11-11: the points are the run's OWN use. Runs open at the same time share the one weekly counter, so its rise
# says nothing about one run (three runs open together were each stopped for the 5 points the three used together).
# A run's own use is counted in tokens and turned into points at a fixed rate.
RUN_CAP_POINTS = 5
# Measured on the lab account: 133 M tokens = 5 points (11-11, three runs), 170 M = 5 points (11-10), 31 M = 1 point.
# That is 27-34 M tokens a point; 25 M counts a run's use a little high, so the cap stops early rather than late.
# Tokens are input (cached included) plus output, as Codex reports them in total_token_usage.
TOKENS_PER_POINT = 25_000_000
ROLLOUT_TAIL_BYTES = 1 << 20


def _tokens(usage):
    """input + output of one Codex usage record, or None when it is not one."""
    if not isinstance(usage,dict):return None
    total=usage.get('total_tokens')
    if type(total) is int and total>=0:return total
    parts=[usage.get('input_tokens'),usage.get('output_tokens')]
    if all(type(v) is int and v>=0 for v in parts):return sum(parts)
    return None


def rollout_tokens(thread_id, home=None):
    """Tokens a persistent Codex session has used so far (its newest total_token_usage); None when its file is missing."""
    root=Path(home).expanduser()/'.codex' if home is not None else Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')
    paths=list(root.joinpath('sessions').rglob('rollout-*%s.jsonl'%thread_id))
    if not paths:return None
    best=0
    for path in paths:
        try:
            size=path.stat().st_size
            for span in (ROLLOUT_TAIL_BYTES,size):
                with path.open('rb') as stream:
                    stream.seek(max(0,size-span));lines=stream.read().split(b'\n')
                found=None
                for line in reversed(lines):
                    if b'"total_token_usage"' not in line:continue
                    try:info=(json.loads(line).get('payload') or {}).get('info') or {}
                    except ValueError:continue  # the first line of a tail can be cut
                    found=_tokens(info.get('total_token_usage'))
                    if found is not None:break
                if found is not None or span>=size:break
        except OSError:return None
        best=max(best,found or 0)
    return best


class QuotaMonitor:
    def __init__(self, private, ledger, *, stop_percent=90, reader=None, clock=time.time, run_cap_points=RUN_CAP_POINTS,
                 tokens_per_point=TOKENS_PER_POINT, rollout_reader=None, probe=None):
        if type(stop_percent) not in (int,float) or not math.isfinite(stop_percent) or not 0<stop_percent<=100:
            raise ValueError('invalid_quota_stop_percent')
        self.private,self.ledger,self.stop_percent=private,ledger,stop_percent
        self.reader=reader or read_weekly_quota
        self.clock=clock; self.latest=None; self.stopped=False; self.failure=None
        self.started=clock()
        self.run_cap_points=run_cap_points; self.capped=False
        base=self.private/'quota-baseline.json'
        self.baseline=json.loads(base.read_bytes()) if base.exists() else None
        self.lock=threading.RLock()
        prior=self.private/'quota-event.json'
        if prior.exists():self.latest=json.loads(prior.read_bytes())
        # The run's own use: persistent sessions (Dev) are read from their session file, one-shot calls (Eval, Advisor)
        # are added up from the usage each call reports. Kept in the run's folder, so a resumed run goes on counting.
        self.tokens_per_point=tokens_per_point; self.rollout_reader=rollout_reader or rollout_tokens
        own=self.private/'quota-own.json'
        self.own=json.loads(own.read_bytes()) if own.exists() else dict(threads={},call_tokens=0)
        # Only the production controller supplies a real probe. Offline callers must inject a fake.
        self.probe=probe
        self.probe_path=self.private/'quota-probe.json'

    def _record(self, sample):
        with (self.private/'quota.jsonl').open('a') as stream:
            stream.write(json.dumps(sample,sort_keys=True)+'\n');stream.flush()
        self.ledger.quota(sample)

    def _probe_once(self, reason):
        """Persist intent before dispatch, including across controller restarts."""
        from .dev_agent_workspace import atomic_write,encode
        if self.probe_path.exists() or self.probe is None:return
        row=dict(at=self.clock(),event='quota_probe_after_reset',reason='quota_probe_after_reset',
                 trigger=reason,status='started')
        jid='quota_probe_after_reset'
        self.ledger.reserve(jid,'quota_probe',{})
        self.ledger.claim(jid)
        atomic_write(self.private,self.probe_path.name,encode(row))
        self._record(row)
        thread=None;usage=None
        def event(value):
            nonlocal thread,usage
            if value.get('type')=='thread.started':thread=value.get('thread_id')
            self.event(value,session=True)
            if value.get('type')=='turn.completed':
                usage=value.get('usage')
                if not isinstance(usage,dict) or any(type(v) is not int or v<0 for v in usage.values()):usage=None
                tokens=_tokens(usage)
                if tokens is not None:
                    # This is exactly one turn. Its reported usage also provides a floor when
                    # the rollout has not been flushed yet; never count both sources twice.
                    if thread in self.own['threads']:
                        seen=self.own['threads'][thread]
                        seen.update(tokens=max(tokens,seen['tokens']),found=True);self._save_own()
                    else:self.spent(usage)
        try:
            self.probe(self.private,event)
            row['status']='completed'
        except Exception as exc:
            row.update(status='failed',error=type(exc).__name__)
        finally:
            row.update(finished_at=self.clock(),usage=usage,thread_id=thread)
            atomic_write(self.private,self.probe_path.name,encode(row))
            self.ledger.settle(jid,row,usage=usage)

    def _save_own(self):
        from .dev_agent_workspace import atomic_write,encode
        atomic_write(self.private,'quota-own.json',encode(self.own))

    def spent(self, usage):
        """Add the usage of one finished one-shot call (Eval, Advisor) to this run's own count."""
        tokens=_tokens(usage)
        if not tokens:return
        with self.lock:
            self.own['call_tokens']+=tokens;self._save_own()

    def own_tokens(self):
        """(tokens this run has used itself, None) or (None, why they cannot be counted)."""
        with self.lock:
            total=self.own['call_tokens'];changed=False
            for tid,seen in list(self.own['threads'].items()):
                try:now=self.rollout_reader(tid)
                except Exception:now=None
                if now is None and not seen.get('found'):return None,'session_file_missing'
                if now is not None and (now>seen.get('tokens',0) or not seen.get('found')):
                    self.own['threads'][tid]=seen=dict(tokens=max(now,seen.get('tokens',0)),found=True);changed=True
                total+=seen.get('tokens',0)
            if changed:self._save_own()
            return total,None

    def event(self, event, *, session=False, count=True):
        """CLI variants wrap weekly limits in payload/rate_limits/secondary.

        session=True marks the stream of a persistent session: its tokens are read from its session file (a turn can
        run for hours without reporting usage), so the usage of its finished turns is not added a second time.
        count=False: the stream is not on the lab's Codex account (another provider), so none of it is this run's use."""
        now=self.clock()
        if count and isinstance(event,dict):
            if session and event.get('type')=='thread.started' and isinstance(event.get('thread_id'),str):
                with self.lock:
                    if event['thread_id'] not in self.own['threads']:
                        self.own['threads'][event['thread_id']]=dict(tokens=0,found=False);self._save_own()
            elif not session and event.get('type')=='turn.completed':self.spent(event.get('usage'))
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
            if not candidates and self.probe is not None:
                tokens,_=self.own_tokens()
                # Even with a missing session, the already-accounted subtotal is a lower bound.
                known=self.own['call_tokens']+sum(v.get('tokens',0) for v in self.own['threads'].values())
                at_cap=self.run_cap_points is not None and known/self.tokens_per_point>=self.run_cap_points
                if self.stopped or at_cap:
                    self.capped=self.capped or at_cap;self.stopped=True
                    self._record(dict(at=now,reason=reason,error='quota_stop',stopped=True,
                                      run_tokens=tokens,run_cap_points=self.run_cap_points))
                    raise ValueError('quota_stop')
                self._probe_once(reason)
                try:reading=self.reader()
                except Exception:reading=None
                now=self.clock()
                candidates=[r for r in (reading,self.latest) if self._valid(r,now)]
            if self.probe_path.exists():
                probe=json.loads(self.probe_path.read_bytes())
                self.own_tokens()  # Recover usage even if the probe timed out before turn.completed.
                seen=self.own['threads'].get(probe.get('thread_id'),{})
                if _tokens(probe.get('usage')) is None and not seen.get('found'):
                    candidates=[]  # A fresh reading cannot excuse an unaccounted probe.
            if candidates:
                self.failure=None
                reset=max(r['resets_at'] for r in candidates)
                window=latest_quota_readings([r for r in candidates if r['resets_at']>=reset-RESET_JITTER_SECONDS])
                used=max(r['used_percent'] for r in window)
                observed=max(r['observed_at'] for r in window)
                if self.baseline is None or abs(self.baseline['resets_at']-reset)>RESET_JITTER_SECONDS:
                    # A new weekly window starts the count again.
                    # A reading taken before this run began (the newest one on disk can be hours old) only holds the
                    # place: the count starts again at the first reading made during the run (11-11: a run opened on a
                    # 26 % reading from the morning, saw the real 34 % twenty seconds later and was stopped for "8 points").
                    self.baseline=dict(used_percent=used,resets_at=reset,at=now,observed_at=observed,provisional=observed<self.started)
                    from .dev_agent_workspace import atomic_write,encode
                    atomic_write(self.private,'quota-baseline.json',encode(self.baseline))
                elif self.baseline.get('provisional') and observed>=self.started:
                    self.baseline=dict(used_percent=used,resets_at=reset,at=now,observed_at=observed,provisional=False,
                                       replaced_used_percent=self.baseline['used_percent'])
                    from .dev_agent_workspace import atomic_write,encode
                    atomic_write(self.private,'quota-baseline.json',encode(self.baseline))
                account_points=used-self.baseline['used_percent']
                tokens,why=self.own_tokens()
                # Without a count of its own the run falls back to the rise of the shared counter (stops early, never late).
                run_points=account_points if tokens is None else tokens/self.tokens_per_point
                source='account_rise:'+why if tokens is None else 'own_tokens'
                if tokens is None and self.probe_path.exists():
                    known=self.own['call_tokens']+sum(v.get('tokens',0) for v in self.own['threads'].values())
                    if known/self.tokens_per_point>run_points:
                        run_points=known/self.tokens_per_point;source='known_tokens:'+why
                self.capped=self.capped or (self.run_cap_points is not None and run_points>=self.run_cap_points)
                self.stopped=self.stopped or used>=self.stop_percent or self.capped
                # Usage only moves when Codex runs, and every local Codex turn writes a fresh
                # reading; an old reading (e.g. during a long execution) is used but flagged.
                sample=dict(at=now,reason=reason,used_percent=used,resets_at=reset,stop_percent=self.stop_percent,
                            run_points=run_points,run_cap_points=self.run_cap_points,run_cap_reached=self.capped,
                            run_points_source=source,run_tokens=tokens,
                            tokens_per_point=self.tokens_per_point,account_points=account_points,
                            stopped=self.stopped,observed_at=observed,stale=now-observed>WEEKLY_READING_MAX_AGE)
            else:
                self.failure=('quota_unreadable_after_probe' if self.probe_path.exists()
                              else error or 'quota_stale_or_unavailable')
                tokens,_=self.own_tokens()
                sample=dict(at=now,reason=reason,error=self.failure,stop_percent=self.stop_percent,
                            run_tokens=tokens,run_points=tokens/self.tokens_per_point if tokens is not None else None,
                            run_cap_points=self.run_cap_points)
            if self.probe_path.exists():sample['quota_probe']=json.loads(self.probe_path.read_bytes())
            self._record(sample)
            if self.failure:raise QuotaUnavailable(self.failure)
            return sample

    def check(self, reason):
        self.sample(reason)
        if self.stopped:raise ValueError('quota_stop')
