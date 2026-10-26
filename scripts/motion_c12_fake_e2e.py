"""C12 automatic origin + motion arms + cross-seed probes; no simulator/network."""
import tempfile
from pathlib import Path
from benchmarks.attention_harness.tests.motion_c12_fakes import lifecycle

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='c12-motion-e2e-') as folder:
        lifecycle(Path(folder))
        print('PASS: 29 simulated Dev motion evidence imports; automatic origin, same-scene arms, cross-seed probes/arms; no human approval claimed')
