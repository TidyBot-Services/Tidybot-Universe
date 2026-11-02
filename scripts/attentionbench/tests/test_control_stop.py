import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from scripts.attentionbench import control


@pytest.fixture
def worker(tmp_path):
    children = []

    def make(ignore_term=False):
        code = ('import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)'
                if ignore_term else 'import time; time.sleep(600)')
        child = subprocess.Popen([sys.executable, '-c', code], start_new_session=True)
        children.append(child)
        exp = tmp_path / 'data/daily-experiments' / ('devexp-' + 'a' * 24)
        exp.mkdir(parents=True, exist_ok=True)
        (exp / f'scheduler-process-{child.pid}.json').write_text(json.dumps(control.process_identity(child.pid)))
        return child, exp

    yield make
    for child in children:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
        child.wait()


def test_cancel(worker, tmp_path, monkeypatch):
    child, exp = worker()
    calls = []
    monkeypatch.setattr(control, 'health', lambda _: True)
    monkeypatch.setattr(control, 'control_token', lambda _: 'token')

    def cancel(url, token, experiment):
        calls.append((url, token, experiment))
        os.killpg(child.pid, signal.SIGTERM)
        return '{}'

    monkeypatch.setattr(control, 'cancel_worker', cancel)
    assert control.stop_workers(tmp_path, 8769, 2) == 1
    assert len(calls) == 1 and calls[0][1:] == ('token', exp.name)
    child.wait(timeout=2)


def test_sigterm_fallback(worker, tmp_path, monkeypatch):
    child, _ = worker()
    monkeypatch.setattr(control, 'health', lambda _: False)
    assert control.stop_workers(tmp_path, 8769, 0) == 1
    child.wait(timeout=2)


def test_identity_mismatch(worker, tmp_path):
    child, exp = worker()
    receipt = exp / f'scheduler-process-{child.pid}.json'
    identity = json.loads(receipt.read_text())
    identity['start_ticks'] = 'wrong'
    receipt.write_text(json.dumps(identity))
    assert control.stop_workers(tmp_path, 8769, 0) == 0
    assert child.poll() is None


def test_unkillable_preserves_services(worker, tmp_path, monkeypatch):
    child, _ = worker(ignore_term=True)
    time.sleep(.1)
    run = tmp_path / 'data/run'
    run.mkdir(parents=True)
    for name in ('ui', 'memory'):
        (run / f'{name}.pid').write_text('1')
    monkeypatch.setattr(control, 'health', lambda _: False)
    monkeypatch.setattr(sys, 'argv', ['control.py', 'stop', '--workspace', str(tmp_path), '--worker-timeout', '0'])
    with pytest.raises(RuntimeError, match=str(child.pid)):
        control.main()
    assert all((run / f'{name}.pid').exists() for name in ('ui', 'memory'))


def test_lock_released_after_worker_stop(worker, tmp_path, monkeypatch):
    child, _ = worker()
    lock = tmp_path / 'sim.lock'
    with lock.open('w') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        monkeypatch.setattr(control, 'health', lambda _: False)
        assert control.stop_workers(tmp_path, 8769, 0) == 1
        child.wait(timeout=2)
    probe = subprocess.run([sys.executable, '-c',
        'import fcntl,sys; f=open(sys.argv[1]); fcntl.flock(f, fcntl.LOCK_EX|fcntl.LOCK_NB)', str(lock)])
    assert probe.returncode == 0


def test_identity_matches_harness_record(worker):
    from benchmarks.attention_harness.dev_loop import process_identity
    child, _ = worker()
    assert control.process_identity(child.pid) == process_identity(child.pid)


def test_cancel_request_sends_json_body():
    import http.server
    import threading
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            seen.append((self.path, self.headers['Content-Type'], self.headers['X-Attention-Token'], body))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"state": "cancelled"}')

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        reply = control.cancel_worker(f'http://127.0.0.1:{server.server_port}', 'tok', 'devexp-x')
    finally:
        server.shutdown()
    assert reply == '{"state": "cancelled"}'
    assert seen == [('/api/dev-v2/experiments/devexp-x/cancel', 'application/json', 'tok', b'{}')]
