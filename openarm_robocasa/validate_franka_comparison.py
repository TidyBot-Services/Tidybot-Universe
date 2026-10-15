"""Run the original approved Franka policy; sample existing read-only state APIs.

This evidence collector never submits robot commands. The existing formal runner
owns all reset/control/evaluation and process cleanup. Sampling is timestamped,
not an assertion that separate agent and object responses share one physics step.
"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import socket
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads((root / 'benchmarks/attention_harness/protocol/v2/skill_graphs/live_run_catalog.json').read_text())
    profile = next(p for p in catalog['profiles'] if p['id'] == 'live-human-robocasa-counter-to-sink-seed101-guidance-v1-1')
    for path_key, hash_key in [('code', 'approved_policy_sha256'), ('config', 'approved_config_sha256')]:
        if hashlib.sha256(Path(profile[path_key]).read_bytes()).hexdigest() != profile[hash_key]:
            raise RuntimeError('Original Franka source pin changed')
    config = json.loads(Path(profile['config']).read_text())
    sim = f"http://127.0.0.1:{5500 + config['port_offset']}"
    agent = f"http://127.0.0.1:{8080 + config['port_offset']}"

    def read(origin, path, *, payload=None):
        request = Request(origin + path, data=None if payload is None else json.dumps(payload).encode(),
                          headers={'Content-Type': 'application/json'}, method='GET' if payload is None else 'POST')
        with urlopen(request, timeout=1) as response:
            return json.load(response)

    for origin in (sim, agent):
        try:
            connection = socket.create_connection(('127.0.0.1', int(origin.rsplit(':', 1)[1])), timeout=1)
        except ConnectionRefusedError:
            pass
        else:
            connection.close()
            raise RuntimeError('Approved service port is occupied; do not sample another run')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, '-B', '-u', '-m', 'benchmarks.attention_harness.formal_attention_cli',
               '--suite', 'robocasa', '--task', 'counter_to_sink', '--seed', '101',
               '--attention-policy', 'reactive_help', '--max-attempts', '1', '--assistance-credits', '0',
               '--token-limit', '8000', '--overall-deadline-seconds', '300', '--record-replay',
               '--code', profile['code'], '--approved-policy-sha256', profile['approved_policy_sha256'],
               '--config', profile['config'], '--approved-config-sha256', profile['approved_config_sha256'],
               '--artifact-root', str(out / 'runs'), '--store-path', str(out / 'attention.sqlite3'),
               '--summary-path', str(out / 'summary.json')]
    for key in ['sim_source_root', 'agent_source_root', 'task_source_root', 'sim_python', 'agent_python']:
        command += ['--' + key.replace('_', '-'), profile[key]]
    (out / 'command.json').write_text(json.dumps(command, indent=2, ensure_ascii=False) + '\n')
    started = time.monotonic()
    samples = errors = 0
    with (out / 'runner.log').open('wb') as log, (out / 'physical_trajectory.jsonl').open('w') as trajectory:
        process = subprocess.Popen(command, cwd=root, stdout=log, stderr=subprocess.STDOUT)
        while process.poll() is None:
            if time.monotonic() - started > 360:
                # The owned runner has a shorter 300s watchdog; preserve its result.
                process.wait(timeout=30)
                break
            try:
                info = read(sim, '/task/info')
                if info.get('task') != 'RoboCasa-Pn-P-Counter-To-Sink-v0':
                    raise RuntimeError('Unexpected original task; refuse cross-run sampling')
                state_requested_at = time.time()
                state = read(agent, '/state')
                state_received_at = time.time()
                perception_requested_at = time.time()
                perception = read(sim, '/perceive', payload={})
                sample = {'sequence': samples, 'source': 'original agent GET /state and simulator POST /perceive',
                          'state_requested_at': state_requested_at, 'state_received_at': state_received_at,
                          'perception_requested_at': perception_requested_at, 'perception_received_at': time.time(),
                          'task_info': info, 'state': state, 'perception': perception,
                          'coherent_single_physics_step': False}
                trajectory.write(json.dumps(sample, allow_nan=False) + '\n')
                trajectory.flush()
                samples += 1
            except Exception as error:
                # Startup and shutdown have no state. Retain all observed errors.
                errors += 1
                with (out / 'sampling_errors.jsonl').open('a') as error_log:
                    error_log.write(json.dumps({'at': time.time(), 'type': type(error).__name__, 'error': str(error)}) + '\n')
            time.sleep(.5)
        exit_code = process.wait()
    report = {'runner_exit_code': exit_code, 'samples': samples, 'sampling_errors': errors,
              'requested_interval_seconds': .5, 'scope': 'read-only sampled trajectory; native verdict comes only from unchanged runner'}
    (out / 'sampler_result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)
    return int(samples < 3 or not (out / 'summary.json').is_file())


if __name__ == '__main__':
    raise SystemExit(main())
