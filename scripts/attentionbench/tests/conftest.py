import os

import pytest


@pytest.fixture(autouse=True)
def _restore_environment():
    """setup, run and build_planner_chain replace the process environment; keep that local to each test."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)
    from benchmarks.attention_harness import workspace_paths
    workspace_paths.clear_cache()
