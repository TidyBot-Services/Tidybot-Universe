"""Trusted, formal-generation Memory delivery for the agent controller."""
import hashlib
import json

from . import dev_planner_memory as memory, memory_run_notes
from .dev_agent_eval import feedback
from .dev_help import EMPTY_MEMORY
from .dev_v2_loop import prompt_memory_view


class MemoryRevoked(PermissionError):
    """A request-local refusal; it must not quarantine the agent run."""


class Memory:
    def __init__(self, store, key, workspace, config, initial_source, *, default=None, help_pricing=None, validation=None):
        self.store, self.key, self.workspace, self.config = store, key, workspace, config
        self.initial_source = initial_source
        self.default = EMPTY_MEMORY if default is None else default
        from .dev_help import HELP_PRICING
        self.help_pricing = HELP_PRICING if help_pricing is None else help_pricing
        cell = store.cell(key)
        self.settings, self.shared = memory.cell_memory(cell), memory.layered(cell)
        self.run_notes, self.family = cell.get('run_notes') is True, cell.get('memory_family') is True
        self.number, self.previous, self.service = None, None, None
        self.packet, self.generations = self.default, {}
        self.message = ''
        self.validation=None
        if validation is not None:
            from .memory_agent_validation import Delivery
            self.validation=Delivery(self,validation['current'],validation['client'],arm_record=validation['arm_record'])

    def open(self, number, previous, *, deliver_notes=True):
        directory = self.workspace.private / 'memory' / f'v{number}'
        directory.mkdir(parents=True, exist_ok=True)
        if self.validation:packet,service=self.validation.load(number,previous,directory)
        else:packet, service = memory.load_memory(self.store, self.key, number, previous,
            self.config, self.settings, directory, self.shared)
        if not self.settings and not self.validation:
            packet = self.default
        self.number, self.previous, self.packet, self.service = number, previous, packet, service
        self._binding()
        notes = memory_run_notes.deliver(self.store, self.key, number, directory) if self.run_notes and deliver_notes else []
        entries = memory_run_notes.merge(packet['entries'], notes, family=self.family) if notes else packet['entries']
        view = feedback(prompt_memory_view(dict(memory=entries)))
        self.workspace.publish(f'memory/v{number}.json', view)
        count = len(view['memory'])
        unvalidated = sum(e.get('unvalidated') is True or e.get('trusted_here') is False for e in entries)
        self.message = f'Memory for v{number}: memory/v{number}.json ({count} notes; {unvalidated} unvalidated)'
        self.generations[number] = packet
        return self.message

    def clear(self, number, previous):
        """v2 query failure: invalidate earlier grants without re-querying Service."""
        self.number,self.previous,self.packet,self.service=number,previous,dict(EMPTY_MEMORY),None
        self.generations[number]=self.packet
        self.workspace.publish(f'memory/v{number}.json',dict(memory=[]))
        self.message=f'Memory for v{number}: memory/v{number}.json (0 notes)'

    def start(self):
        versions = self.store.cell(self.key).get('versions', [])
        if versions:
            from .dev_v2_loop import checked
            last = max(versions, key=lambda row: row['version'])
            self.number, self.previous = last['version'] + 1, checked(last['source']).read_text()
        else:
            self.number, self.previous = 1, self.initial_source
        # Retain the requested generation even when the service query raises.
        return self.open(self.number, self.previous)

    def _binding(self):
        digest = hashlib.sha256(self.previous.encode()).hexdigest()
        if any(e.get('development_id') != f'{self.key}:v{self.number}' or
               e.get('source_policy_sha256') != digest for e in self.packet['entries']):
            raise PermissionError('Memory grant belongs to a different generation/source version')

    def recheck(self):
        try:
            self._binding()
            if self.service is not None:
                self.service.recheck(self.packet)
        except Exception as exc:
            # No service details, identifiers or paths enter the request response.
            raise MemoryRevoked('memory_revoked') from exc

    def journal(self, number, source_ref, feedback_ref):
        if self.validation:return
        directory = self.workspace.private / 'memory' / f'v{number}'
        packet = self.generations.get(number)
        if packet is None:
            path = directory / 'memory-input.json'
            packet = json.loads(path.read_bytes()) if path.exists() else self.default
        memory.journal_result(self.store, self.key, number, packet, source_ref, feedback_ref, directory)

    def cache(self, ledger, number, entries):
        """Legacy retrieve_memory costs one cache help; shared retrieval is free."""
        from .dev_v2_loop import immutable
        from .dev_agent_export import mirror_job
        price = self.help_pricing['cache']
        ref = immutable(self.workspace.private / 'memory' / f'cache-v{number}.json',
                        dict(provider='cache', content=entries))
        jid = f'help:cache:v{number}'
        job = ledger.reserve(jid, 'cache', {'help': price['count']})
        if job['state'] == 'reserved':
            ledger.claim(jid)
            ledger.settle(jid, dict(help=ref))
        job = next(j for j in ledger.jobs() if j['id'] == jid)
        mirror_job(self.store, self.key, number, 'cache', job,
                   dict(help_count=price['count'], help_credits=price['credits'], help_wait_seconds=0),
                   dict(help=ref, agent_request_id=jid, accounting='agent_ledger_mirror'))


def stage_provisional(store, key, version, directory, workspace, ledger):
    """Seal the prior formal interval; bookkeeping can never stop a run."""
    from pathlib import Path
    from . import memory_provisional as provisional
    from .dev_agent_export import exported
    from .dev_v2_loop import immutable, checked
    from .dev_agent_workspace import read_beneath
    try:
        directory = Path(directory)
        cell = store.cell(key)
        if (not exported(cell, version) or cell.get('validation_experiment') or cell.get('positive_control')
                or (directory / 'memory-validation-input.json').exists()):
            return None
        projected = json.loads((directory / 'public-feedback.json').read_bytes())
        memory_run_notes.outcomes(store, key, version, directory, projected)
        if version < 2:
            return None
        answer = json.loads((directory / 'help.json').read_bytes())
        v2 = answer.get('agent_policy_version') == 'agent-policy-v2'
        if v2:
            from .dev_agent_help_interval import interval_help, answer_projection, job_projection
            jobs = ledger.jobs()
            matches = interval_help(jobs, version)
            if len(matches) > 1:
                raise provisional.NotStaged('multiple_helps_in_interval')
            if not matches:
                return None
            raw_job = matches[0]
            if answer != answer_projection(raw_job['receipt'], version):
                raise provisional.NotStaged('execution_help_receipt_drift')
        if (answer.get('provider') != 'advisor' or answer.get('delivered') is not True
                or answer.get('status') != 'delivered' or answer.get('cache_hit')
                or answer.get('rejected')):
            return None
        vid = f'v{version - 1:04d}'
        if not v2:
            jobs = ledger.jobs()
        help_job = job_projection(raw_job, version) if v2 else next(j for j in jobs if j['id'] == 'help:' + vid)
        help_identity = answer['help_execution_id'] if v2 else vid
        evidence_dir = directory / 'agent-help'
        refs = dict(request_view=provisional._ref(workspace.private / 'help' / help_identity / 'request-view.json'),
                    advisor=provisional._ref(workspace.private / 'help' / help_identity / 'advisor.json'),
                    help_job=immutable(evidence_dir / 'help-job.json', help_job))
        for label, number in (('origin', version - 1), ('help', version)):
            record = json.loads((directory.parent / f'v{number}' / 'version.json').read_bytes())
            checked(record['source'])
            if provisional._ref(directory.parent / f'v{number}' / 'policy.py')['sha256'] != record['source']['sha256']:
                raise provisional.NotStaged('source_export_drift')
            refs[label + '_record'] = provisional._ref(directory.parent / f'v{number}' / 'version.json')
            job = next(j for j in cell['jobs'] if j['version'] == number and j['kind'] == 'execute' and j['state'] == 'settled')
            refs[label + '_export'] = immutable(evidence_dir / (label + '-export.json'), job)
            agent_job = next(j for j in jobs if j['id'] == job['receipt']['agent_request_id'])
            refs[label + '_execution'] = immutable(evidence_dir / (label + '-execution.json'), agent_job)
        if answer.get('system_injected') is True:
            refs['delivery'] = immutable(evidence_dir / 'proactive-grant.json', help_job['receipt'])
        else:
            responses = workspace.canonical / 'rpc/responses'
            matches = []
            for path in sorted(responses.glob('*.json')):
                value = json.loads(read_beneath(responses, path.name, 32*1024*1024))
                if (value.get('op') == 'ask_help' and value.get('status') == 'completed'
                        and (value.get('result', {}).get('help_id') == raw_job['id'] if v2 else
                             value.get('result', {}).get('version_id') == vid)
                        and value['result'].get('delivered') is True):
                    matches.append(path)
            if len(matches) != 1:
                raise provisional.NotStaged('help_delivery_missing_or_ambiguous')
            if v2:
                from .dev_agent_help_interval import delivery_projection
                delivery = json.loads(read_beneath(responses, matches[0].name, 32*1024*1024))
                refs['delivery'] = immutable(evidence_dir / 'delivery.json', delivery_projection(delivery, answer))
            else:
                refs['delivery'] = provisional._ref(matches[0])
        evidence = immutable(directory / 'agent-help-evidence.json', dict(
            schema_version='attentionbench.agent-help-evidence.v1', channel='dev_agent',
            origin_version=version - 1, help_version=version, refs=refs))
        # The importer and Service share the same byte and interval verifier.
        from .memory_auto_import import agent_evidence
        proposal_refs = dict(agent_help_evidence=evidence, help=provisional._ref(directory / 'help.json'))
        for label, number in (('origin', version - 1), ('help', version)):
            base = directory.parent / f'v{number}'
            proposal_refs[label + '_source'] = provisional._ref(base / 'policy.py')
            proposal_refs[label + '_public_feedback'] = provisional._ref(base / 'public-feedback.json')
        from attention_memory_service.agent_provisional import verify
        verify(dict(origin_version=version - 1, help_version=version, provider=answer['provider'], cell=key,
                    digests={name: ref['sha256'] for name, ref in proposal_refs.items()}),
               answer['content'], agent_evidence(dict(refs=proposal_refs)))
        memory_run_notes.create(store, key, version, directory)
        return provisional.stage(store, key, version, directory, agent_help_evidence=evidence)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, provisional.NotStaged) else 'error:' + type(exc).__name__
        try:
            with store.tx() as db:
                store.event(db, 'planner_memory_provisional_skipped', dict(cell=key, version=version, reason=reason[:200]))
            store.update_cell(key, agent_memory_staging_failure=dict(version=version, reason=reason[:200]))
        except Exception:
            pass
        return None
