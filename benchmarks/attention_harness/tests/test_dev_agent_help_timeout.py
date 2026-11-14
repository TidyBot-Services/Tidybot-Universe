"""Offline broker help deadlines; human/Advisor latency uses a virtual clock."""
import json
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import dev_agent_broker as module
from benchmarks.attention_harness.dev_advisor_proxy import default_config
from benchmarks.attention_harness.tests.test_dev_agent_d1 import broker, request, workspace  # noqa: F401


class VirtualCall:
    """Complete the collaborator thread after simulated latency during Event.wait."""
    def __init__(self, latency):
        self.now = 1000.
        self.latency = latency
        self.pending = None

    def clock(self):
        return self.now

    def thread(self, *, target, daemon):
        def start():
            if target.__name__ == 'invoke':
                self.pending = (self.now + self.latency, target)
            else:
                target()  # Cancellation has no latency.
        return SimpleNamespace(start=start)

    def event(self):
        clock = self

        class Event:
            done = False

            def set(self):
                self.done = True

            def is_set(self):
                return self.done

            def wait(self, seconds):
                end = clock.now + seconds
                if clock.pending and clock.pending[0] <= end:
                    clock.now, callback = clock.pending
                    clock.pending = None
                    callback()
                clock.now = end
                return self.done

        return Event()


class Helper:
    def __init__(self, config):
        self.config = config
        self.cancelled = False
        self.deadline = None

    def ask(self, payload, *, deadline):
        self.deadline = deadline
        return dict(status='delivered', delivered=True, provider='advisor', content='Move carefully.')

    def cancel(self):
        self.cancelled = True


@pytest.mark.parametrize('human_wait', [None, 0, 30, 120])
@pytest.mark.parametrize('attempts,seconds', [(2, 55), (3, 40), (1, 20)])
def test_timeout_uses_run_human_wait_and_advisor_retry_limit(workspace, human_wait, attempts, seconds):
    config = dict(advisor_proxy=dict(default_config(), max_attempts=attempts, attempt_timeout_seconds=seconds))
    if human_wait is not None:
        config['human_reply_seconds'] = human_wait
    b = broker(workspace, helper=Helper(config))
    try:
        expected = human_wait + attempts * seconds + module.HELP_TIMEOUT_MARGIN if human_wait else 120.
        assert b.timeouts['ask_help'] == expected
        assert module.TIMEOUTS['ask_help'] == 120.
    finally:
        b.close()


def test_explicit_timeout_override_is_preserved(workspace):
    helper = Helper(dict(human_reply_seconds=120, advisor_proxy=default_config()))
    b = broker(workspace, helper=helper, timeouts={'ask_help': .02})
    try:
        assert b.timeouts['ask_help'] == .02
    finally:
        b.close()


@pytest.mark.parametrize('wall_seconds,latency,delivered,elapsed', [
    (1000, 120 + 20, True, 140),
    (100, 120 + 20, False, 100),
    (1000, 300, False, 240),
])
def test_help_receipt_and_wall_deadline_with_virtual_latency(
        workspace, monkeypatch, wall_seconds, latency, delivered, elapsed):
    clock = VirtualCall(latency)
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=clock.clock))
    helper = Helper(dict(human_reply_seconds=120, advisor_proxy=default_config()))
    b = broker(workspace, helper=helper, budget=module.Budget(wall_seconds=wall_seconds))
    # Keep request dispatch synchronous; only the bounded collaborator is delayed.
    monkeypatch.setattr(b.pool, 'submit', lambda fn, *args: fn(*args))
    monkeypatch.setattr(module, 'threading', SimpleNamespace(Event=clock.event, Thread=clock.thread))
    try:
        response = b.accept(request(op='ask_help', payload={'question': 'How should I move?'}))
        assert response['status'] == 'completed' and not b.quarantined
        assert clock.now - 1000 == pytest.approx(elapsed, abs=.051)
        assert helper.cancelled is not delivered
        result = response['result']
        if delivered:
            assert result['status'] == 'delivered' and result['delivered'] is True
            assert helper.deadline == 1240.
        else:
            assert result['status'] == 'help_error' and result['error_class'] == 'TimeoutError'
        receipt = json.loads((workspace.ws/'rpc/responses/r1.json').read_text())
        assert receipt == response
    finally:
        b.close()
