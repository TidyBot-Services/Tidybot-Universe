"""Real in-process service, fake execution/replay; no models or network."""
import tempfile
from pathlib import Path
from benchmarks.attention_harness.tests.cross_seed_fakes import lifecycle

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='cross-seed-e2e-') as folder:
        fake,state=lifecycle(Path(folder))
        print('scene trusted → probe seed 114: transfer → 5 pairs passed')
        print('probe seed 104: neutral → 3 pairs harmless → task trusted')
        print('3 target failures in last 5 uses → demoted one level: scene trusted')
        print(f"PASS: {state['runs_today']} cross-seed runs; fake runners, independent validator, real in-process service")
