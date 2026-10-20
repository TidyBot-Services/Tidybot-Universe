"""Local Codex subscription adapter for the existing chat client boundary."""
from __future__ import annotations

import base64
import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from .codex_quota import quota_mark
from .parcc_client import ParccError, ParccResponse

MODEL = 'gpt-6-astra'
REASONING_EFFORT = 'high'


def codex_environment():
    """Only pass process settings and the home needed for Codex authentication."""
    allowed = ('HOME', 'PATH', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TERM',
               'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME')
    return {key: os.environ[key] for key in allowed if key in os.environ}


def _prompt(messages, root):
    parts = ['Answer only from the supplied material. Do not use tools or read files.']
    images = []
    for message in messages:
        role = message['role']
        content = message['content']
        parts.append(f'[{role}]')
        for item in content if isinstance(content, list) else [{'type': 'text', 'text': content}]:
            if item.get('type') == 'text':
                parts.append(item['text'])
            elif item.get('type') == 'image_url':
                url = item['image_url']['url']
                try:
                    header, encoded = url.split(',', 1)
                    suffix = {"data:image/png;base64": ".png", "data:image/jpeg;base64": ".jpg"}[header]
                    data = base64.b64decode(encoded, validate=True)
                except (ValueError, KeyError) as exc:
                    raise ParccError('Codex image input is invalid') from exc
                path = root / f'image-{len(images)+1}{suffix}'
                path.write_bytes(data)
                images.append(path)
                parts.append(f'[image {len(images)} supplied with -i]')
            else:
                raise ParccError('Codex message part is unsupported')
    return '\n'.join(parts) + '\n', images


class CodexClient:
    def __init__(self, *, timeout_seconds=180.0, response_observer=None, runner=None):
        self.timeout_seconds = timeout_seconds
        self.response_observer = response_observer
        self.runner = runner or subprocess.Popen
        self.quota_before = None
        self.quota_after = None

    def chat(self, *, model, messages, max_tokens, temperature=0.0, reasoning_effort=REASONING_EFFORT):
        if model != MODEL:
            raise ParccError('Unsupported Codex model')
        if reasoning_effort != REASONING_EFFORT:
            raise ParccError('Unsupported Codex reasoning effort')
        started = time.monotonic()
        deadline = started + self.timeout_seconds
        self.quota_before = quota_mark()
        with tempfile.TemporaryDirectory(prefix='attention-codex-') as directory:
            root = Path(directory)
            prompt, images = _prompt(messages, root)
            argv = ['codex', 'exec', '--json', '--ephemeral', '--skip-git-repo-check',
                    '-s', 'read-only', '-m', model, '-c',
                    f'model_reasoning_effort={reasoning_effort}']
            for path in images:
                argv.extend(('-i', str(path)))
            argv.append('-')
            with (root / 'stdout.jsonl').open('w+b') as stdout, (root / 'stderr.log').open('w+b') as stderr:
                try:
                    process = self.runner(argv, cwd=root, env=codex_environment(), stdin=subprocess.PIPE,
                                          stdout=stdout, stderr=stderr, start_new_session=True)
                except OSError as exc:
                    raise ParccError('Codex executable unavailable') from exc
                try:
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise subprocess.TimeoutExpired(argv,self.timeout_seconds)
                    process.communicate(prompt.encode(), timeout=remaining)
                except subprocess.TimeoutExpired as exc:
                    self._kill(process)
                    raise ParccError('Codex request timed out') from exc
                except BaseException:
                    self._kill(process)
                    raise
                if process.returncode:
                    raise ParccError('Codex request failed')
                stdout.seek(0)
                content = None
                raw_usage = None
                for line in stdout:
                    try:
                        event = json.loads(line)
                    except ValueError as exc:
                        raise ParccError('Codex returned invalid events') from exc
                    if not isinstance(event, dict):
                        raise ParccError('Codex returned invalid events')
                    if event.get('type') == 'error' or event.get('type') == 'turn.failed':
                        raise ParccError('Codex returned an error')
                    item=event.get('item')
                    if event.get('type') == 'item.completed' and isinstance(item,dict) and item.get('type') == 'agent_message':
                        content = item.get('text')
                    if event.get('type') == 'turn.completed':
                        raw_usage = event.get('usage')
            if not isinstance(content, str) or not content.strip():
                raise ParccError('Codex returned no final message')
            if not isinstance(raw_usage, dict) or any(type(raw_usage.get(k)) is not int or raw_usage[k] < 0 for k in ('input_tokens', 'output_tokens')):
                raise ParccError('Codex returned no usable token usage')
            usage = {'prompt_tokens': raw_usage['input_tokens'], 'completion_tokens': raw_usage['output_tokens'],
                     'total_tokens': raw_usage['input_tokens'] + raw_usage['output_tokens'], 'codex_usage': raw_usage}
            body = {'id': None, 'object': 'chat.completion', 'model': model,
                    'choices': [{'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}],
                    'usage': usage}
            if self.response_observer is not None:
                self.response_observer(200, {}, json.dumps(body).encode())
            self.quota_after = quota_mark()
            return ParccResponse(model, content, None, round(time.monotonic()-started, 3), 1, usage, finish_reason='stop')

    @staticmethod
    def _kill(process):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
