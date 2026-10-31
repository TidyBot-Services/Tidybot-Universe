"""Keep machine-specific paths out of active tracked code."""

import fnmatch
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CODE_DIRS = ('benchmarks/', 'curobo_v2/', 'eval/', 'openarm365/',
             'openarm365_dev/', 'openarm365_dev_v2/', 'openarm_bimanual/',
             'openarm_mobile/', 'openarm_robocasa/', 'scripts/', 'skill-agent-setup/')
SUFFIXES = ('.py', '.sh', '.js', '.html', '.yml', '.yaml', '.toml', '.cfg')
MARKERS = (b'/home/' + b'truares', b'Documents/' + b'Codex')


def test_no_machine_paths():
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    tracked = [name for name in tracked if name]
    exemptions = json.loads((ROOT/'config/attentionbench/machine-path-exemptions.json').read_text())
    assert all(isinstance(row, dict) and set(row) == {'path', 'reason'} and row['reason']
               for row in exemptions)
    stale = [row['path'] for row in exemptions
             if not any(fnmatch.fnmatchcase(name, row['path']) for name in tracked)]
    assert not stale, f'Stale machine-path exemptions: {stale}'
    offenders = []
    for name in tracked:
        if not name.startswith(CODE_DIRS) or not name.endswith(SUFFIXES):
            continue
        if any(fnmatch.fnmatchcase(name, row['path']) for row in exemptions):
            continue
        if any(marker in (ROOT/name).read_bytes() for marker in MARKERS):
            offenders.append(name)
    assert not offenders, f'Machine-specific paths in tracked code: {offenders}'
