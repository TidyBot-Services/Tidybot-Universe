"""M-8 keeps the origin's frozen planner configuration and exact config bytes."""
from pathlib import Path

import pytest
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment
pytestmark = pytest.mark.usefixtures("r3_deployment")

from benchmarks.attention_harness import dev_experiment as experiments
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.dev_v2_loop import immutable
from openarm365_dev_v2 import dev_config
from openarm365_dev_v2.scene_contract import registered_contract_ref


@pytest.mark.parametrize('legacy', [False, True])
def test_validation_origin_preserves_planner_config(tmp_path, monkeypatch, legacy):
    # Missing copied native geometry is unrelated to this offline config test.
    monkeypatch.setattr(dev_config, 'model_sources', lambda *a, **kw: {})
    if legacy:
        make_config = dev_config._make_dev_config
        make_lock = experiments.make_lock
        monkeypatch.setattr(experiments,"make_lock",lambda path,**kw:make_lock(path))
        def legacy_config(*args, **kwargs):
            config = make_config(*args, **kwargs)
            for key in dev_config.PLANNER_CONFIG_KEYS:
                del config[key]
            config['contact_exempt_objects'] = 'none'
            return config
        monkeypatch.setattr(experiments, 'make_dev_config', legacy_config)
    selection = dict(request_key='original-001', tasks=['open_cabinet'], seeds=[102],
        policies=['autonomous'], mode='diagnostic', budget_tier='dev', target_depth=1,
        target_valid_executions=1, max_versions=1, help_budget=0,
        reproducible_reset=True, scene_contract=registered_contract_ref())
    original = experiments.create(DevV2Store(tmp_path/'origin.sqlite3'),
                                  tmp_path/'original', selection)
    manifest = experiments.verify_manifest(original)
    config = manifest['configs']['open_cabinet:102']
    assert ('execution_settings' not in config) if legacy else config['execution_settings']['profile']=='r3-20261025'
    assert config['contact_exempt_objects'] == ('none' if legacy else 'movable_objects')
    assert all((key not in config) if legacy else (key in config)
               for key in dev_config.PLANNER_CONFIG_KEYS)
    origin = dict(manifest=original['manifest'], config=immutable(tmp_path/'config.json', config),
        source=immutable(tmp_path/'origin.py', manifest['initial_sources']['open_cabinet']['source']),
        cell=original['cells'][0]['key'], version=1)
    original_bytes = Path(origin['config']['uri']).read_bytes()
    validation = dict(selection, request_key='validation-001', model_source='codex',
        run_notes=False, memory_family=False, attempt_wall_seconds=config['attempt_wall_seconds'])
    result = experiments.create(DevV2Store(tmp_path/'validation.sqlite3'),
        tmp_path/'validation', validation, validation_origin=origin)
    assert experiments.verify_manifest(result)['configs'] == {'open_cabinet:102': config}
    assert len(result['cells']) == 10
    for cell in result['cells']:
        assert Path(cell['artifact_dir'], 'config.json').read_bytes() == original_bytes
    assert Path(origin['config']['uri']).read_bytes() == original_bytes
