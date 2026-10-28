"""M-11 fake provider/executor, real service and worker, no live resources."""
import tempfile
from pathlib import Path
from pytest import MonkeyPatch
from benchmarks.attention_harness.tests.family_fakes import run
from benchmarks.attention_harness.tests.family_import_evidence import run as run_imports

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='family-e2e-') as tmp, MonkeyPatch.context() as patch:
        run(Path(tmp)/'fixture')
        run(Path(tmp)/'real',real=True,monkeypatch=patch)
        run_imports(Path(tmp)/'imports',patch)
    print('PASS: fixture → rewrite → 2 neutral scenes / 6 pairs → hypothetical family trust → revoke; real family promotion → demotion; 14 budgeted runs each; real family provisioning + prepare for probe and arm')
