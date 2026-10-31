"""Machine registry resolution is explicit, lazy, and byte-for-byte stable."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.attention_harness import legacy_paths, workspace_paths


def registry(tmp_path, monkeypatch, paths=None, schema='attentionbench.paths.v1'):
    monkeypatch.setenv('ATTENTIONBENCH_WORKSPACE', str(tmp_path))
    workspace_paths.clear_cache()
    if paths is not None:
        (tmp_path/'paths.json').write_text(json.dumps({'schema': schema, 'paths': paths}))
    return tmp_path/'paths.json'


def test_missing_registry_and_unknown_key(tmp_path, monkeypatch):
    path = registry(tmp_path, monkeypatch)
    with pytest.raises(workspace_paths.WorkspacePathError) as exc:
        workspace_paths.get('sim.python')
    assert 'sim.python' in str(exc.value) and str(path) in str(exc.value)
    assert 'config/attentionbench/paths.template.json' in str(exc.value)
    registry(tmp_path, monkeypatch, {'sim.python': 'envs/sim/bin/python'})
    with pytest.raises(workspace_paths.WorkspacePathError) as exc:
        workspace_paths.get('sim.unknown')
    assert 'sim.unknown' in str(exc.value) and str(path) in str(exc.value)


def test_bad_schema_relative_absolute_and_cache(tmp_path, monkeypatch):
    path = registry(tmp_path, monkeypatch, {}, schema='wrong')
    with pytest.raises(workspace_paths.WorkspacePathError, match='Invalid path registry schema'):
        workspace_paths.get('sim.python')
    absolute = Path('/opt')/'attentionbench'/'python'
    raw_absolute = '/opt/../attentionbench/python//'
    registry(tmp_path, monkeypatch, {'relative': 'envs/../sim/bin/python',
                                     'raw_relative': './envs/python/',
                                     'absolute': str(absolute), 'raw_absolute': raw_absolute,
                                     'tilde': '~/python'})
    assert workspace_paths.get_str('relative') == str(tmp_path/'envs/../sim/bin/python')
    assert workspace_paths.get_str('raw_relative') == str(tmp_path) + '/./envs/python/'
    assert workspace_paths.get_str('absolute') == str(absolute)
    assert workspace_paths.get_str('raw_absolute') == raw_absolute
    assert workspace_paths.get_str('tilde') == str(tmp_path/'~/python')
    path.write_text(json.dumps({'schema': 'attentionbench.paths.v1', 'paths': {'absolute': '/changed'}}))
    assert workspace_paths.get_str('absolute') == str(absolute)
    workspace_paths.clear_cache()
    assert workspace_paths.get_str('absolute') == '/changed'


def test_codex_path_map_resolves_missing_path(tmp_path, monkeypatch):
    monkeypatch.setenv('ATTENTIONBENCH_WORKSPACE', str(tmp_path))
    legacy_paths.clear_cache()
    old = legacy_paths.CODEX/'2026-10-09'/'new-chat'/'outputs'/'sample.json'
    new = tmp_path/'data'/'sample.json'
    new.parent.mkdir()
    new.write_text('{}')
    (tmp_path/'path-map.tsv').write_text(
        f'{old.parent}\t{new.parent}\tmove\n', encoding='utf-8')
    assert legacy_paths.resolve(old) == new


def test_author_registry_reproduces_previous_values(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[3]
    script = root/'scripts/attentionbench/write_author_paths.py'
    output = subprocess.check_output([sys.executable, str(script)], text=True, cwd=root)
    data = json.loads(output)
    assert data['schema'] == 'attentionbench.paths.v1'
    registry(tmp_path, monkeypatch, data['paths'])
    home = Path('/home')/'truares'
    codex = home/'Documents'/'Codex'
    desktop = home/'桌面'
    workspace = desktop/'AttentionBench-Workspace'
    expected = {
        'sim.python': codex/'2026-10-09/new-chat/work/service_env/bin/python',
        'sim.robocasa_root': codex/'2026-10-09/new-chat/work/upstream/robocasa',
        'sim.robosuite_root': codex/'2026-10-09/new-chat/work/upstream/robosuite',
        'sim.assets_manifest': codex/'2026-10-09/new-chat/outputs/robocasa365/assets-manifest.json',
        'sim.configs_dir': workspace/'development/Tidybot-Universe-attention-native/benchmarks/attention_harness/robocasa365/configs',
        'sim.delivery_root': codex/'2026-10-09/new-chat/outputs/robocasa365',
        'sim.legacy_python': home/'miniconda3/envs/maniskill/bin/python',
        'sim.legacy_bin': home/'miniconda3/envs/maniskill/bin',
        'sim.legacy_tasks': home/'文档/Tidybot-Universe/sims/robocasa_tasks',
        'sim.dev_robot_config': codex/'2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-05/runtime/project/config/openarm_fixed.yml',
        'sim.dev_assets_dir': codex/'2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-05/runtime/project/curobo_v2/service/curobo_service/assets',
        'memory.service_root': desktop/'attention_memory_service',
        'memory.m3_root': workspace/'development/memory-m3',
        'memory.m3_keys': workspace/'development/memory-m3/keys.env',
        'litellm.key_file': home/'.litellm-key',
        'ui.fixed_root': codex/'2026-10-11/openarm-curobo-integration',
        'ui.dev_root': codex/'2026-10-11/new-chat-2/outputs/openarm-dev-integration',
        'ui.dev_local_root': codex/'2026-10-11/new-chat-2/outputs/openarm-dev-integration/dev-local',
        'ui.dev_v2_root': codex/'2026-10-14/goal-attentionbench-robocasa365-dev-v2-home/outputs/daily-experiments',
        'ui.store': codex/'2026-10-06/docs-live-human-ui-validation-plan/work/mac-ui-debug-20261006-154731/attention.sqlite3',
        'ui.artifact_root': codex/'2026-10-06/docs-live-human-ui-validation-plan/work/mac-ui-debug-20261006-154731/artifacts',
        'ui.serve_daily': codex/'2026-10-14/goal-attentionbench-robocasa365-dev-v2-home/outputs/serve_daily.py',
        'ui.daily_python': home/'.cache/tidybot-attention/venv/bin/python',
        'ui.robocasa_python': desktop/'attentionbench-week2-clean-venv/bin/python',
        'deployments.dev05_root': codex/'2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-05',
        'deployments.dev06_root': codex/'2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-06',
        'deployments.dev05_lock': codex/'2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-05/runtime-lock.json',
        'deployments.dev06_lock': codex/'2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-06/runtime-lock.json',
        'deployments.main_native_stop': codex/'2026-10-12/goal-curobov2-robocasa-robocasa365-v2-only/outputs/main-fixed-task-01/owned/native_stop.json',
        'scene.contract': workspace/'experiments/scene-contracts/scene-contract-r3-104-107-114.json',
        'test.real_eval': home/'下载/dev-agent/test-evidence/d4/real-eval',
        'test.real_session': home/'下载/dev-agent/test-evidence/d2/real-session',
        'test.dev_v2_db': codex/'2026-10-14/new-chat/work/dev-v2.sqlite',
        'test.native_robot_config': desktop/'Tidybot-Universe-attention-native/openarm_robocasa/configs/robot_parameters_v2.json',
    }
    assert set(data['paths']) == set(expected)
    template = json.loads((root/'config/attentionbench/paths.template.json').read_text())
    assert template['schema'] == data['schema']
    assert template['_comment'] and set(template['paths']) == set(expected) | {'planner.local_dev_trust'}
    for key, old in expected.items():
        assert workspace_paths.get_str(key) == str(old), key
    workspace_paths.clear_cache()
