import threading
import time
import pytest
from curobo_v2.identity import IdentityError,digest
from curobo_v2.integrity import IntegrityWatch


def test_readonly_checks_reuse_continuous_verification_without_rehashing_every_poll():
    calls=[]
    watch=IntegrityWatch(lambda:calls.append(True),interval=1.,max_age=3.)
    try:
        for _ in range(100):watch.check()
        assert len(calls)==1
    finally:watch.close()
    with pytest.raises(IdentityError):watch.check()


def test_changed_source_stays_latched_after_file_is_restored(tmp_path):
    path=tmp_path/'locked.py';path.write_text('approved');expected=digest(path);calls=[]
    def verify():
        calls.append(True)
        if digest(path)!=expected:raise IdentityError('locked source changed')
    watch=IntegrityWatch(verify,interval=.01,max_age=1.)
    try:
        path.write_text('modified');deadline=time.monotonic()+1.
        while watch.failed is None and time.monotonic()<deadline:time.sleep(.005)
        with pytest.raises(IdentityError,match='source changed'):watch.check()
        path.write_text('approved')
        with pytest.raises(IdentityError,match='source changed'):watch.check()
    finally:watch.close()


def test_stale_or_missing_continuous_verifier_refuses_execution():
    watch=IntegrityWatch(lambda:None,interval=1.,max_age=2.)
    try:
        watch.last_verified=time.monotonic()-3.
        watch.check(require_fresh=False)  # Startup performs a separate full synchronous attestation.
        with pytest.raises(IdentityError,match='freshness'):watch.check()
    finally:watch.close()


def test_initial_identity_failure_cannot_start_a_verifier():
    def fail():raise IdentityError('not approved')
    with pytest.raises(IdentityError,match='not approved'):
        IntegrityWatch(fail,interval=.01,max_age=1.)
