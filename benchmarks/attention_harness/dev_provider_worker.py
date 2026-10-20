"""One paid request in a cancellable process. No retries or ledger authority."""
import argparse
import json
import os
import threading
import time
from pathlib import Path
from .parcc_client import ParccClient
from .dev_loop import immutable


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--request', type=Path, required=True)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--kind', choices=['model', 'advisor'], required=True)
    p.add_argument('--deadline', type=float, required=True)
    a = p.parse_args()
    parent = os.getppid()
    done = threading.Event()
    def watch_parent():
        while not done.wait(.1):
            if os.getppid() != parent:
                os._exit(75)  # Unknown paid work stays held by parent recovery.
    threading.Thread(target=watch_parent, daemon=True).start()
    try:
        key = os.getenv('PARCC_API_KEY') or os.getenv('LITELLM_KEY')
        path = Path('/home/truares/.litellm-key')
        if not key and path.is_file():
            key = path.read_text().strip()
        request = json.loads(a.request.read_bytes())
        seconds = a.deadline - time.time()
        if seconds <= 0:
            raise TimeoutError('provider persistent deadline expired')
        def observed(status, headers, raw):
            immutable(a.directory / (a.kind + '-provider-response.json'), dict(
                http_status=status, request_id=headers.get('x-request-id'), body=json.loads(raw)))
        client = ParccClient(api_key=key, max_attempts=1, timeout_seconds=seconds, response_observer=observed, stream=True)
        response = client.chat(**{k: request[k] for k in ('model', 'max_tokens', 'messages', 'temperature', 'reasoning_effort')})
        immutable(a.directory / (a.kind + '-output.json'), response.artifact())
    finally:
        done.set()


if __name__ == '__main__':
    main()
