"""C18: fake executor/replay, real importer and decisions; no external calls."""
import tempfile
from pathlib import Path
from benchmarks.attention_harness.tests.positive_control_fakes import run

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='positive-control-e2e-') as directory:
        root=Path(directory)
        fake,state,record=run(root/'pass')
        assert record['verdict']['decision']=='would_pass'
        assert record['cross_seed']['task_trust']['verdict']=='would be trusted for this task'
        fake,state,record=run(root/'fail',treatments=(True,)*5)
        assert record['verdict']['decision']=='would_fail'
        print('PASS: Test · not counted in results; same-scene pass/fail, transfer/neutral; no Memory promotion or source import')
