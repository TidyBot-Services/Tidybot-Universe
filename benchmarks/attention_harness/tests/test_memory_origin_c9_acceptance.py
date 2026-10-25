"""Run the existing fake scripts and browser-unit suites through pytest."""
import runpy
import subprocess
from pathlib import Path

import pytest
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.usefixtures('r3_deployment')
@pytest.mark.parametrize('script', ['autovalidate_fake_e2e.py', 'cross_seed_fake_e2e.py'])
def test_fake_script(script):
    runpy.run_path(str(ROOT/'scripts'/script), run_name='__main__')


def test_memory_ui_suites():
    folder = Path(__file__).parent
    names = ['test_memory_autovalidate_ui.cjs', 'test_memory_idle_gate_c8_ui.cjs',
             'test_memory_monitor_ui.cjs', 'test_memory_monitor_runtime.cjs',
             'test_dev_memory_lifecycle_ui.cjs', 'test_memory_allmodes.cjs',
             'test_daily_memory_panel.cjs', 'test_codex_quota_ui.cjs',
             'test_memory_origin_c9_ui.cjs']
    result = subprocess.run(['node', '--test', *[str(folder/name) for name in names]],
                            cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
