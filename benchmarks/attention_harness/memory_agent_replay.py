"""Fresh validator-owned policy execution through the native Dev v2 runner."""
import json
from pathlib import Path

from .dev_v2_loop import immutable


def replay_policy(task_id, seed, config, *, source, directory, cell, version, runner_factory=None):
    from .formal_runner_boundary import FormalRunRequest
    from openarm365_dev_v2.runner import OpenArm365DevRunnerV2
    from openarm365_dev_v2.dev_config import SIM_PYTHON
    out = Path(directory); out.mkdir(parents=True, exist_ok=False)
    code = immutable(out/'policy.py', source)
    frozen = immutable(out/'config.json', config)
    episodes = []
    runner = (runner_factory or OpenArm365DevRunnerV2)(service_source_root=None, sim_python=SIM_PYTHON,
        sim_source_root=None, task_source_root=None, asset_manifest=None, agent_mode=True,
        independent_evaluation=True, episode_observer=episodes.append)
    request = FormalRunRequest(suite='robocasa365', task_id=task_id, seed=seed,
        policy_code_path=Path(code['uri']), policy_sha256=code['sha256'], config_path=Path(frozen['uri']),
        config_sha256=frozen['sha256'], artifact_root=out/'executions',
        overall_deadline_seconds=config['attempt_wall_seconds']+60,
        attempt_deadline_seconds=config['attempt_wall_seconds'], sdk_call_limit=config['sdk_call_limit'],
        run_id='run:origin-replay:'+cell, attempt_id=f'origin-replay:{cell}:{version}', entry_lock=None)
    result = runner.execute(request)
    if not episodes:raise ValueError('origin policy replay produced no episode')
    episode = Path(episodes[-1]).resolve()
    if not episode.is_relative_to(out.resolve()):raise ValueError('origin replay episode outside validator root')
    bundle = immutable(out/'bundle.json', dict(result, config_lock=frozen, policy_source=code))
    observation = json.loads((episode/'initial_public_observation.json').read_bytes())
    state = json.loads((episode/'scene-condition.json').read_bytes())['state']
    return observation, state, bundle
