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


def v2_workspace(root):
    try:
        return '# Dev tools (agent-policy-v2; file protocol v1)' in (root/'docs/tools.md').read_text()
    except OSError:
        return False


def diagnostic_module():
    try:
        from . import dev_agent_diagnose
    except ImportError:
        import dev_agent_diagnose
    return dev_agent_diagnose


def bounded_json(value, reserve=0, full_file=None):
    """Bound the print, preserving first_failure verbatim even if it alone exceeds the cap."""
    def dump(v):
        return json.dumps(v, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    limit = max(0, PRINT_LIMIT - reserve)
    if len(dump(value)) <= limit:
        return dump(value)
    short = {k: v if k == 'first_failure' else compact(v, 300) for k, v in value.items()}
    if full_file:
        short['full_file'] = full_file
    # Retain small fields first; the complete object is always on disk.
    for key in sorted(short, key=lambda k: len(dump(short[k])), reverse=True):
        if len(dump(short)) <= limit:
            break
        if key not in ('first_failure', 'full_file') and len(dump(short[key])) > 80:
            short[key] = '[see full file]'
    if len(dump(short)) > limit:
        short = {k: v for k, v in short.items() if k in ('status', 'outcome', 'first_failure', 'full_file')}
        short['notes'] = ['Other fields: see full file; first_failure is never truncated.']
    return dump(short)


NOTES_TEMPLATE = '## eN (vN or run) — what changed / result / first failure / why / next'


def notes_check(root, eid=None):
    if eid is None:
        ids = [p.name for p in (root/'executions').glob('e*') if p.is_dir() and re.fullmatch(r'e\d+', p.name)]
        eid = max(ids, key=lambda v: int(v[1:])) if ids else None
    try:
        text = (root/'work/notes.md').read_text()
    except OSError:
        text = ''
    return dict(execution_id=eid, mentioned=bool(eid and re.search(r'(?<!\w)' + re.escape(eid) + r'(?!\w)', text)))


def completion(value, root, rid, full=False):
    """v1 and responses without a summary retain the existing output exactly."""
    if not v2_workspace(root):
        return show(value, root, rid, full)
    result = value.get('result') or {}
    eid = value.get('execution_id') or result.get('execution_id')
    if not eid:
        return show(value, root, rid, full)
    try:
        module = diagnostic_module()
        folder = module.execution_folder(root, eid)
        summary = module.read_json(folder/'summary.json', (folder,), [])
    except ValueError:
        summary = None
    if not isinstance(summary, dict):
        return show(value, root, rid, full)
    block = dict(status=value['status'])
    for key in ('outcome', 'first_failure', 'robot', 'objects_moved', 'sdk_call_count'):
        if key in summary:
            block[key] = summary[key]
    # Older v2 summaries could shorten a long failure; recover the full public row.
    if isinstance(block.get('first_failure'), str):
        trace = module.read_json(folder/'trace.json', (folder,), [])
        block.pop('first_failure')
        if isinstance(trace, list):
            first, _ = module.failure(trace, [], [])
            if first:
                block['first_failure'] = first
        if 'first_failure' not in block:
            block['notes'] = ['first_failure: older summary elided it; full trace unavailable']
    for key in ('stdout_tail', 'stderr_tail'):
        block[key] = str(summary.get(key, ''))[-600:]
    lines = [f'Full files: executions/{folder.name}/ (see summary.json full_files); devtool.py diagnose {folder.name}']
    if not notes_check(root, folder.name)['mentioned']:
        lines.append(f'Notes: add a section for {folder.name} in work/notes.md (devtool.py notes template)')
    suffix = '\n' + '\n'.join(lines)
    return bounded_json(block, len(suffix)) + suffix


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('op', choices=['submit', 'run', 'observe', 'find_objects', 'find_fixtures',
        'ik_check', 'plan_dry_run', 'ask_help', 'status', 'wait', 'diagnose', 'notes'])
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
    if args.op in ('diagnose', 'notes'):
        if not v2_workspace(root):
            parser.error(args.op + ' requires an agent-policy-v2 workspace')
        if args.op == 'notes':
            if args.id not in ('check', 'template'):
                parser.error('notes requires check or template')
            print(NOTES_TEMPLATE if args.id == 'template' else json.dumps(notes_check(root)))
        else:
            try:
                value = diagnostic_module().diagnose(root, args.id)
            except (ValueError, OSError) as exc:
                parser.error(str(exc))
            print(bounded_json(value, full_file='executions/'+diagnostic_module().execution_id(args.id)+'/diagnose.json'))
        return 0
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
    print(completion(value, root, rid, args.full) if args.op in ('run', 'submit') else show(value, root, rid, args.full))
    return 0 if value['status'] == 'completed' else 2 if value['status'] == 'pending' else 1


if __name__ == '__main__':
    raise SystemExit(main())
