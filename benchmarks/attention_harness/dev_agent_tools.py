#!/usr/bin/python3
"""Standalone stdlib file client. This file is copied into ws/docs/devtool.py."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

SCHEMA = 'attentionbench.dev-agent-request.v1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def wait(root, request_id, timeout, digest=None):
    deadline = time.monotonic() + timeout
    delay = .1
    while True:
        try:
            value = json.loads((root / 'rpc/responses' / (request_id + '.json')).read_bytes())
            if value.get('request_id') != request_id or (digest and value.get('digest') != digest):
                raise ValueError('response identity mismatch')
            if value.get('status') in ('completed', 'rejected', 'needs_review'):
                return value
        except FileNotFoundError:
            pass
        if time.monotonic() >= deadline:
            return dict(request_id=request_id, status='pending')
        time.sleep(min(delay, max(0, deadline - time.monotonic())))
        delay = min(.5, delay * 1.2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('op', choices=['submit', 'run', 'observe', 'find_objects',
        'ik_check', 'plan_dry_run', 'ask_help', 'status', 'wait'])
    parser.add_argument('id', nargs='?')
    parser.add_argument('--entry'); parser.add_argument('--reason', default='')
    parser.add_argument('--question', default=''); parser.add_argument('--no-eval', action='store_true')
    for name in ('target', 'action', 'start-joints', 'names'):
        parser.add_argument('--' + name, type=json.loads)
    parser.add_argument('--timeout', type=float, default=3000)
    parser.add_argument('--request-id')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    rid = args.id if args.op == 'wait' else args.request_id or uuid.uuid4().hex
    if not rid or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', rid): parser.error('invalid request ID')
    if not 0 <= args.timeout <= 21600: parser.error('invalid timeout')
    if args.op == 'wait':
        value = wait(root, rid, args.timeout)
    else:
        payload = {}
        if args.op in ('submit', 'run'):
            if not args.entry: parser.error('--entry required')
            if args.op == 'run' and not args.no_eval: parser.error('run requires --no-eval')
            payload = dict(entry=args.entry, reason=args.reason)
        if args.op == 'ask_help': payload = dict(question=args.question)
        for name in ('target', 'action', 'start_joints', 'names'):
            value = getattr(args, name)
            if value is not None: payload[name] = value
        request = dict(schema=SCHEMA, request_id=rid, op=args.op, payload=payload)
        data = canonical(request)
        inbox = root / 'rpc/requests'
        temp = inbox / ('.' + rid + '-' + uuid.uuid4().hex)
        with temp.open('xb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, inbox / (rid + '.json'))
        fd = os.open(inbox, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
        value = wait(root, rid, args.timeout, hashlib.sha256(data).hexdigest())
    print(json.dumps(value, sort_keys=True, allow_nan=False))
    return 0 if value['status'] == 'completed' else 2 if value['status'] == 'pending' else 1


if __name__ == '__main__':
    raise SystemExit(main())
