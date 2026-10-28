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
    def __init__(self, store, key, workspace, config, initial_source, *, default=None, help_pricing=None):
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

    def open(self, number, previous):
        directory = self.workspace.private / 'memory' / f'v{number}'
        directory.mkdir(parents=True, exist_ok=True)
        packet, service = memory.load_memory(self.store, self.key, number, previous,
            self.config, self.settings, directory, self.shared)
        if not self.settings:
            packet = self.default
        self.number, self.previous, self.packet, self.service = number, previous, packet, service
        self._binding()
        notes = memory_run_notes.deliver(self.store, self.key, number, directory) if self.run_notes else []
        entries = memory_run_notes.merge(packet['entries'], notes, family=self.family) if notes else packet['entries']
        view = feedback(prompt_memory_view(dict(memory=entries)))
        self.workspace.publish(f'memory/v{number}.json', view)
        count = len(view['memory'])
        unvalidated = sum(e.get('unvalidated') is True or e.get('trusted_here') is False for e in entries)
        self.message = f'Memory for v{number}: memory/v{number}.json ({count} notes; {unvalidated} unvalidated)'
        self.generations[number] = packet
        return self.message

    def start(self):
        versions = self.store.cell(self.key).get('versions', [])
        if versions:
            from .dev_v2_loop import checked
            last = max(versions, key=lambda row: row['version'])
            return self.open(last['version'] + 1, checked(last['source']).read_text())
        return self.open(1, self.initial_source)

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
