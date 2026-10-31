"""Print the author's local path registry, or write it with --write."""

import argparse
import json
from pathlib import Path


HOME = Path('/home') / 'truares'
CODEX = HOME / 'Documents' / 'Codex'
DESKTOP = HOME / '桌面'
WORKSPACE = DESKTOP / 'AttentionBench-Workspace'

# Each path is deliberately retained in its old location on the author machine.
# The third field is the portable collaborator layout used by the template.
PATHS = {
    'sim.python': ('codex', '2026-10-09/new-chat/work/service_env/bin/python', 'envs/robocasa365/bin/python'),
    'sim.robocasa_root': ('codex', '2026-10-09/new-chat/work/upstream/robocasa', 'sim/robocasa'),
    'sim.robosuite_root': ('codex', '2026-10-09/new-chat/work/upstream/robosuite', 'sim/robosuite'),
    'sim.assets_manifest': ('codex', '2026-10-09/new-chat/outputs/robocasa365/assets-manifest.json', 'data/robocasa365/assets-manifest.json'),
    'sim.configs_dir': ('codex', '2026-10-09/new-chat/outputs/robocasa365/configs', 'data/robocasa365/configs'),
    'sim.delivery_root': ('codex', '2026-10-09/new-chat/outputs/robocasa365', 'data/robocasa365'),
    'sim.legacy_python': ('home', 'miniconda3/envs/maniskill/bin/python', 'envs/maniskill/bin/python'),
    'sim.legacy_bin': ('home', 'miniconda3/envs/maniskill/bin', 'envs/maniskill/bin'),
    'sim.legacy_tasks': ('home', '文档/Tidybot-Universe/sims/robocasa_tasks', 'sim/robocasa_tasks'),
    'sim.dev_robot_config': ('codex', '2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-05/runtime/project/config/openarm_fixed.yml', 'deployments/dev05/runtime/project/config/openarm_fixed.yml'),
    'sim.dev_assets_dir': ('codex', '2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-05/runtime/project/curobo_v2/service/curobo_service/assets', 'deployments/dev05/runtime/project/curobo_v2/service/curobo_service/assets'),
    'repo.native_tree': ('desktop', 'Tidybot-Universe-attention-native', 'repos/Tidybot-Universe-attention-native'),
    'memory.service_root': ('desktop', 'attention_memory_service', 'repos/attention_memory_service'),
    'memory.m3_root': ('workspace', 'development/memory-m3', 'repos/memory-m3'),
    'memory.m3_keys': ('workspace', 'development/memory-m3/keys.env', 'repos/memory-m3/keys.env'),
    'litellm.key_file': ('home', '.litellm-key', 'data/secrets/litellm-key'),
    'ui.fixed_root': ('codex', '2026-10-11/openarm-curobo-integration', 'deployments/openarm-curobo-integration'),
    'ui.dev_root': ('codex', '2026-10-11/new-chat-2/outputs/openarm-dev-integration', 'deployments/openarm-dev-integration'),
    'ui.dev_local_root': ('codex', '2026-10-11/new-chat-2/outputs/openarm-dev-integration/dev-local', 'deployments/openarm-dev-integration/dev-local'),
    'ui.dev_v2_root': ('codex', '2026-10-14/goal-attentionbench-robocasa365-dev-v2-home/outputs/daily-experiments', 'data/daily-experiments'),
    'ui.run_catalog': ('codex', '2026-10-12/new-chat-2/outputs/daily-v2-33/catalog.json', 'data/daily-v2-33/catalog.json'),
    'ui.store': ('codex', '2026-10-06/docs-live-human-ui-validation-plan/work/mac-ui-debug-20261006-154731/attention.sqlite3', 'data/ui/attention.sqlite3'),
    'ui.artifact_root': ('codex', '2026-10-06/docs-live-human-ui-validation-plan/work/mac-ui-debug-20261006-154731/artifacts', 'data/ui/artifacts'),
    'ui.serve_daily': ('codex', '2026-10-14/goal-attentionbench-robocasa365-dev-v2-home/outputs/serve_daily.py', 'repos/attentionbench/serve_daily.py'),
    'ui.daily_python': ('home', '.cache/tidybot-attention/venv/bin/python', 'envs/ui/bin/python'),
    'ui.robocasa_python': ('desktop', 'attentionbench-week2-clean-venv/bin/python', 'envs/robocasa-ui/bin/python'),
    'deployments.dev05_root': ('codex', '2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-05', 'deployments/dev05'),
    'deployments.dev06_root': ('codex', '2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-06', 'deployments/dev06'),
    'deployments.dev05_lock': ('codex', '2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-05/runtime-lock.json', 'deployments/dev05/runtime-lock.json'),
    'deployments.dev06_lock': ('codex', '2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-06/runtime-lock.json', 'deployments/dev06/runtime-lock.json'),
    'deployments.main_native_stop': ('codex', '2026-10-12/goal-curobov2-robocasa-robocasa365-v2-only/outputs/main-fixed-task-01/owned/native_stop.json', 'data/main-fixed-task-01/owned/native_stop.json'),
    'scene.contract': ('workspace', 'experiments/scene-contracts/scene-contract-r3-104-107-114.json', 'data/scene-contracts/scene-contract-r3-104-107-114.json'),
    'test.real_eval': ('home', '下载/dev-agent/test-evidence/d4/real-eval', 'data/test-evidence/d4/real-eval'),
    'test.real_session': ('home', '下载/dev-agent/test-evidence/d2/real-session', 'data/test-evidence/d2/real-session'),
    'test.dev_v2_db': ('codex', '2026-10-14/new-chat/work/dev-v2.sqlite', 'data/dev-v2.sqlite'),
    'test.native_robot_config': ('desktop', 'Tidybot-Universe-attention-native/openarm_robocasa/configs/robot_parameters_v2.json', 'repos/Tidybot-Universe-attention-native/openarm_robocasa/configs/robot_parameters_v2.json'),
}


def author_paths() -> dict[str, str]:
    roots = {'home': HOME, 'codex': CODEX, 'desktop': DESKTOP, 'workspace': WORKSPACE}
    return {key: str(roots[base] / suffix) for key, (base, suffix, _) in PATHS.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    registry = {'schema': 'attentionbench.paths.v1', 'paths': author_paths()}
    output = json.dumps(registry, ensure_ascii=False, indent=2) + '\n'
    if args.write:
        path = WORKSPACE / 'paths.json'
        path.write_text(output, encoding='utf-8')
        print(path)
    else:
        print(output, end='')


if __name__ == '__main__':
    main()
