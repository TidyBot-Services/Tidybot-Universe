"""Immutable dev_v2 evidence for formal agent versions, never probe/no-eval work."""
import json
from pathlib import Path

from .core.dev_store import DevConflict
from .dev_ledger import encoded
from .dev_loop import public_feedback, sha
from .dev_v2_loop import immutable
from .dev_version_history import enabled as history_enabled


def reference(path):
    path = Path(path).resolve()
    return dict(uri=str(path), sha256=sha(path))


def exported(cell, version):
    """Admission is per settled formal version, not merely an agent cell flag."""
    return cell.get('dev_mode') == 'agent' and cell.get('agent_export') == 'v1' and any(
        j.get('version') == version and j.get('kind') == 'execute' and
        j.get('state') == 'settled' and j.get('receipt', {}).get('bundle')
        for j in cell.get('jobs', []))


def mirror_job(store, key, number, kind, job, actual, receipt):
    jid = f'{key}:{number}:{kind}'
    mirrored = dict(job_id=jid, cell=key, version=number, kind=kind,
        reservation=actual, actual=actual, receipt=receipt,
        **{k: job[k] for k in ('reserved_at', 'claimed_at', 'settled_at')})
    with store.tx() as db:
        old = db.execute('SELECT state,payload FROM v2_jobs WHERE id=?', (jid,)).fetchone()
        if old and (old[0] != 'settled' or json.loads(old[1]) != mirrored):
            raise DevConflict('conflicting agent execution export')
        if not old:
            db.execute('INSERT INTO v2_jobs VALUES (?,?,?,?,?,?)',
                       (jid, key, number, kind, 'settled', encoded(mirrored)))
            store.event(db, 'agent_execution_exported' if kind == 'execute' else 'agent_cache_exported', mirrored)


class Exporter:
    def __init__(self, store, key, workspace, config, ledger, *, memory_validation=None):
        self.store, self.key, self.workspace, self.config, self.ledger = store, key, workspace, config, ledger
        self.root = Path(store.cell(key)['artifact_dir'])
        self.config_ref = immutable(self.root / 'config.json', config)
        self.pending = {}
        self.memory_validation = memory_validation

    def capture(self, response, row, executor):
        if response['op'] != 'submit':
            return
        # Copy the runner references now; later no-eval work must never replace them.
        dispatched=response.get('execution_id') and response.get('execution_dispatched') is not False
        self.pending[response['request_id']] = dict(row=row,
            result=getattr(executor, 'result', None) if dispatched else None,
            episode=getattr(executor, 'episode', None) if dispatched else None)

    def settle(self, response, memory=None):
        if response['op'] != 'submit' or response['request_id'] not in self.pending:
            return None
        pending = self.pending[response['request_id']]
        row = pending['row']; number = row['version']; directory = self.root / f'v{number}'
        source = reference(self.workspace.private / 'sources' / (response['request_id'] + '.py'))
        # v000N/help.json is a live interval (manual help can update it later).
        # Publish the settled snapshot at the export's vN path and pin that copy.
        snapshot = self.workspace.canonical / f'versions/v{number}/help.json'
        help_path = snapshot if snapshot.exists() else self.workspace.canonical / 'versions' / response['version_id'] / 'help.json'
        help_value = json.loads(help_path.read_bytes())
        self.workspace.publish(f'versions/v{number}/help.json', help_value)
        # ws is writable by Dev: bind the canonical digest, never follow a
        # replacement link there while collecting trusted export references.
        help_ref = dict(uri=str(self.workspace.ws / f'versions/v{number}/help.json'), sha256=sha(snapshot))
        record = dict(version=number, source=source, help=help_ref,
            reason=row.get('reason', response.get('result', {}).get('outcome', {}).get('status', '')), formal_eligible=False,
            agent={k: v for k, v in row.items() if k not in ('version', 'formal_eligible', 'reason')})
        result, episode = pending['result'], pending['episode']
        if result and episode:
            episode = Path(episode).resolve()
            execution_root = (self.workspace.private / 'executions' / response['execution_id']).resolve()
            if not episode.is_relative_to(execution_root):
                raise DevConflict('agent episode outside formal execution')
            native = (result.get('artifacts') or {}).get('native_result')
            if native:
                record['agent']['native_result'] = native
        immutable(directory / 'version.json', record)
        self.store.version(self.key, record)
        memory_dir = self.workspace.private / 'memory' / f'v{number}'
        for name in ('memory-input.json', 'memory-run-notes-input.json'):
            path = memory_dir / name
            if path.is_file():
                immutable(directory / name, json.loads(path.read_bytes()))
        # Invalid source or failed launch has no runner episode to import.
        if not result or not episode:
            return record
        job = next(j for j in self.ledger.jobs() if j['id'] == 'request:' + response['request_id'])
        if job['state'] != 'settled':
            raise DevConflict('agent execution must settle before export')
        usage = job.get('execution_usage', {})
        if any(type(usage.get(k)) not in (int, float) for k in ('sdk_calls', 'control_steps', 'wall_seconds')):
            raise DevConflict('agent execution usage unknown')
        observations = {label: reference(episode / f'{label}_public_observation.json') for label in ('initial', 'final')}
        scene = reference(episode / 'scene-condition.json')
        trace_ref = reference(episode / 'trace.json')
        visible = history_enabled(self.config) and not self.memory_validation
        bundle = immutable(directory / 'bundle.json', dict(result, channel='dev_agent',
            artifact_dir=str(episode), config_lock=self.config_ref, dev_version=number,
            policy_id=self.store.cell(self.key)['policy_id'], formal_eligible=False,
            public_base_yaw_visible=visible, public_observations=observations, scene_condition=scene))
        projected = (dict(schema_version='attentionbench.dev-public-feedback.v1',
                          execution_status='condition_invalid', public_error='scene_contract_mismatch', action_trace=[])
                     if result['status']=='condition_invalid' else
                     public_feedback(json.loads((episode / 'trace.json').read_bytes()), base_yaw_visible=visible))
        feedback = immutable(directory / 'public-feedback.json', projected)
        actual = dict(executions=1, sdk_calls=usage['sdk_calls'], control_steps=usage['control_steps'],
                      execution_seconds=usage['wall_seconds'])
        receipt = dict(bundle=bundle, public_feedback=feedback, trace=trace_ref,
            scene_condition=scene, execution_status=result['status'], execution_usage=usage,
            agent_request_id=job['id'], accounting='agent_ledger_mirror')
        if (result.get('artifacts') or {}).get('native_result'):
            receipt['native_result']=result['artifacts']['native_result']
        # The request was already charged by AgentLedger. This is evidence, not
        # another dispatch/reservation against either set of run budgets.
        mirror_job(self.store, self.key, number, 'execute', job, actual, receipt)
        self.store.update_cell(self.key, agent_export='v1')
        if memory is not None:
            memory.journal(number, source, feedback)
        return record
