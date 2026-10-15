"""Old Desktop paths are found in the workspace without recreating them on the Desktop."""

from __future__ import annotations

import json

import pytest

from benchmarks.attention_harness import legacy_paths
from benchmarks.attention_harness.legacy_paths import DESKTOP, resolve, resolve_tree


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "Workspace"
    for folder in ("development", "experiments"):
        (root / folder).mkdir(parents=True)
    (root / "development" / "service_a").mkdir()
    (root / "development" / "service_a" / "file.txt").write_text("a")
    (root / "experiments" / "run-1").mkdir()
    (root / "experiments" / "run-2" / "deep").mkdir(parents=True)
    (root / "path-map.tsv").write_text(
        "# step1\n"
        f"{DESKTOP}/AttentionBench/实验记录/run-1\t{root}/experiments/run-1\tmove\n"
        f"{DESKTOP}/stray\t{root}/development/stray\tunlink-compat-link\n")
    monkeypatch.setenv(legacy_paths.WORKSPACE_ENV, str(root))
    legacy_paths.clear_cache()
    yield root
    legacy_paths.clear_cache()


def test_existing_paths_are_returned_unchanged(tmp_path, workspace):
    real = tmp_path / "somewhere"
    real.mkdir()
    assert resolve(real) == real


def test_a_moved_repository_is_found_by_name(workspace):
    assert resolve(DESKTOP / "service_a") == workspace / "development" / "service_a"
    assert resolve(DESKTOP / "service_a" / "file.txt").read_text() == "a"
    assert resolve(DESKTOP / "run-2" / "deep") == workspace / "experiments" / "run-2" / "deep"


def test_the_path_map_wins_for_paths_that_changed_shape(workspace):
    old = DESKTOP / "AttentionBench" / "实验记录" / "run-1"
    assert resolve(old) == workspace / "experiments" / "run-1"


def test_only_move_rows_of_the_map_are_used(workspace):
    assert resolve(DESKTOP / "stray") == DESKTOP / "stray"


def test_unknown_and_foreign_paths_stay_as_they_are(workspace, tmp_path):
    assert resolve(DESKTOP / "nowhere") == DESKTOP / "nowhere"
    foreign = tmp_path / "elsewhere" / "x"
    assert resolve(foreign) == foreign


def test_without_a_workspace_nothing_is_rewritten(monkeypatch, tmp_path):
    monkeypatch.setenv(legacy_paths.WORKSPACE_ENV, str(tmp_path / "missing"))
    legacy_paths.clear_cache()
    assert resolve(DESKTOP / "service_a") == DESKTOP / "service_a"
    legacy_paths.clear_cache()


def test_resolve_tree_changes_only_missing_desktop_paths(workspace):
    value = {"root": str(DESKTOP / "service_a"), "hash": "ab" * 32, "missing": str(DESKTOP / "nowhere"),
             "nested": [{"p": str(DESKTOP / "run-2" / "deep")}], "n": 3, "flag": True}
    result = resolve_tree(value)
    assert result["root"] == str(workspace / "development" / "service_a")
    assert result["nested"][0]["p"] == str(workspace / "experiments" / "run-2" / "deep")
    assert result["hash"] == value["hash"] and result["missing"] == value["missing"]
    assert result["n"] == 3 and result["flag"] is True
    assert value["root"] == str(DESKTOP / "service_a")  # the input is not edited


def test_a_catalog_written_with_old_paths_loads_from_the_new_home(workspace, tmp_path, monkeypatch):
    """The real loader accepts old roots; the catalog file keeps what it recorded."""
    from benchmarks.attention_harness.ui_launch import load_catalog
    import benchmarks.attention_harness.ui_launch as ui_launch

    seen = {}

    def fake(profile):
        seen["root"] = profile["service_source_root"]

    monkeypatch.setattr(ui_launch, "resolve_tree", lambda item: (
        fake(resolve_tree(item)) or resolve_tree(item)))
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"profiles": [{"id": "x", "suite": "robosuite", "task": "nope",
                                                  "service_source_root": str(DESKTOP / "service_a")}]}))
    with pytest.raises(ValueError):  # the profile is deliberately incomplete; only the root matters here
        load_catalog(catalog)
    assert seen["root"] == str(workspace / "development" / "service_a")
    assert "service_a" in catalog.read_text() and str(workspace) not in catalog.read_text()
