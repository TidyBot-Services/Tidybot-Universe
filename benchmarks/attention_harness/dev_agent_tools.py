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


PRINT_LIMIT = 4000  # characters; the full response is always kept in rpc/responses/<id>.json


def compact(value, limit=800):
    """Elide long arrays/strings; keep every short field (status, reasons, margins, clearances)."""
    text = json.dumps(value, sort_keys=True, allow_nan=False)
    if len(text) <= limit:
        return value
    if isinstance(value, dict):
        return {k: compact(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        ends = [v for v in (value[:1] + value[-1:]) if len(json.dumps(v, allow_nan=False)) <= 300]
        return {'elided': f'list of {len(value)} items, {len(text)} chars',
                **({'first': ends[0], 'last': ends[-1]} if len(ends) == 2 else {})}
    if isinstance(value, str):
        return value[:600] + f'... [{len(value)} chars]'
    return value


def show(value, root, rid, full=False):
    text = json.dumps(value, sort_keys=True, allow_nan=False)
    path = root / 'rpc/responses' / (rid + '.json')
    if full or len(text) <= PRINT_LIMIT or not path.is_file():
        return text
    short = compact(value)
    if len(json.dumps(short, allow_nan=False)) > PRINT_LIMIT:
        short = {k: v if len(json.dumps(v, allow_nan=False)) <= 300 else f'[{len(json.dumps(v, allow_nan=False))} chars]'
                 for k, v in value.items()}
    short = dict(short, printed='short', full_chars=len(text),
                 full_response='rpc/responses/' + rid + '.json (or rerun: devtool.py wait ' + rid + ' --full)')
    return json.dumps(short, sort_keys=True, allow_nan=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('op', choices=['submit', 'run', 'observe', 'find_objects', 'find_fixtures',
        'ik_check', 'plan_dry_run', 'ask_help', 'status', 'wait'])
    parser.add_argument('id', nargs='?')
    parser.add_argument('--entry'); parser.add_argument('--reason', default='')
    parser.add_argument('--summary')
    parser.add_argument('--question', default=''); parser.add_argument('--no-eval', action='store_true')
    for name in ('target', 'action', 'start-joints', 'base-offset', 'names'):
        parser.add_argument('--' + name, type=json.loads)
    parser.add_argument('--timeout', type=float, default=3000)
    parser.add_argument('--request-id')
    parser.add_argument('--full', action='store_true', help='print the whole response (default: long arrays elided)')
    args = parser.parse_args()
    if args.op == 'submit' and (not args.summary or not args.summary.strip() or len(args.summary) > 80 or '\n' in args.summary or '\r' in args.summary):
        parser.error('submit requires --summary (1–80 characters, one line)')
    root = Path(__file__).resolve().parents[1]
    rid = args.id if args.op == 'wait' else args.request_id or uuid.uuid4().hex
    if not rid or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', rid): parser.error('invalid request ID')
    if not 0 <= args.timeout <= 21600: parser.error('invalid timeout')
    if args.op == 'wait':
        value = wait(root, rid, args.timeout)
    else:
        payload = {}
        if args.op in ('submit', 'run'):
            if args.op == 'run' and not args.no_eval: parser.error('run requires --no-eval')
            payload = dict(reason=args.reason)
            if args.entry:payload['entry']=args.entry
            if args.op == 'submit':payload['summary']=args.summary.strip()
        if args.op == 'ask_help': payload = dict(question=args.question)
        for name in ('target', 'action', 'start_joints', 'base_offset', 'names'):
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
    print(show(value, root, rid, args.full))
    return 0 if value['status'] == 'completed' else 2 if value['status'] == 'pending' else 1


if __name__ == '__main__':
    raise SystemExit(main())
