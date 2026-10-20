"""One paid request in a cancellable process. No retries or ledger authority."""
import argparse
import json
import os
import signal
import threading
import time
from pathlib import Path
from .parcc_client import ParccClient
from .codex_client import CodexClient
from .dev_loop import immutable


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--request', type=Path, required=True)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--kind', choices=['model', 'advisor'], required=True)
    p.add_argument('--deadline', type=float, required=True)
    a = p.parse_args()
    parent = os.getppid()
    def interrupted(signum, frame):
        raise KeyboardInterrupt('provider interrupted')
    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    done = threading.Event()
    def watch_parent():
        while not done.wait(.1):
            if os.getppid() != parent:
                os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=watch_parent, daemon=True).start()
    try:
        request = json.loads(a.request.read_bytes())
        source = request.get('model_source', 'parcc')
        if source not in {'parcc', 'codex'}:
            raise ValueError('unknown model source')
        seconds = a.deadline - time.time()
        if seconds <= 0:
            raise TimeoutError('provider persistent deadline expired')
        def observed(status, headers, raw):
            immutable(a.directory / (a.kind + '-provider-response.json'), dict(
                http_status=status, request_id=headers.get('x-request-id'), body=json.loads(raw)))
        if source == 'codex':
            client = CodexClient(timeout_seconds=seconds, response_observer=observed)
        else:
            key = os.getenv('PARCC_API_KEY') or os.getenv('LITELLM_KEY')
            path = Path('/home/truares/.litellm-key')
            if not key and path.is_file():
                key = path.read_text().strip()
            client = ParccClient(api_key=key, max_attempts=1, timeout_seconds=seconds, response_observer=observed, stream=True)
        response = client.chat(**{k: request[k] for k in ('model', 'max_tokens', 'messages', 'temperature', 'reasoning_effort')})
        output = response.artifact()
        if source == 'codex':
            output.update(model_source=source, quota_before=client.quota_before, quota_after=client.quota_after,
                          token_usage_note='Codex input tokens include its own system prompt; not comparable with PARCC.')
        immutable(a.directory / (a.kind + '-output.json'), output)
    finally:
        done.set()
        signal.signal(signal.SIGTERM, previous_handler)


if __name__ == '__main__':
    main()
