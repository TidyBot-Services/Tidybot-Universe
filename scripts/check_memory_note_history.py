"""Offline, read-only note_history check; no gateway, model, store writer or run.

Run with the batch-rule Python/PYTHONPATH environment. Optional positional paths
replace the three frozen run directories. IDs are exact, including the distinct
local r- note for R1; it is never silently aliased to the imported p- note.
"""
import argparse
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from benchmarks.attention_harness.memory_monitor import join_reuse_history, note_history, project

ROOT = Path('/home/truares/Documents/Codex/2026-10-14/goal-attentionbench-robocasa365-dev-v2-home/outputs/daily-experiments')
RUNS = ['devexp-bc29f0111688ebe3a71d05d3', 'devexp-95096d760756097098f93baa', 'devexp-e37f3a14f489d0cbc9c4285b']
NOTE = 'ac75dacad58ba8e7958303ea'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', nargs='*', type=Path)
    parser.add_argument('--ids', nargs='+', default=['p-'+NOTE, 'p-'+NOTE+'-g', 'r-'+NOTE])
    parser.add_argument('--preview-payload', type=Path, help='Write offline renderer input inside the worktree')
    args = parser.parse_args()
    runs = args.runs or [ROOT/rid for rid in RUNS]
    # Recover metadata from the exact saved deliveries, without a live catalogue.
    notes = {}
    for run in runs:
        with sqlite3.connect((run/'ledger.sqlite3').resolve().as_uri()+'?mode=ro', uri=True) as db:
            cells = [json.loads(r[0]) for r in db.execute('SELECT payload FROM v2_cells')]
        for cell in cells:
            directory = Path(cell['artifact_dir'])
            if not directory.resolve().is_relative_to(run.resolve()):raise ValueError('artifacts outside run')
            for path in sorted((directory/'agent/private/memory').glob('v*/memory-input.json')):
                for entry in json.loads(path.read_bytes())['entries']:
                    if entry['memory_id'] in args.ids:notes.setdefault(entry['memory_id'], entry)
    print('Exact IDs:', ', '.join(args.ids))
    histories = []
    for run in runs:
        print('\nRUN', run.name)
        value = note_history(run, args.ids, notes=notes.values())
        histories.append(value)
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    if args.preview_payload:
        # Use the production join/enrichment, constrained to the requested runs.
        combined = dict(rows=[r for h in histories for r in h['rows']], errors=[e for h in histories for e in h['errors']])
        reuse = join_reuse_history(dict(notes=[dict(original=dict(notes['p-'+NOTE], guidance=notes['p-'+NOTE]['content']),
            generalized=dict(notes['p-'+NOTE+'-g'], guidance=notes['p-'+NOTE+'-g']['content']))], errors=[]), combined)
        class ReadOnlyBridge:
            store = SimpleNamespace(path=runs[0]/'ledger.sqlite3')
            def get(self, path):
                with sqlite3.connect(self.store.path.resolve().as_uri()+'?mode=ro', uri=True) as db:
                    experiment = json.loads(db.execute('SELECT payload FROM v2_experiments WHERE id=?', (runs[0].name,)).fetchone()[0])
                    cells = [json.loads(r[0]) for r in db.execute('SELECT payload FROM v2_cells')]
                    for cell in cells:
                        cell['versions'] = [json.loads(r[0]) for r in db.execute('SELECT payload FROM v2_versions WHERE cell=? ORDER BY version', (cell['key'],))]
                        cell['jobs'] = [dict(json.loads(r[0]), state=r[1]) for r in db.execute('SELECT payload,state FROM v2_jobs WHERE cell=?', (cell['key'],))]
                return dict(experiment, cells=cells), 'application/json'
        monitor = project(ReadOnlyBridge(), None, runs[0].name)
        args.preview_payload.write_text(json.dumps(dict(monitor=monitor, reuse=reuse), ensure_ascii=False, indent=2)+'\n')


if __name__ == '__main__':
    main()
