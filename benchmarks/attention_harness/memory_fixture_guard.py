"""Keep test-fixture initial states out of Memory evidence and notes."""
import json
from pathlib import Path

REASON = 'test_fixture_receipt_excluded_from_memory'


def fixture_reason(config=None, episode=None, bundle=None):
    if isinstance(config, dict) and 'test_fixture' in config:
        return REASON
    if isinstance(bundle, dict) and bundle.get('test_fixture') is True:
        return REASON
    if episode is not None:
        for name in ('result.json', 'sandbox_receipt.json', 'reset_receipt.json'):
            path = Path(episode) / name
            if path.is_file() and json.loads(path.read_bytes()).get('test_fixture'):
                return REASON
    return None


def refuse_import(store, cell, version, config=None, episode=None, bundle=None):
    reason = fixture_reason(config, episode, bundle)
    if reason:
        from .core.dev_store import DevConflict
        with store.tx() as db:
            store.event(db, 'planner_memory_offline_skipped', dict(cell=cell, version=version, reason=reason))
        raise DevConflict(reason)
