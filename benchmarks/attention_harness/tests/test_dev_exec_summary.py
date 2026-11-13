"""Try speed-up item 3: short execution summary and capped devtool printing (no simulator)."""
import json
import subprocess
import sys
from pathlib import Path

from benchmarks.attention_harness.dev_agent_exec_summary import build
from benchmarks.attention_harness import dev_agent_tools as tools


def obs(x, cup):
    return {'robot': {'robot0_left_eef_pos': [x, 0, 1], 'robot0_left_gripper_qpos': [.02, .02],
                      'robot0_left_eef_quat': [0, 0, 0, 1]},
            'perception': {'values': {'cup_pos': cup, 'sink_pos': [1, 1, 1]}}}


def test_summary_keeps_failures_state_and_tails_in_a_few_kb():
    trace = [dict(source='sensors', operation='find_objects', status='completed', arguments={'args': [], 'kwargs': {}},
                  result=[{'name': 'cup', 'position': [0] * 3}] * 50)]
    trace += [dict(source='right_arm', operation='move_delta', status='completed', arguments={'args': [0, 0, .01], 'kwargs': {}})] * 100
    trace += [dict(source='right_arm', operation='move_delta', status='failed', arguments={'args': [0, -.015, .05], 'kwargs': {}},
                   error={'message': 'RecoverablePlanRejection: start_state_collision', 'type': 'RuntimeError'})]
    s = build('e000003', outcome={'status': 'failed'}, trace=trace, stdout='x' * 50000 + 'END', stderr='',
              initial_observation=obs(0, [0, 0, 1]), final_observation=obs(.2, [0, .05, 1]), files={'state_log.json': 9677391})
    text = json.dumps(s)
    assert len(text) < 16000
    assert s['sdk_call_count'] == 102 and s['failed_call_count'] == 1
    assert s['first_failure']['error']['message'].startswith('RecoverablePlanRejection')
    assert s['sdk_calls'][-1]['error'].startswith('"RecoverablePlanRejection') and {'omitted': 42} in s['sdk_calls']
    assert s['stdout_tail'].endswith('END') and s['stdout_chars'] == 50003
    assert list(s['objects_moved']) == ['cup'] and s['objects_moved']['cup']['moved_m'] == .05
    assert s['robot']['end']['robot0_left_eef_pos'] == [.2, 0, 1] and 'robot0_left_eef_quat' not in s['robot']['end']
    assert s['full_files'] == {'state_log.json': 9677391}


def test_devtool_short_print_keeps_small_fields_and_names_the_full_file(tmp_path):
    (tmp_path / 'rpc/responses').mkdir(parents=True)
    value = dict(status='completed', request_id='r1', result=dict(planning_accepted=False, reason='joint_margin',
                 diagnostics=dict(goal_margin={'minimum_joint_margin_rad': .01}, trajectory=[[0.1] * 14] * 81,
                                  solver_diagnostics=[{'solution_qpos': [[0.2] * 7] * 400}])))
    (tmp_path / 'rpc/responses/r1.json').write_text(json.dumps(value))
    out = json.loads(tools.show(value, tmp_path, 'r1'))
    assert len(json.dumps(out)) <= tools.PRINT_LIMIT + 300 and out['printed'] == 'short'
    assert out['result']['reason'] == 'joint_margin' and out['result']['planning_accepted'] is False
    assert out['result']['diagnostics']['goal_margin'] == {'minimum_joint_margin_rad': .01}
    assert out['result']['diagnostics']['trajectory']['elided'].startswith('list of 81 items')
    assert out['full_response'].startswith('rpc/responses/r1.json')
    assert json.loads(tools.show(value, tmp_path, 'r1', full=True)) == value


def test_short_responses_print_unchanged(tmp_path):
    (tmp_path / 'rpc/responses').mkdir(parents=True)
    value = dict(status='completed', request_id='r2', result={'ok': True})
    assert tools.show(value, tmp_path, 'r2') == json.dumps(value, sort_keys=True)



def test_full_flag_is_accepted_by_the_cli(tmp_path):
    # A copied devtool in a scratch workspace; --timeout 0 returns pending without a broker.
    ws = tmp_path / 'ws'; (ws / 'docs').mkdir(parents=True); (ws / 'rpc/requests').mkdir(parents=True)
    (ws / 'docs/devtool.py').write_bytes(Path(tools.__file__).read_bytes())
    out = subprocess.run([sys.executable, 'docs/devtool.py', 'status', '--full', '--timeout', '0', '--request-id', 'abc'],
                         cwd=ws, capture_output=True, text=True)
    assert out.returncode == 2 and json.loads(out.stdout) == {'request_id': 'abc', 'status': 'pending'}
