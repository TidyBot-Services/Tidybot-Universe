"""Continuous file identity checks without blocking read-only physics telemetry."""
from __future__ import annotations
import threading
import time
from .identity import IdentityError


class IntegrityWatch:
    def __init__(self, verify, *, interval, max_age):
        if not 0 < interval < max_age:
            raise ValueError('Identity verification interval must be within its freshness bound')
        self.verify,self.interval,self.max_age=verify,interval,max_age
        self.failed=None;self.done=threading.Event()
        self.verify();self.last_verified=time.monotonic()
        self.thread=threading.Thread(target=self._watch,name='curobo-v2-source-identity',daemon=True)
        self.thread.start()

    def fail(self, reason):
        if self.failed is None:self.failed=str(reason)

    def check(self, *, require_fresh=True):
        if self.failed:raise IdentityError(self.failed)
        if not self.thread.is_alive()or require_fresh and time.monotonic()-self.last_verified>self.max_age:
            self.fail('Continuous V2 source verification stopped or exceeded its freshness bound')
            raise IdentityError(self.failed)

    def refresh(self):
        if self.failed:raise IdentityError(self.failed)
        self.verify();self.last_verified=time.monotonic();self.check()

    def _watch(self):
        while not self.done.wait(self.interval):
            try:self.verify()
            except Exception as exc:
                self.fail(f'{type(exc).__name__}: {exc}');return
            self.last_verified=time.monotonic()

    def close(self):
        self.done.set();self.thread.join(timeout=self.max_age)
        if self.thread.is_alive():raise RuntimeError('V2 identity verification did not terminate')
