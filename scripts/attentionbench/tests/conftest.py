import os

import pytest


@pytest.fixture(autouse=True)
def _restore_workspace_registry():
    """build_planner_chain.build sets ATTENTIONBENCH_WORKSPACE for its subprocesses; keep it local to each test."""
    saved = os.environ.get('ATTENTIONBENCH_WORKSPACE')
    yield
    if saved is None:
        os.environ.pop('ATTENTIONBENCH_WORKSPACE', None)
    else:
        os.environ['ATTENTIONBENCH_WORKSPACE'] = saved
    from benchmarks.attention_harness import workspace_paths
    workspace_paths.clear_cache()
