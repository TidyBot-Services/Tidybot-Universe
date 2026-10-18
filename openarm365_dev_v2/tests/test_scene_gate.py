"""Experiment-mode scene gate, seed-keyed scene contract and held-out seed registration. No simulator, no model."""
import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.dev_experiment import create, verify_manifest
from benchmarks.attention_harness.dev_loop import immutable
from benchmarks.attention_harness.dev_tasks import HELD_OUT_SEEDS, SEEDS, validate_selection
from openarm365_dev_v2.scene_contract import SCHEMA_V2, fingerprint, validate
from openarm365_dev_v2.dev_config import validate_dev_config

pytestmark = [pytest.mark.scene_gate, pytest.mark.eval_gate]

STATE = {'robot': {'x': [1.0]}, 'objects': {'obj_pos': [0.5, 0.25, 1.0]}, 'fixtures': {}}


def contract(tmp_path, seeds=(101, 102), tasks=('open_cabinet',), state=STATE):
    entries = {t: {'seeds': {str(s): {'normalized_sha256': fingerprint(state, True)} for s in seeds}} for t in tasks}
    return immutable(tmp_path / 'contract-v2.json', {'schema': SCHEMA_V2, 'tolerance': 1e-6, 'tasks': entries})


def selection(**kw):
    base = dict(request_key='scene-gate-test', tasks=['open_cabinet'], seeds=[101], policies=['autonomous'],
                target_depth=3, target_valid_executions=3, max_versions=5, help_budget=2, coverage='strict_full_depth',
                eval_agent={'enabled': True, 'model': 'parcc/Qwen'})
    base.update(kw)
    return base


def new(tmp_path, **kw):
    return create(DevV2Store(tmp_path / 's.sqlite'), tmp_path / 'a', selection(**kw))


def test_experiment_without_reproducible_reset_is_refused(tmp_path):
    with pytest.raises(ValueError, match='reproducible_reset'):
        new(tmp_path, scene_contract=contract(tmp_path))


@pytest.mark.parametrize('value', [False, 1, 'true', None])
def test_reproducible_reset_must_be_literally_true(tmp_path, value):
    with pytest.raises(ValueError):
        new(tmp_path, reproducible_reset=value, scene_contract=contract(tmp_path))


def test_experiment_without_scene_contract_is_refused(tmp_path):
    with pytest.raises(ValueError, match='scene_contract'):
        new(tmp_path, reproducible_reset=True)


def test_contract_must_cover_every_selected_task_and_seed(tmp_path):
    ref = contract(tmp_path, seeds=(101,))
    with pytest.raises(ValueError, match='seed 102'):
        new(tmp_path, reproducible_reset=True, scene_contract=ref, seeds=[101, 102])
    with pytest.raises(ValueError, match='pick_place_counter_to_sink'):
        new(tmp_path, reproducible_reset=True, scene_contract=ref, tasks=['open_cabinet', 'pick_place_counter_to_sink'])


def test_covered_experiment_carries_reset_seed_and_contract_into_every_config(tmp_path):
    ref = contract(tmp_path)
    e = new(tmp_path, reproducible_reset=True, scene_contract=ref, seeds=[101, 102])
    for seed in (101, 102):
        config = verify_manifest(e)['configs'][f'open_cabinet:{seed}']
        assert config['scene_reset_seed'] == seed and config['scene_contract'] == ref
        assert validate_dev_config(config)['scene_contract'] == ref


def test_diagnostic_mode_is_exempt(tmp_path):
    from benchmarks.attention_harness.dev_configuration import require_scene_gate
    require_scene_gate('diagnostic', {}, ['open_cabinet'], [101])
    require_scene_gate('interactive', {}, ['open_cabinet'], [101])


def test_legacy_independent_configuration_is_not_covered_by_the_gate():
    # The v3 selection gate still exempts this schema; new legacy experiment
    # manifests bind the registered reset and contract during materialization.
    from benchmarks.attention_harness.dev_configuration import require_scene_gate
    require_scene_gate('experiment', {'configuration': {}}, ['open_cabinet'], [101])


def test_main_bridge_selection_gets_the_registered_contract_and_uncovered_seeds_are_refused(tmp_path):
    from openarm365_dev_v2.scene_contract import registered_contract_ref
    ref = registered_contract_ref()
    e = new(tmp_path, reproducible_reset=True, scene_contract=ref, seeds=[101, 103],
            tasks=['open_cabinet', 'pick_place_counter_to_sink'])
    assert verify_manifest(e)['configs']['pick_place_counter_to_sink:103']['scene_contract'] == ref
    with pytest.raises(ValueError, match='seed 104'):
        new(tmp_path / 'x', reproducible_reset=True, scene_contract=ref, seeds=[104])


def test_v2_contract_matches_only_the_registered_state_for_the_seed(tmp_path):
    ref = contract(tmp_path)
    assert validate(ref, 'open_cabinet', STATE, 101)['status'] == 'verified'
    moved = json.loads(json.dumps(STATE))
    moved['objects']['obj_pos'][1] = -0.25
    result = validate(ref, 'open_cabinet', moved, 101)
    assert not result['matched'] and result['status'] == 'scene_contract_mismatch'
    with pytest.raises(ValueError, match='requires the configuration seed'):
        validate(ref, 'open_cabinet', STATE)
    with pytest.raises(ValueError, match='no reference'):
        validate(ref, 'open_cabinet', STATE, 103)


def test_v2_contract_digest_drift_is_refused(tmp_path):
    ref = contract(tmp_path)
    Path(ref['uri']).chmod(0o644)
    Path(ref['uri']).write_text('{}')
    with pytest.raises(ValueError, match='digest'):
        validate(ref, 'open_cabinet', STATE, 101)


def test_held_out_seeds_are_disjoint_registered_and_unselectable(tmp_path):
    assert HELD_OUT_SEEDS == tuple(range(1001, 1011)) and not set(HELD_OUT_SEEDS) & set(SEEDS)
    for seed in HELD_OUT_SEEDS:
        with pytest.raises(ValueError, match='held-out'):
            validate_selection('open_cabinet', seed)
    with pytest.raises(ValueError, match='held-out'):
        new(tmp_path, reproducible_reset=True, scene_contract=contract(tmp_path), seeds=[1001])
    assert validate_selection('open_cabinet', 101).task_id == 'open_cabinet'


def test_registered_contract_covers_dev_seeds_101_to_103_for_both_tasks():
    from openarm365_dev_v2.scene_contract import load_contract, registered_contract_ref, seed_entry
    ref = registered_contract_ref()
    assert Path(ref['uri']).name == 'dev-seeds-101-103-v2-20261019-kp1500.json'
    assert ref['sha256'] == hashlib.sha256(Path(ref['uri']).read_bytes()).hexdigest()
    contract = load_contract(ref)
    assert contract['tolerance'] == 1e-6
    assert contract['provenance']['controller_kp'] == 1500
    assert contract['provenance']['supersedes']['sha256'] == '44a386e908fd94a5bef2359fac364ab047d4c97940050dc17895245bbac7fd63'
    for task in ('open_cabinet', 'pick_place_counter_to_sink'):
        for seed in (101, 102, 103):
            entry = seed_entry(contract, task, seed)
            assert entry['fresh_processes'] >= 20 and len(entry['normalized_sha256']) == 64
        with pytest.raises(ValueError, match='no reference'):
            seed_entry(contract, task, 104)
        assert not set(map(int, contract['tasks'][task]['seeds'])) & set(HELD_OUT_SEEDS)


def test_registered_contract_tampering_fails_closed(tmp_path, monkeypatch):
    import openarm365_dev_v2.scene_contract as scene_contract
    tampered = tmp_path / scene_contract.REGISTERED_CONTRACT.name
    tampered.write_bytes(scene_contract.REGISTERED_CONTRACT.read_bytes() + b' ')
    monkeypatch.setattr(scene_contract, 'REGISTERED_CONTRACT', tampered)
    with pytest.raises(ValueError, match='re-registration'):
        scene_contract.registered_contract_ref()


def test_old_manifest_contract_reference_still_loads():
    from openarm365_dev_v2.scene_contract import load_contract, seed_entry
    path = Path(__file__).resolve().parents[1] / 'scene_contracts' / 'dev-seeds-101-103-v2-20261016.json'
    ref = {'uri': str(path), 'sha256': '44a386e908fd94a5bef2359fac364ab047d4c97940050dc17895245bbac7fd63'}
    assert hashlib.sha256(path.read_bytes()).hexdigest() == ref['sha256']
    old = load_contract(ref)
    assert old['provenance']['built'] == '2026-10-16'
    assert seed_entry(old, 'pick_place_counter_to_sink', 102)['normalized_sha256'] == 'd844035e63a67adae07bbd799f76af4e86fb2b07b7c095cc01be606d7399fbae'
