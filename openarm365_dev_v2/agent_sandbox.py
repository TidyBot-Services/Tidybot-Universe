"""Agent-only policy transport: separate RPC pipe and authored output streams."""
import json
import os
from pathlib import Path
import selectors
import subprocess
import time
from openarm365_dev.sandbox import _bwrap_prefix, _json_safe, _meter_sdk_call, FormalPolicyOutcome
from openarm365_dev.policy_validation import OPERATIONS
from .agent_policy import validate_source

OUTPUT_LIMIT = 16 * 1024 * 1024


def execute_agent_policy(*, code, sdk, context, deadline, stderr_path, cancel_check=None,
                         max_sdk_calls=320, source_config=None, memory_limit_mb=512,
                         unsafe_test_transport=False):
    validate_source(code)
    if not 0 < max_sdk_calls <= 320: raise ValueError('agent SDK limit')
    if memory_limit_mb != 512: raise ValueError('agent memory limit')
    worker = Path(__file__).with_name('agent_policy_worker.py')
    stderr_path = Path(stderr_path); stdout_path = stderr_path.with_name('policy-stdout.txt')
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = [] if unsafe_test_transport else _bwrap_prefix(worker)
    protocol_read, protocol_write = os.pipe()
    command = (['/usr/bin/python3', '-I', str(worker)] if unsafe_test_transport else
               prefix + ['--preserve-fds', str(protocol_write - 2),
                                        '/usr/bin/python3', '-I', '/worker/worker.py'])
    command += [str(memory_limit_mb), str(protocol_write)]
    started = time.monotonic(); calls = 0; status = 'failed'; error = None
    try:
        with stdout_path.open('wb') as out, stderr_path.open('wb') as err:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=out, stderr=err,
                                       pass_fds=(protocol_write,), start_new_session=True)
            os.close(protocol_write); protocol_write = None
            selector = selectors.DefaultSelector(); selector.register(protocol_read, selectors.EVENT_READ)
            buffer = bytearray()
            try:
                process.stdin.write((json.dumps(dict(code=code, context=_json_safe(context))) + '\n').encode()); process.stdin.flush()
                while status != 'completed' and error is None:
                    if cancel_check and cancel_check(): status='cancelled'; error='cancelled'; break
                    if time.monotonic() >= deadline: status='timeout'; error='deadline'; break
                    if any(p.stat().st_size >= OUTPUT_LIMIT for p in (stdout_path, stderr_path)):
                        error='output_limit'; break
                    if not selector.select(.05):
                        if process.poll() is not None: error='worker_exited'
                        continue
                    chunk = os.read(protocol_read, 65536)
                    if not chunk: error='protocol_closed'; break
                    buffer.extend(chunk)
                    if len(buffer) > 8_000_000: error='protocol_limit'; break
                    while b'\n' in buffer:
                        line, _, buffer = buffer.partition(b'\n')
                        message = json.loads(line)
                        kind = message.get('kind')
                        if kind == 'done': status='completed'; break
                        if kind == 'error': error='policy_error'; break
                        group, op = message.get('group'), message.get('operation')
                        if kind != 'call' or op not in OPERATIONS.get(group, ()):
                            error='unauthorized_rpc'; break
                        try:
                            calls = _meter_sdk_call(calls, max_sdk_calls)
                            if not isinstance(message.get('args'), list) or not isinstance(message.get('kwargs'), dict):
                                raise ValueError('invalid_args')
                            result = getattr(getattr(sdk, group), op)(*message['args'], **message['kwargs'])
                            # Only trusted SDK wrappers may return recoverable refusals.
                            value = _json_safe(result) if group == 'sensors' or isinstance(result, dict) else None
                            response = dict(ok=True, value=value)
                        except Exception:
                            response = dict(ok=False, error='fatal_sdk_error'); error='fatal_sdk_error'
                        process.stdin.write((json.dumps(response, allow_nan=False) + '\n').encode()); process.stdin.flush()
                        if error: break
            except (OSError, ValueError): error='protocol_error'
            finally:
                selector.close()
                if status == 'completed':
                    try: process.wait(timeout=1)
                    except subprocess.TimeoutExpired: error='worker_cleanup_timeout'
                if process.poll() is None:
                    import signal
                    os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)
                process.stdin.close()
            if any(p.stat().st_size >= OUTPUT_LIMIT for p in (stdout_path, stderr_path)): error='output_limit'
            if error or process.returncode: status='failed' if status != 'timeout' else status
            return FormalPolicyOutcome(status, calls, error, time.monotonic()-started, process.returncode)
    finally:
        os.close(protocol_read)
        if protocol_write is not None: os.close(protocol_write)
