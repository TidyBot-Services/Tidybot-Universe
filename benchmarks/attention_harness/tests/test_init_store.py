import sqlite3

import pytest

from benchmarks.attention_harness.init_store import init_store


def test_init_store_creates_empty_store_and_artifacts(tmp_path):
    store = tmp_path / 'data' / 'attention.sqlite3'
    artifacts = tmp_path / 'episodes'
    assert init_store(store, artifacts) == artifacts
    assert artifacts.is_dir()
    with sqlite3.connect(store) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    with pytest.raises(FileExistsError, match='already exists'):
        init_store(store, artifacts)
    assert artifacts.is_dir()
