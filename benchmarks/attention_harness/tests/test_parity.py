import pytest

from benchmarks.attention_harness.parity import compare, passed


def test_parity_report() -> None:
    native = [
        {"task_id": "cube_lift", "seed": 101, "native_success": True},
        {"task_id": "cube_stack", "seed": 101, "native_success": False},
    ]
    legacy = [dict(row) for row in native]
    report = compare(native, legacy)
    assert report["compared"] == 2
    assert report["agreement"] == 1.0
    assert report["mismatches"] == []
    assert passed(report)


def test_probe_id_is_part_of_strict_key() -> None:
    native = [
        {"task_id": "cube_lift", "seed": 101, "probe_id": "failure", "native_success": False},
        {"task_id": "cube_lift", "seed": 101, "probe_id": "success", "native_success": True},
    ]
    legacy = [dict(row) for row in native]
    report = compare(
        native,
        legacy,
        native_metadata={"versions": {"robosuite": "1"}, "predicate_ast_sha256": {"cube_lift": "a"}},
        legacy_metadata={"versions": {"robosuite": "1"}, "predicate_ast_sha256": {"cube_lift": "a"}},
    )
    assert report["compared"] == 2
    assert report["predicate_source_match"] is True
    assert report["version_match"] is True
    assert passed(report)


def test_missing_duplicate_and_non_boolean_rows_fail() -> None:
    row = {"task_id": "cube_lift", "seed": 101, "probe_id": "initial", "native_success": False}
    assert not passed(compare([row], []))
    with pytest.raises(ValueError, match="duplicate"):
        compare([row, dict(row)], [row])
    with pytest.raises(ValueError, match="Boolean"):
        compare([{**row, "native_success": 0}], [row])


def test_metadata_mismatch_fails_gate() -> None:
    row = {"task_id": "cube_lift", "seed": 101, "native_success": False}
    report = compare(
        [row],
        [row],
        native_metadata={"versions": {"robosuite": "1"}, "predicate_ast_sha256": {"cube_lift": "new"}},
        legacy_metadata={"versions": {"robosuite": "2"}, "predicate_ast_sha256": {"cube_lift": "old"}},
    )
    assert not passed(report)
