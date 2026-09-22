from benchmarks.attention_harness.parity import compare


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
