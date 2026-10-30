"""Offline importer for provisional Memory proposals and their observed uses.

Opt-in only: a finished daily run launches it when
``ATTENTION_MEMORY_AUTO_IMPORT_KEYS`` names a readable key file. Keys are read
from that file and never printed, logged or stored. Every submission is
idempotent: a receipt or a terminal refusal is recorded in the ledger once.

    python -m benchmarks.attention_harness.memory_auto_import \\
        (--ledger LEDGER [...] | --root EXPERIMENTS_ROOT) \\
        --base-url URL --store-id ID --namespace NS --keys KEYS_FILE [--dry-run]
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from .dev_loop import sha
from .memory_provisional import SCHEMA, memory_id as provisional_id
from .memory_titles import note_title, unique_title

ENV = 'ATTENTION_MEMORY_AUTO_IMPORT_KEYS'
KEY_NAMES = ('ATTENTION_MEMORY_API_KEY', 'ATTENTION_MEMORY_IMPORTER_KEY')
ACTOR = 'memory-auto-import'
RECEIPT, REFUSED = 'planner_memory_provisional_receipt', 'planner_memory_provisional_refused'
REF_KEYS = ('origin_public_feedback', 'origin_source', 'help', 'help_model_input', 'help_source', 'help_public_feedback')


class Refused(Exception):
    """Terminal: the evidence or the Service refused this item; never retried."""


def read_keys(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line.startswith('export '):
            line = line[7:]
        if line and not line.startswith('#') and '=' in line:
            name, value = line.split('=', 1)
            values[name.strip()] = value.strip().strip('"\'')
    missing = [name for name in KEY_NAMES if not values.get(name)]
    if missing:
        raise ValueError('key file lacks: ' + ', '.join(missing))
    return {name: values[name] for name in KEY_NAMES}


class Redactor:
    def __init__(self, secrets=()):
        self.secrets = [s for s in secrets if s]

    def __call__(self, text):
        text = str(text)
        for secret in self.secrets:
            text = text.replace(secret, '[redacted]')
        return text


def _events(ledger):
    with sqlite3.connect('file:' + str(Path(ledger).resolve()) + '?mode=ro', uri=True) as db:
        return [(kind, json.loads(raw)) for kind, raw in
                db.execute('SELECT kind, payload FROM v2_events ORDER BY seq')]


def _done(events):
    """Item keys that already have a receipt or a terminal refusal."""
    return {event['item'] for kind, event in events if kind in (RECEIPT, REFUSED) and 'item' in event}


def _verified(ref):
    path = Path(ref['uri'])
    if not path.is_file() or sha(path) != ref['sha256']:
        raise Refused('evidence digest drift: ' + path.name)
    return path


def summary_for(guidance):
    text = ' '.join(guidance.split())
    first = re.split(r'(?<=[.!?。！？])\s', text, maxsplit=1)[0]
    if len(first) > 80:
        first = first[:79].rsplit(' ', 1)[0].rstrip(',;:') + '…'
    try:
        from attention_memory_service.note_title import validate_summary
        return validate_summary(first)
    except Exception:
        return None  # The summary is display text only; omit it when not plain.


def provisional_payload(proposal, namespace, *, taken=()):
    if proposal.get('schema_version') != SCHEMA or proposal.get('status') != 'pending_import':
        raise Refused('not a pending provisional proposal')
    agent = proposal.get('origin', {}).get('channel') == 'dev_agent'
    if ((proposal.get('dev_mode') == 'agent' or proposal.get('origin', {}).get('dev_mode') == 'agent') and not agent
            or proposal.get('provenance', {}).get('channel') == 'dev_agent' and not agent):
        raise Refused('agent_memory_auto_import_and_validation_unsupported')
    refs = proposal['refs']
    keys = tuple('agent_help_evidence' if name == 'help_model_input' and agent else name for name in REF_KEYS)
    if set(refs) != set(keys):
        raise Refused('proposal evidence refs incomplete')
    paths = {name: _verified(refs[name]) for name in keys}
    help_value = json.loads(paths['help'].read_bytes())
    guidance = proposal['help_content']
    if help_value.get('content') != guidance or help_value.get('provider') != proposal['provider']:
        raise Refused('help content differs from the proposal')
    context = {**proposal['context'], 'namespace': namespace}
    origin = dict(experiment_id=proposal['experiment_id'], cell=proposal['cell'], policy_id=proposal['policy_id'],
                  origin_version=proposal['origin_version'], help_version=proposal['help_version'],
                  provider=proposal['provider'], effective_reason=proposal['effective_reason'],
                  eval_reason_class=proposal['eval_reason_class'],
                  digests={name: refs[name]['sha256'] for name in keys})
    payload = dict(actor=ACTOR, memory_id=provisional_id(proposal['experiment_id'], proposal['cell'], proposal['help_version']),
                   context=context, guidance=guidance, failure_signature=proposal['failure_signature'], origin=origin,
                   origin_public_feedback_text=paths['origin_public_feedback'].read_bytes().decode('utf-8'),
                   help_public_feedback_text=paths['help_public_feedback'].read_bytes().decode('utf-8'),
                   title=unique_title(note_title(guidance, proposal['failure_signature']), taken, proposal['help_version']))
    if agent:
        origin['channel'] = 'dev_agent'
        payload['agent_evidence'] = agent_evidence(proposal)
        from attention_memory_service.agent_provisional import verify
        verify(origin, guidance, payload['agent_evidence'])
    if 'from_run_note' in proposal:
        payload['from_run_note'] = proposal['from_run_note']
    summary = summary_for(guidance)
    if summary:
        payload['summary'] = summary
    return payload


def agent_evidence(proposal):
    refs = proposal['refs']
    path = _verified(refs['agent_help_evidence'])
    value = json.loads(path.read_bytes())
    texts = {name: _verified(ref).read_bytes().decode('utf-8') for name, ref in value['refs'].items()}
    return dict(refs={name: _verified(ref).read_bytes().decode('utf-8') for name, ref in refs.items()},
                help=texts)


def pending(ledger):
    """Yield ``(item_key, kind, cell, version, payload_builder)`` not yet receipted."""
    agents={}
    try:
        with sqlite3.connect('file:' + str(Path(ledger).resolve()) + '?mode=ro', uri=True) as db:
            agents={key:json.loads(raw) for key,raw in db.execute('SELECT id,payload FROM v2_cells')
                    if json.loads(raw).get('dev_mode')=='agent'}
            for key,cell in agents.items():
                cell['jobs']=[dict(json.loads(raw),state=state) for state,raw in db.execute(
                    'SELECT state,payload FROM v2_jobs WHERE cell=?',(key,))]
    except sqlite3.OperationalError:
        pass  # Ledgers without v2_cells cannot hold agent cells; keep the old behaviour.
    events = _events(ledger)
    done = _done(events)
    for kind, event in events:
        if kind != 'planner_memory_offline_queued' or event.get('kind') not in ('provisional', 'provisional_outcome'):
            continue
        if event.get('cell') in agents:
            from .dev_agent_export import exported
            cell=agents[event['cell']]
            if (not exported(cell,event.get('version'))
                    or cell.get('validation_experiment') or cell.get('positive_control')
                    or (Path(cell['artifact_dir'])/f"v{event['version']}"/'memory-validation-input.json').exists()):
                raise Refused('agent_memory_auto_import_and_validation_unsupported')
            if event['kind'] == 'provisional':
                proposal = _queue_value(event['queue'])
                if (proposal.get('origin', {}).get('channel') != 'dev_agent'
                        or proposal.get('provenance', {}).get('channel') != 'dev_agent'
                        or proposal.get('cell') != event['cell'] or proposal.get('help_version') != event['version']
                        or proposal.get('experiment_id') != cell['experiment_id']):
                    raise Refused('agent_memory_auto_import_and_validation_unsupported')
                evidence = agent_evidence(proposal)
                with sqlite3.connect('file:' + str(Path(ledger).resolve()) + '?mode=ro', uri=True) as db:
                    help_job = json.loads(evidence['help']['help_job'])
                    if 'execution_job' in help_job:
                        from .dev_agent_help_interval import check_projection
                        live_jobs = [dict(json.loads(raw), id=jid, state=state) for jid, state, raw in db.execute(
                            'SELECT id,state,payload FROM v2_agent_jobs WHERE cell=? ORDER BY rowid', (event['cell'],))]
                        try:
                            check_projection(help_job, live_jobs, proposal['help_version'],
                                json.loads(evidence['refs']['help']), json.loads(evidence['help']['delivery']))
                        except Exception as exc:
                            raise Refused('evidence digest drift: agent execution help') from exc
                    for name in ('help_job', 'origin_execution', 'help_execution'):
                        sealed = json.loads(evidence['help'][name])
                        if name == 'help_job' and 'execution_job' in sealed:
                            sealed = sealed['execution_job']
                        row = db.execute('SELECT state,payload FROM v2_agent_jobs WHERE cell=? AND id=?',
                                         (event['cell'], sealed['id'])).fetchone()
                        if row is None or dict(json.loads(row[1]), id=sealed['id'], state=row[0]) != sealed:
                            raise Refused('evidence digest drift: agent ledger ' + name)
                for name in ('origin_export', 'help_export'):
                    sealed = json.loads(evidence['help'][name])
                    if sealed not in cell['jobs']:
                        raise Refused('evidence digest drift: agent execution mirror')
            with sqlite3.connect('file:' + str(Path(ledger).resolve()) + '?mode=ro', uri=True) as db:
                row = db.execute('SELECT payload FROM v2_experiments WHERE id=?', (cell['experiment_id'],)).fetchone()
            manifest = json.loads(_verified(json.loads(row[0])['manifest']).read_bytes())
            config = json.loads((Path(cell['artifact_dir']) / 'config.json').read_bytes())
            if any(obj.get(k) for obj in (manifest, manifest.get('selection', {}), config)
                   for k in ('validation_experiment', 'positive_control', 'origin_dev_lock')):
                raise Refused('agent_memory_auto_import_and_validation_unsupported')
        queue = event['queue']
        if event['kind'] == 'provisional':
            item = 'provisional:' + queue['sha256']
            if item not in done:
                yield item, 'provisional', event, queue
        else:
            for grant in _queue_value(queue).get('evidence', {}).get('grants', []):
                item = 'outcome:' + grant['grant_id']
                if item not in done:
                    yield item, 'provisional_outcome', event, dict(queue=queue, grant=grant)


def _queue_value(queue):
    return json.loads(_verified(queue).read_bytes())


def outcome_payload(target):
    value = _queue_value(target['queue'])
    feedback = _verified(value['evidence']['public_feedback'])
    text = feedback.read_bytes().decode('utf-8')
    return dict(grant_id=target['grant']['grant_id'], development_id=target['grant']['development_id'],
                public_feedback_text=text, public_feedback_sha256=sha(feedback))


def _record(ledger, kind, value):
    from .dev_ledger import DevV2Store
    store = DevV2Store(ledger)
    with store.tx() as db:
        store.event(db, kind, value)


def terminal(exc):
    """The Service refused the content itself (HTTP 409/422): never retried."""
    return isinstance(exc, ValueError) or type(exc).__name__ == 'StateConflictError'


def run(ledgers, *, client, namespace, dry_run=False, out=print, redact=Redactor()):
    """Submit every pending item once. Returns counts by outcome."""
    counts = dict(created=0, outcomes=0, refused=0, deferred=0, planned=0)
    for ledger in ledgers:
        # Replay all proposals, including receipted ones, in queue order so
        # collision suffixes remain deterministic across importer restarts.
        title_inputs, taken = {}, []
        for kind, event in _events(ledger):
            if kind != 'planner_memory_offline_queued' or event.get('kind') != 'provisional':
                continue
            if event['queue']['sha256'] in title_inputs:
                continue
            try:
                proposal = _queue_value(event['queue'])
                chosen = unique_title(note_title(proposal['help_content'], proposal['failure_signature']),
                                      taken, proposal['help_version'])
                title_inputs[event['queue']['sha256']] = tuple(taken)
                taken.append(chosen)
            except (Refused, KeyError, ValueError, OSError):
                continue
        for item, kind, event, target in pending(ledger):
            label = f"{Path(ledger).parent.name} {event['cell'][:12]} v{event['version']} {kind}"
            try:
                if kind == 'provisional':
                    proposal = _queue_value(target)
                    payload = provisional_payload(proposal, namespace, taken=title_inputs[target['sha256']])
                else:
                    payload = outcome_payload(target)
            except (Refused, KeyError, ValueError, OSError) as exc:
                reason = redact(exc)[:300]
                counts['refused'] += 1
                out(f'refused {label}: {reason}')
                if not dry_run:
                    _record(ledger, REFUSED, dict(item=item, cell=event['cell'], version=event['version'], kind=kind,
                                                  error_class=type(exc).__name__, message=reason))
                continue
            if dry_run:
                counts['planned'] += 1
                shown = payload.get('memory_id') or payload.get('grant_id')
                out(f'would submit {label}: {shown}')
                continue
            try:
                if kind == 'provisional':
                    response = client.create_provisional_note(**payload)
                else:
                    response = client.record_provisional_outcome(**payload)
            except Exception as exc:
                if not terminal(exc):
                    # Unreachable, unauthorised or server error: no receipt,
                    # the next run retries.
                    counts['deferred'] += 1
                    out(f'deferred {label}: {type(exc).__name__}: {redact(exc)[:200]}')
                    continue
                reason = redact(exc)[:300]
                counts['refused'] += 1
                out(f'refused {label}: {type(exc).__name__}: {reason}')
                _record(ledger, REFUSED, dict(item=item, cell=event['cell'], version=event['version'], kind=kind,
                                              error_class=type(exc).__name__, message=reason))
                continue
            receipt = dict(item=item, cell=event['cell'], version=event['version'], kind=kind)
            if kind == 'provisional':
                receipt.update(memory_id=response.get('memory_id'), status=response.get('status'),
                               expires_at=response.get('expires_at'))
                counts['created'] += 1
            else:
                receipt.update(grant_id=payload['grant_id'], memory_id=response.get('memory_id'),
                               signature_present=response.get('signature_present'), progress=response.get('progress'))
                counts['outcomes'] += 1
            _record(ledger, RECEIPT, receipt)
            out(f"imported {label}: {receipt.get('memory_id')} {receipt.get('status') or ''}".rstrip())
    return counts


def ledgers_for(args):
    paths = [Path(p) for p in args.ledger or []]
    if args.root:
        paths += sorted(Path(args.root).glob('devexp-*/ledger.sqlite3'))
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description='Import provisional Memory proposals (offline importer role).')
    parser.add_argument('--ledger', action='append')
    parser.add_argument('--root')
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--store-id', required=True)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--keys', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    redact = Redactor()
    try:
        ledgers = ledgers_for(args)
        if not ledgers:
            raise ValueError('no ledger selected')
        client = None
        if not args.dry_run:
            keys = read_keys(args.keys)
            redact = Redactor(keys.values())
            from .memory_service_client import MemoryServiceClient
            client = MemoryServiceClient(args.base_url, api_key=keys['ATTENTION_MEMORY_API_KEY'],
                                         importer_key=keys['ATTENTION_MEMORY_IMPORTER_KEY'], timeout=10)
            if client.store_id() != args.store_id:
                raise PermissionError('Memory Service authority identity mismatch')
        counts = run(ledgers, client=client, namespace=args.namespace, dry_run=args.dry_run,
                     out=lambda line: print(redact(line), flush=True), redact=redact)
        print('summary ' + json.dumps(counts, sort_keys=True), flush=True)
        return 0
    except Exception as exc:
        print(f'error: {type(exc).__name__}: {redact(exc)[:300]}', file=sys.stderr, flush=True)
        return 2


def launch_after_run(experiment_dir, *, environ=None, popen=subprocess.Popen):
    """Opt-in hook for a finished daily run. Never waits and never raises."""
    environ = os.environ if environ is None else environ
    try:
        keys = environ.get(ENV)
        if not keys or not os.access(keys, os.R_OK) or not Path(keys).is_file():
            return None
        directory = Path(experiment_dir)
        ledger = directory / 'ledger.sqlite3'
        with sqlite3.connect('file:' + str(ledger.resolve()) + '?mode=ro', uri=True) as db:
            experiment = json.loads(db.execute('SELECT payload FROM v2_experiments').fetchone()[0])
        manifest = json.loads(Path(experiment['manifest']['uri']).read_bytes())
        if manifest.get('dev_mode')=='agent':
            if manifest.get('validation_experiment') or manifest.get('positive_control'):return None
            from .dev_agent_export import exported
            with sqlite3.connect('file:'+str(ledger.resolve())+'?mode=ro',uri=True) as db:
                cells=[(key,json.loads(raw)) for key,raw in db.execute('SELECT id,payload FROM v2_cells')]
                eligible=False
                for key,cell in cells:
                    cell['jobs']=[dict(json.loads(raw),state=state) for state,raw in db.execute(
                        'SELECT state,payload FROM v2_jobs WHERE cell=?',(key,))]
                    eligible=eligible or any(exported(cell,j['version']) for j in cell['jobs'])
                if not eligible:return None
        settings = manifest.get('memory_authority') or (manifest.get('planner_features') or {}).get('memory')
        if not settings:
            return None
        log = (directory / 'memory-auto-import.log').open('ab')
        return popen([sys.executable, '-B', '-m', 'benchmarks.attention_harness.memory_auto_import',
                      '--ledger', str(ledger), '--base-url', settings['base_url'], '--store-id', settings['store_id'],
                      '--namespace', settings['namespace'], '--keys', keys],
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                     env={**environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    except Exception:
        return None


if __name__ == '__main__':
    raise SystemExit(main())
