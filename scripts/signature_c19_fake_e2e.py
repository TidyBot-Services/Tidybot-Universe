"""C19: all arms reject planning, but only the target reason counts."""
import tempfile
from pathlib import Path
from benchmarks.attention_harness.tests.signature_c19_fakes import lifecycle

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='signature-c19-e2e-') as directory:
        lifecycle(Path(directory))
        print('PASS: C19 sealed three-key origin/plan, 3/5 target controls, 0/5 target treatments, would_pass')
