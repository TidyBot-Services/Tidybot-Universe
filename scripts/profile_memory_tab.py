"""Offline Memory timings on real evidence; all SQLite access is mode=ro.

Gateway/catalogue inputs are frozen from the saved R2/R3 delivery packets, so
these numbers measure projection CPU/I/O and exclude live service/network time.
"""
import argparse
import cProfile
import copy
import importlib.util
import inspect
import json
from pathlib import Path
import pstats
import sqlite3
import time
from contextlib import contextmanager

from benchmarks.attention_harness.dev_ledger import DevV2Store

ROOT = Path.home()/'Documents/Codex/2026-10-14/goal-attentionbench-robocasa365-dev-v2-home/outputs/daily-experiments'
RUN = 'devexp-bc29f0111688ebe3a71d05d3'


def connect(path):
    return sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)


class ReadOnlyStore(DevV2Store):
    def __init__(self, path):
        # Bypass the writer's schema/goal initialization entirely.
        self.path = path

    @contextmanager
    def tx(self):
        db = connect(self.path)
        try:yield db
        finally:db.close()


class Bridge:
    root = ROOT
    store = ReadOnlyStore(ROOT/RUN/'ledger.sqlite3')

    def get(self, path):
        # Identical snapshot and public cleaning used by DailyDevV2.get, with
        # a mode=ro transaction adapter instead of its writable store factory.
        from benchmarks.attention_harness.dev_experiment import snapshot
        from benchmarks.attention_harness.dev_agent_view import clean
        value = snapshot(self.store, RUN)
        if value['selection'].get('dev_mode') == 'agent':value = clean(value)
        return value, 'application/json'


def frozen_notes():
    notes = []
    for rid in ('devexp-95096d760756097098f93baa', 'devexp-e37f3a14f489d0cbc9c4285b'):
        with connect(ROOT/rid/'ledger.sqlite3') as db:
            cell = json.loads(db.execute('SELECT payload FROM v2_cells').fetchone()[0])
        packet = Path(cell['artifact_dir'])/'agent/private/memory/v1/memory-input.json'
        notes.extend(json.loads(packet.read_bytes())['entries'])
    return notes


class Memory:
    def __init__(self):
        self.notes = frozen_notes()
        self.gateway = self

    def dev_catalogue(self):return copy.deepcopy(self.notes)
    def list_dev_v2_memories(self):return self.dev_catalogue()
    def reuse_notes_list(self):
        return dict(notes=[dict(original=copy.deepcopy(self.notes[0]), generalized=copy.deepcopy(self.notes[1]))], errors=[])
    def family_list(self):return dict(fixtures=[], candidates=[], events=[])
    def validation_queue(self):return []
    def memory_events(self, **kwargs):return []
    def cross_seed_queue(self):return []
    def cross_seed_status(self, **kwargs):return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', type=Path)
    parser.add_argument('--repeats', type=int, default=4)
    parser.add_argument('--profile', action='store_true')
    args = parser.parse_args()
    if args.module:
        spec = importlib.util.spec_from_file_location('benchmarks.attention_harness._perf_snapshot', args.module)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        from benchmarks.attention_harness import memory_monitor as module
    memory = Memory()
    print('Runs:', len(list(ROOT.glob('devexp-*'))), 'notes:', [n['memory_id'] for n in memory.notes], flush=True)
    checks_args = dict(gateway=memory)
    if 'experiments_root' in inspect.signature(module.automatic_checks).parameters:
        checks_args['experiments_root'] = ROOT
    for name, call in (
        ('automatic_checks', lambda: module.automatic_checks(**checks_args)),
        ('project', lambda: module.project(Bridge(), memory, RUN)),
    ):
        def clear():
            # Application cold caches; never flush the host's filesystem cache.
            for key, value in vars(module).items():
                if key in ('_USAGE',) or key.startswith('_HISTORY_') and hasattr(value, 'clear'):
                    value.clear()
        if args.profile:
            clear()
            profile = cProfile.Profile()
            profile.runcall(call)
            print(name, 'cold cProfile', flush=True)
            pstats.Stats(profile).strip_dirs().sort_stats('cumulative').print_stats(22)
        clear()
        for index in range(args.repeats):
            start = time.perf_counter()
            value = call()
            print(name, 'cold' if index == 0 else f'warm-{index}', round(time.perf_counter()-start, 6),
                  'history_errors', len(value.get('history_errors', value.get('reuse', {}).get('errors', []))), flush=True)
        if args.profile:
            profile = cProfile.Profile()
            profile.runcall(call)
            print(name, 'warm cProfile', flush=True)
            pstats.Stats(profile).strip_dirs().sort_stats('cumulative').print_stats(22)


if __name__ == '__main__':main()
