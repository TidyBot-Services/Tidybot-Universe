"""Offline Dev think-time contracts. Synthetic public folders and fake sessions only."""
import ast
import builtins
import copy
import json
from pathlib import Path
import subprocess
import sys
import types

import pytest

from benchmarks.attention_harness import dev_agent_diagnose as diag, dev_agent_tools as tools
from benchmarks.attention_harness.dev_agent_exec_summary import build as summary
from benchmarks.attention_harness.dev_agent_session import CodexAgentSession, COMPACT_PROMPT, SessionError
from benchmarks.attention_harness.dev_agent_workspace import TOOLS, TOOLS_V2, Workspace
from benchmarks.attention_harness.dev_agent_config import context_profile, normalize
from benchmarks.attention_harness.tests.test_dev_agent_configuration import SELECTION
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment, offline_dev_identity


def write(root, name, value):
    path = root/name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(tmp_path, message='ik_path_joint_jump'):
    root = tmp_path/'ws'
    folder = root/'executions/e000007'
    robot = dict(robot0_left_eef_pos=[.1, 0, 0], robot0_left_eef_quat=[0, 0, 0, 1],
                 robot0_left_gripper_qpos=[.02, .02], robot0_left_joint_pos=[.99, .9, .8, 0, 0, 0, 0])
    trace = [dict(source='sensors', operation='get_observation', result={'robot': robot}),
             dict(source='right_gripper', operation='open', status='completed'),
             dict(source='left_arm', operation='move_to', arguments={'kwargs': {'position': [.4, 0, 0]}},
                  status='failed', error={'message': message}, physics_step=30),
             dict(source='right_arm', operation='move_delta', status='failed', error='later')]
    write(folder, 'result.json', {'status': 'failed'})
    write(folder, 'trace.json', trace)
    write(folder, 'state_log.json', [dict(physics_step=i, robot=robot, unexpected_contacts=[i],
          bilateral_contact_objects={'left': ['cup']}, hidden_unrelated='unused') for i in range(65)])
    write(folder, 'diagnostics/planner.json', [{'reason': 'ik_path_joint_jump'}])
    write(folder, 'initial_observation.json', {'robot': robot, 'perception': {'values': {'cup_pos': [0, 0, 0]}}})
    write(folder, 'final_observation.json', {'robot': robot, 'perception': {'values': {'cup_pos': [.02, 0, 0]}}})
    (folder/'stdout.txt').write_text('x'*1500+'stdout end')
    (folder/'stderr.txt').write_text('stderr end')
    write(folder, 'help.json', {'status': 'unavailable'})
    write(folder, 'eval.json', {'status': 'unavailable'})
    write(root, 'docs/sdk.json', {'groups': ['left_arm', 'right_arm'], 'control_steps': 9600})
    (root/'docs/tools.md').write_text(TOOLS_V2)
    (root/'work').mkdir()
    (root/'work/notes.md').write_text('')
    return root, folder, trace


def test_diagnose_complete_distance_contacts_and_movement(tmp_path):
    root, folder, trace = fixture(tmp_path)
    value = diag.diagnose(root, '7')
    assert list(value)[:2] == ['outcome', 'first_failure']
    first = value['first_failure']
    assert first['index'] == 2 and first['call'] == 'left_arm.move_to'
    assert first['arguments'] == trace[2]['arguments'] and len(first['previous']) == 2
    assert first['planner_reason'] == ['ik_path_joint_jump']
    arm = value['at_failure']['arms']['left']
    assert arm['distance_to_target_m'] == pytest.approx(.3)
    assert 'eef_quat' not in arm and arm['gripper_opening'] == [.02, .02]
    assert value['at_failure']['row_index'] == 30
    assert [v['physics_step'] for v in value['contacts']] == list(range(28, 33))
    assert all(set(v) == {'physics_step', 'unexpected_contacts', 'bilateral_contact_objects'} for v in value['contacts'])
    assert 'hidden_unrelated' not in json.dumps(value['contacts'])
    assert value['objects_moved']['cup']['moved_m'] == .02
    assert 'joint_margins' not in value and not any('joint_margins' in n for n in value.get('notes', []))
    assert value['pointers']['full_files']['trace.json'] == (folder/'trace.json').stat().st_size
    assert 'help.json' in value['pointers']['lines'][2] and 'eval.json' in value['pointers']['lines'][3]
    assert json.loads((folder/'diagnose.json').read_text()) == value
    assert len(tools.bounded_json(value)) <= 4000


def test_diagnose_long_failure_never_cut(tmp_path):
    message = 'ik_path_joint_jump ' + 'long failure '*800 + ' THE END'
    root, folder, trace = fixture(tmp_path, message)
    trace[2]['arguments']['kwargs']['detail'] = 'full arguments '*600
    write(folder, 'trace.json', trace)
    value = diag.diagnose(root, 'e000007')
    out = json.loads(tools.bounded_json(value))
    assert out['first_failure']['error']['message'] == message
    assert out['first_failure']['arguments'] == trace[2]['arguments']
    assert json.loads((folder/'diagnose.json').read_text())['first_failure']['error']['message'] == message


@pytest.mark.parametrize('as_string', [False, True])
def test_long_result_error_and_rejection_strings_never_cut(tmp_path, as_string):
    message = 'full error or rejection ' * 500 + 'END'
    root, folder, trace = fixture(tmp_path)
    trace[2].pop('error')
    trace[2]['result'] = message if as_string else {
        'rejected': True, 'reason': message, 'diagnostics': {'error': {'detail': message}, 'qpos': [0]*1000}}
    write(folder, 'trace.json', trace)
    value = diag.diagnose(root, '7')
    shown = json.loads(tools.bounded_json(value))['first_failure']['result']
    saved = json.loads((folder/'diagnose.json').read_text())['first_failure']['result']
    assert shown == saved
    if as_string:
        assert shown['message'] == message
    else:
        assert shown['reason'] == message and shown['diagnostics']['error']['detail'] == message
        assert 'qpos' not in shown['diagnostics']


def test_missing_files_no_limits_and_fallback_are_explicit(tmp_path):
    root, folder, _ = fixture(tmp_path)
    write(root, 'docs/sdk.json', {})
    trace = [{'source': 'left_arm', 'operation': 'move_delta', 'arguments': {'args': [.01, 0, 0]},
              'result': {'rejected': True, 'reason': 'official_ik_non_success'}, 'status': 'rejected'}]
    write(folder, 'trace.json', trace)
    (folder/'initial_observation.json').unlink()
    (folder/'diagnostics/planner.json').unlink()
    value = diag.diagnose(root, '7')
    assert value['at_failure']['row_index'] == 64
    assert 'no reliable' in value['at_failure']['alignment']
    assert 'joint_margins' not in value and not any('joint_margins' in n for n in value['notes'])
    assert 'distance_to_target_m' not in value['at_failure']['arms']['left']
    assert any('initial_observation.json' in n for n in value['notes'])
    for path in folder.rglob('*.json'):
        path.unlink()
    empty = diag.diagnose(root, '7')
    assert 'outcome' not in empty and 'first_failure' not in empty and empty['notes']
    assert len(tools.bounded_json(empty)) <= 4000


def test_delta_target_uses_known_pre_call_eef(tmp_path):
    root, folder, trace = fixture(tmp_path)
    trace[2].update(operation='move_delta', arguments={'args': [.05, 0, 0]})
    write(folder, 'trace.json', trace)
    value = diag.diagnose(root, '7')
    arm = value['at_failure']['arms']['left']
    assert arm['target_position'] == pytest.approx([.15, 0, 0])
    assert arm['distance_to_target_m'] == pytest.approx(.05)
    trace[0]['result'] = {}
    write(folder, 'trace.json', trace)
    assert diag.build(root, '7')['at_failure']['arms']['left']['distance_to_target_m'] == pytest.approx(.05)
    trace.insert(2, dict(source='left_arm', operation='move_delta', status='completed'))
    write(folder, 'trace.json', trace)
    assert 'distance_to_target_m' not in diag.build(root, '7')['at_failure']['arms']['left']


def test_native_tcp_and_explicit_sequence_alignment(tmp_path):
    root, folder, trace = fixture(tmp_path)
    trace[2].pop('physics_step'); trace[2]['sequence'] = 5
    write(folder, 'trace.json', trace)
    write(folder, 'state_log.json', [dict(sequence=5, tcp={'left': [[1,0,0,.1],[0,1,0,0],[0,0,1,0],[0,0,0,1]]},
                                       gripper_qpos={'left': [.02,.02]})])
    value = diag.diagnose(root, '7')
    assert value['at_failure']['arms']['left']['eef_pos'] == [.1,0,0]
    assert 'sequence' in value['at_failure']['alignment']


def public_shapes(tmp_path, failed):
    """Real published shapes: relative trace time, no trace/state coordinates in common."""
    root, folder, _ = fixture(tmp_path)
    def call(source, status='completed', result=None):
        return dict(arguments={'args': [], 'kwargs': {}}, operation='move_delta',
                    result={'ok': True, 'rejected': False} if result is None else result,
                    source=source, status=status, timestamp=1.25, timestamp_basis='relative')
    trace = [call('left_gripper'), call('sensors'), failed, call('base')]
    rows = [dict(request_id=f'request-{i//1000}', physics_step=i+1,
                 qpos=[0]*14, qvel=[0]*14, base_qpos=[0]*3,
                 tcp={s: [[1,0,0,.123456789+i/10000], [0,1,0,0], [0,0,1,.9], [0,0,0,1]]
                      for s in ('left', 'right')},
                 gripper_qpos={'left': [.0123456,.0123456], 'right': [.02,.02]},
                 gripper_qvel={'left': [0,0], 'right': [0,0]},
                 unexpected_contacts=[], bilateral_contact_objects={'left': [], 'right': []},
                 held_envelope={'rotation_rad': .1, 'translation_m': .01},
                 execution_profile='r4-20261027') for i in range(3000)]
    if not diag.dispatched(failed):
        for i, row in enumerate(rows):
            row['request_id'] = f'request-{i//1500}'
    write(folder, 'trace.json', trace)
    write(folder, 'state_log.json', rows)
    return root, folder, trace, rows


def public_failure(source='left_arm', status='failed', result=None):
    return dict(arguments={'args': [], 'kwargs': {'position': [.4,0,.9]}},
                operation='move_delta', result={'message': 'motion failed'} if result is None else result,
                source=source, status=status, timestamp=2.5, timestamp_basis='relative')


@pytest.mark.parametrize('source', sorted(diag.MOVING))
def test_request_group_failure_during_motion(tmp_path, source):
    root, folder, trace, rows = public_shapes(tmp_path, public_failure(source))
    value = diag.build(root, '7')
    assert value['at_failure']['row_index'] == 1999
    assert value['at_failure']['alignment'] == 'request group 2 of 3 (failed during motion)'
    assert value['first_failure']['result'] == {'message': 'motion failed', 'full': 'executions/e000007/trace.json[2]'}
    assert value['at_failure']['arms']['left']['eef_pos'] == [.3234,0,.9]
    assert value['at_failure']['arms']['left']['gripper_opening'] == [.0123,.0123]
    assert value['first_failure']['previous'] == [dict(call='left_gripper.move_delta', status='completed'),
                                                dict(call='sensors.move_delta', status='completed')]


@pytest.mark.parametrize('source,status,result', [
    ('left_arm', 'rejected', {'rejected': True, 'reason': 'pre-dispatch refusal'}),
    ('left_arm', 'completed', {'rejected': True, 'reason': 'pre-dispatch refusal'}),
    ('sensors', 'failed', {'message': 'sensor failed'}),
])
def test_request_group_pre_dispatch_or_sensor_failure_before_more_motion(tmp_path, source, status, result):
    root, folder, trace, rows = public_shapes(tmp_path, public_failure(source, status, result))
    value = diag.build(root, '7')
    assert value['at_failure']['row_index'] == 1499 < len(rows)-1
    assert value['at_failure']['alignment'].startswith('request group 1 of 2 (previous dispatched group;')
    assert value['first_failure']['result']['full'] == 'executions/e000007/trace.json[2]'
    for key in result:
        assert value['first_failure']['result'][key] == result[key]


def test_request_group_rejection_before_any_motion_uses_first_row(tmp_path):
    root, folder, trace, rows = public_shapes(tmp_path, public_failure(status='rejected', result={'rejected': True, 'reason': 'no motion'}))
    trace = [trace[2], trace[0], trace[3]]
    write(folder, 'trace.json', trace)
    value = diag.build(root, '7')
    assert value['at_failure']['row_index'] == 0
    assert 'no previous dispatched group' in value['at_failure']['alignment']


@pytest.mark.parametrize('problem,expected', [
    ('prefix', 'dispatched moving calls through failure exceed'),
    ('extra', 'dispatched moving calls !='),
    ('missing', 'state row missing request_id'),
    ('reappears', 'non-contiguous request_id group'),
])
def test_request_group_mapping_inconsistency_is_explained(tmp_path, problem, expected):
    root, folder, trace, rows = public_shapes(tmp_path, public_failure())
    if problem == 'prefix':
        for row in rows:
            row['request_id'] = 'only-one'
    elif problem == 'extra':
        rows[-1]['request_id'] = 'extra-group'
    elif problem == 'missing':
        rows[100].pop('request_id')
    else:
        rows[-1]['request_id'] = rows[0]['request_id']
    write(folder, 'state_log.json', rows)
    value = diag.build(root, '7')
    assert value['at_failure']['row_index'] == 2999
    assert value['at_failure']['alignment'].startswith('last row;')
    assert expected in value['at_failure']['alignment']


def test_large_public_execution_bounds_both_cli_and_file(tmp_path):
    result = dict(rejected=True, reason='safe refusal', diagnostics={
        'reason': 'planner refusal', 'candidate_reasons': ['collision', 'joint margin'],
        'solver_diagnostics': [{'error_message': 'no path', 'trajectory': [0]*10000}],
        'candidate_qpos': [[0]*14]*1000})
    root, folder, trace, rows = public_shapes(tmp_path, public_failure(status='rejected', result=result))
    for i, row in enumerate(rows):
        row['unexpected_contacts'] = [{'bodies': ['arm', 'counter'], 'distance': -.001}]
        row['bilateral_contact_objects'] = {'left': ['cup'] if i%2 else [], 'right': []}
    write(folder, 'state_log.json', rows)
    write(folder, 'result.json', dict(status='completed', error=None, sdk_calls=100, detail='x'*10000))
    for i in range(500):
        path = folder/'frames'/f'{i}.jpg'
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b'fake frame')
    copied_cli(root)
    completed = subprocess.run([sys.executable, 'docs/devtool.py', 'diagnose', '7'], cwd=root, capture_output=True, text=True)
    assert completed.returncode == 0
    value = json.loads(completed.stdout)
    saved = (folder/'diagnose.json').read_text()
    assert len(completed.stdout) <= 4000 and len(saved) <= 4000
    assert len(json.dumps(value, ensure_ascii=False)) <= 4000
    assert json.loads(saved) == value
    assert value['outcome'] == {'status': 'completed', 'error': None}
    first = value['first_failure']
    assert first['call'] == 'left_arm.move_delta' and first['arguments'] == trace[2]['arguments']
    assert first['result'] == dict(rejected=True, reason='safe refusal', full='executions/e000007/trace.json[2]',
                                   diagnostics={'reason': 'planner refusal', 'candidate_reasons': ['collision', 'joint margin'],
                                                'solver_diagnostics': [{'error_message': 'no path'}]})
    assert 0 < len(value['contacts']) <= 5
    assert all(1479 <= r['physics_step']-1 <= 1519 for r in value['contacts'])
    assert value['pointers']['full_files']['frames/']['files'] == 500
    assert 'qpos' not in saved and 'candidate_qpos' not in saved


def test_contact_selection_uses_bilateral_sets_and_global_previous_row():
    rows = [dict(physics_step=i, unexpected_contacts=[], bilateral_contact_objects={'left': ['a','b']}) for i in range(50)]
    rows[5]['bilateral_contact_objects']['left'] = ['b','a']
    rows[20]['bilateral_contact_objects']['left'] = ['cup']
    rows[22]['unexpected_contacts'] = ['collision']
    assert [r['physics_step'] for r in diag.contact_window(rows, 40)] == [20,21,22]


def test_diagnose_opens_only_execution(tmp_path, monkeypatch):
    root, folder, _ = fixture(tmp_path)
    opened = []
    real_open, real_path_open = builtins.open, Path.open
    def check(path):
        path = Path(path).resolve()
        assert folder in path.parents, path
        opened.append(path)
    def guard(path, *args, **kw):
        check(path); return real_open(path, *args, **kw)
    def path_guard(path, *args, **kw):
        check(path); return real_path_open(path, *args, **kw)
    monkeypatch.setattr(builtins, 'open', guard)
    monkeypatch.setattr(Path, 'open', path_guard)
    value = diag.diagnose(root, '7')
    assert value['first_failure'] and opened and folder/'diagnose.json' in opened


def test_symlinks_and_traversal_cannot_open_outside(tmp_path, monkeypatch):
    root, folder, _ = fixture(tmp_path)
    outside = tmp_path/'outside.json'; outside.write_text('private')
    (folder/'trace.json').unlink(); (folder/'trace.json').symlink_to(outside)
    (root/'docs/sdk.json').unlink(); (root/'docs/sdk.json').symlink_to(outside)
    real = Path.open
    def guard(path, *a, **kw):
        assert path.resolve() != outside
        return real(path, *a, **kw)
    monkeypatch.setattr(Path, 'open', guard)
    assert 'first_failure' not in diag.diagnose(root, '7')
    for eid in ('../outside', '/etc/passwd', None, '-1'):
        with pytest.raises(ValueError): diag.diagnose(root, eid)


def test_completion_v2_notes_and_full_failure_v1_identical(tmp_path):
    root, folder, trace = fixture(tmp_path, 'huge '*1300+'END')
    value = dict(status='completed', execution_id='e000007', result={})
    s = summary('e000007', outcome={'status':'failed'}, trace=trace, stdout='x'*1000+'END', stderr='y'*900,
                initial_observation={}, final_observation={}, files={'trace.json':123}, full_failure=True)
    write(folder, 'summary.json', s)
    out = tools.completion(value, root, 'r1')
    block = json.loads(out.split('\n')[0])
    assert block['first_failure'] == trace[2]
    assert 'Full files: executions/e000007/' in out and 'Notes: add a section for e000007' in out
    (root/'work/notes.md').write_text('## e000007 (run) — changed / failed')
    assert 'Notes: add' not in tools.completion(value, root, 'r1')
    (root/'docs/tools.md').write_text(TOOLS)
    assert tools.completion(value, root, 'r1') == tools.show(value, root, 'r1')
    (root/'docs/tools.md').write_text(TOOLS_V2)
    (folder/'summary.json').unlink()
    assert tools.completion(value, root, 'r1') == tools.show(value, root, 'r1')


def test_completion_small_failure_cap_and_essentials(tmp_path):
    root, folder, trace = fixture(tmp_path)
    s = summary('e000007', outcome={'status':'failed'}, trace=trace, stdout='a'*2000, stderr='b'*2000,
                initial_observation={}, final_observation={}, files={})
    write(folder, 'summary.json', s)
    value = dict(status='completed', result={'execution_id':'e000007'})
    out = tools.completion(value, root, 'r1')
    assert len(out) <= 4000
    block = json.loads(out.split('\n')[0])
    assert block['first_failure'] == trace[2] and block['sdk_call_count'] == 4
    assert len(block['stdout_tail']) == len(block['stderr_tail']) == 600
    assert 'robot' in block and 'objects_moved' in block


def test_notes_latest_id_boundaries_and_template(tmp_path):
    root, folder, _ = fixture(tmp_path)
    assert tools.notes_check(root) == {'execution_id':'e000007', 'mentioned':False}
    (root/'work/notes.md').write_text('e0000079')
    assert not tools.notes_check(root)['mentioned']
    (root/'work/notes.md').write_text('## e000007 (run)')
    assert tools.notes_check(root)['mentioned']
    (root/'executions/e000010').mkdir()
    assert tools.notes_check(root)['execution_id'] == 'e000010'
    assert tools.NOTES_TEMPLATE == '## eN (vN or run) — what changed / result / first failure / why / next'


def copied_cli(root):
    for module in (tools, diag, sys.modules[summary.__module__]):
        (root/'docs'/Path(module.__file__).name.replace('dev_agent_tools.py','devtool.py')).write_bytes(Path(module.__file__).read_bytes())


def test_copied_standalone_cli_diagnose_and_notes(tmp_path):
    root, folder, _ = fixture(tmp_path)
    copied_cli(root)
    def run(*args):
        return subprocess.run([sys.executable, 'docs/devtool.py', *args], cwd=root, capture_output=True, text=True)
    out = run('diagnose','7')
    assert out.returncode == 0 and len(out.stdout.rstrip('\n')) <= 4000
    assert json.loads(out.stdout)['first_failure']['index'] == 2 and (folder/'diagnose.json').is_file()
    assert json.loads(run('notes','check').stdout)['execution_id'] == 'e000007'
    assert run('notes','template').stdout.rstrip() == tools.NOTES_TEMPLATE
    (root/'docs/tools.md').write_text(TOOLS)
    assert run('diagnose','7').returncode == 2


def command_factory(root, prompt):
    return ['codex','-a','never','exec','-C',str(root),'--json','-m','gpt-6-astra',
            '-c','model_reasoning_effort="low"','-c','default_permissions="ws"',prompt]


@pytest.mark.parametrize('resumed',[False,True])
def test_baseline_byte_identical_and_slim_exactly_three_flags(tmp_path, resumed):
    # Execute the task's fixed base revision as the command oracle, even after commits.
    source = subprocess.check_output(['git','show','9fa75be:benchmarks/attention_harness/dev_agent_session.py'], text=True)
    old = types.ModuleType('benchmarks.attention_harness._session_before_think')
    old.__package__ = 'benchmarks.attention_harness'
    exec(compile(source,'old_session','exec'), old.__dict__)
    baseline = CodexAgentSession(tmp_path, tmp_path/'base', command_factory=command_factory)
    oracle = old.CodexAgentSession(tmp_path, tmp_path/'old', command_factory=command_factory)
    slim = CodexAgentSession(tmp_path, tmp_path/'slim', command_factory=command_factory, dev_context_profile='slim')
    for session in (baseline, oracle, slim): session.thread_id = 'exact-thread'
    expected = oracle.command(resumed)
    assert json.dumps(baseline.command(resumed)) == json.dumps(expected)
    command = slim.command(resumed)
    added = ['-c','model_auto_compact_token_limit=80000','-c','tool_output_token_limit=4000','-c','compact_prompt='+json.dumps(COMPACT_PROMPT)]
    i = command.index(added[1])-1
    assert command[i:i+6] == added and command[:i]+command[i+6:] == expected
    assert 'work/notes.md' in COMPACT_PROMPT and 'last 3 execution ids' in COMPACT_PROMPT and 'verbatim' in COMPACT_PROMPT


def test_profiles_refused_unknown_v1_slim_and_resume_drift(tmp_path):
    for value in ('invalid', None, 3):
        with pytest.raises(ValueError): CodexAgentSession(tmp_path,tmp_path/'private',dev_context_profile=value)
        with pytest.raises(ValueError): normalize(dict(SELECTION,dev_context_profile=value))
    with pytest.raises(ValueError,match='v2'): normalize(dict(SELECTION,dev_context_profile='slim'))
    assert context_profile({}) == 'baseline'
    assert normalize(dict(SELECTION,agent_policy_version='agent-policy-v2',dev_context_profile='slim'))['dev_context_profile'] == 'slim'
    private = tmp_path/'private'; private.mkdir()
    s = CodexAgentSession(tmp_path,private,command_factory=command_factory,dev_context_profile='slim')
    s.thread_id = 'thread'; s._save()
    assert json.loads((private/'session.json').read_text())['dev_context_profile'] == 'slim'
    with pytest.raises(SessionError,match='changed'): CodexAgentSession(tmp_path,private)
    assert CodexAgentSession(tmp_path,private,dev_context_profile='slim').thread_id == 'thread'


def test_profile_manifest_and_configs_frozen(tmp_path, offline_dev_identity):
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    from benchmarks.attention_harness import dev_experiment as exp
    from benchmarks.attention_harness.dev_agent_policy_freeze import validate_comparison
    store = DevV2Store(tmp_path/'db')
    result = exp.create(store,tmp_path/'runs',dict(SELECTION,dev_context_profile='slim'))
    manifest = exp.verify_manifest(result)
    assert manifest['dev_context_profile'] == manifest['selection']['dev_context_profile'] == 'slim'
    for config in manifest['configs'].values():
        assert context_profile(config,manifest) == 'slim'
        from openarm365_dev_v2.dev_config import validate_dev_config
        with pytest.raises(ValueError, match='unsupported_dev_context_profile'):
            validate_dev_config(dict(config, dev_context_profile='unknown'))
        with pytest.raises(ValueError, match='requires agent policy v2'):
            validate_dev_config(dict(config, agent_policy_version='agent-policy-v1'))
    changed = copy.deepcopy(manifest); changed['dev_context_profile'] = 'baseline'
    with pytest.raises(ValueError,match='mismatch'): context_profile(next(iter(manifest['configs'].values())),changed)
    with pytest.raises(ValueError,match='comparison_conditions'): validate_comparison([manifest,changed])


def test_v1_tools_bytes_fixed_base_and_v2_guide(tmp_path):
    source = subprocess.check_output(['git','show','9fa75be:benchmarks/attention_harness/dev_agent_workspace.py'],text=True)
    tree = ast.parse(source)
    before = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TOOLS' for t in n.targets))
    assert TOOLS.encode() == before.encode()
    from benchmarks.attention_harness.tests.test_dev_agent_d1 import CONFIG
    w = Workspace(tmp_path/'v2').prepare({},dict(CONFIG,agent_policy_version='agent-policy-v2'))
    assert 'If your context was compacted' in (w.ws/'docs/guide.md').read_text()
    assert (w.ws/'docs/dev_agent_diagnose.py').is_file()
    v1 = Workspace(tmp_path/'v1').prepare({},CONFIG)
    assert 'compacted' not in (v1.ws/'docs/guide.md').read_text()
    assert not (v1.ws/'docs/dev_agent_diagnose.py').exists()


@pytest.mark.parametrize('op',['run','submit'])
def test_broker_publishes_diagnosis_and_cli_only_reads_it(tmp_path, monkeypatch, op):
    from benchmarks.attention_harness.tests.test_dev_agent_policy_v2_flow import Rig, CONFIG
    r = Rig(tmp_path,'autonomous')
    try:
        r.ws.prepare({},dict(CONFIG,agent_policy_version='agent-policy-v2'))
        response = r.call('run') if op == 'run' else r.submit()
        assert response['status'] == 'completed'
        eid = response['execution_id']
        folder = r.ws.ws/'executions'/eid
        assert (folder/'diagnose.json').read_bytes() == (r.ws.canonical/'executions'/eid/'diagnose.json').read_bytes()
        assert len((folder/'diagnose.json').read_text()) <= 4000
        assert b'\n  ' not in (folder/'diagnose.json').read_bytes()
        def no_write(*a, **kw):
            raise AssertionError('Dev must not write to the read-only execution mount')
        monkeypatch.setattr(Path,'write_text',no_write)
        value = diag.diagnose(r.ws.ws,eid)
        assert value['first_failure']['error']['message'] == 'grasp missed'
        assert 'devtool.py diagnose '+eid in tools.completion(response,r.ws.ws,'r1')
    finally:
        r.close()


def test_session_turn_evidence_records_profile_on_start_and_resume(tmp_path):
    from benchmarks.attention_harness.tests.test_dev_agent_d2 import fake_session, CONFIG
    ws = Workspace(tmp_path/'agent').prepare({},dict(CONFIG,agent_policy_version='agent-policy-v2'))
    s = fake_session(ws,['pass','pass'])
    s.dev_context_profile = 'slim'
    list(s.start('start'))
    list(s.resume(s.thread_id,'resume'))
    for turn in ('t000001','t000002'):
        evidence = json.loads((ws.private/'turns'/turn/'input.json').read_text())
        assert evidence['dev_context_profile'] == 'slim'
        assert 'model_auto_compact_token_limit=80000' in evidence['command']
        assert 'tool_output_token_limit=4000' in evidence['command']
        assert any(v.startswith('compact_prompt=') for v in evidence['command'])


def test_late_help_refreshes_published_diagnosis(tmp_path):
    from benchmarks.attention_harness.tests.test_dev_agent_policy_v2_flow import Rig
    r = Rig(tmp_path)
    try:
        response = r.submit()
        eid = response['execution_id']
        before = diag.diagnose(r.ws.ws,eid)
        assert 'help.json' not in before['pointers']['full_files']
        assert r.call('ask_help',{'question':'Why did the motion fail?'})['status'] == 'completed'
        after = diag.diagnose(r.ws.ws,eid)
        assert 'help.json' in after['pointers']['full_files']
        assert any('help.json: executions/' in line for line in after['pointers']['lines'])
    finally:
        r.close()


def test_v1_session_evidence_stays_unchanged_v2_baseline_is_recorded(tmp_path):
    private = tmp_path/'private'; private.mkdir()
    s = CodexAgentSession(tmp_path,private,command_factory=command_factory)
    s.thread_id = 'thread'; s._save()
    assert json.loads((private/'session.json').read_text()) == {'thread_id':'thread','turn':0}
    s.record_context_profile = True; s._save()
    assert json.loads((private/'session.json').read_text())['dev_context_profile'] == 'baseline'


def test_broker_diagnosis_failure_never_fails_settlement():
    # Reviewer addition (11-14): the diagnose call in settlement is isolated from the settlement try block.
    from pathlib import Path
    from benchmarks.attention_harness import dev_agent_broker
    src = Path(dev_agent_broker.__file__).read_text()
    block = src[src.index("from .dev_agent_diagnose import build as build_diagnosis") - 400:src.index("from .dev_agent_diagnose import build as build_diagnosis") + 400]
    assert 'try:' in block and "errors/diagnose-'+eid+'.json'" in block and 'except Exception as exc' in block


def test_frames_are_summarised_in_full_files(tmp_path):
    import json
    from benchmarks.attention_harness.dev_agent_diagnose import build
    folder = tmp_path / 'executions' / 'e000001'
    (folder / 'frames').mkdir(parents=True)
    for i in range(50):
        (folder / 'frames' / f'f{i:03d}.jpg').write_bytes(b'x' * 10)
    (folder / 'result.json').write_text(json.dumps({'status': 'failed'}))
    (folder / 'trace.json').write_text('[]')
    out = build(tmp_path, 'e000001')
    files = out['pointers']['full_files']
    assert files['frames/'] == {'files': 50, 'bytes': 500}
    assert not any(k.startswith('frames/f') for k in files)


def test_contact_setting_labels_are_not_reported_as_contacts():
    # Reviewer addition (11-14): constant fixture_contact_* settings appear in every published row.
    from benchmarks.attention_harness.dev_agent_diagnose import contacts_in
    row = {'unexpected_contacts': [['a', 'b', -0.001]], 'bilateral_contact_objects': {'left': []},
           'fixture_contact_mode': 'record_only', 'fixture_contact_radius_m': 0.01,
           'fixture_press_contact_radius_m': 0.035, 'fixture_contact_base_rehold_max_yaw_rad': 0.1}
    assert set(contacts_in(row)) == {'unexpected_contacts', 'bilateral_contact_objects'}


def test_completion_recovers_a_first_failure_that_an_older_summary_shortened(tmp_path):
    # Reviewer addition (11-14): exercise the fallback that re-reads the public trace row.
    import json
    from benchmarks.attention_harness import dev_agent_tools as tools
    from benchmarks.attention_harness.dev_agent_workspace import TOOLS_V2
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs' / 'tools.md').write_text(TOOLS_V2)
    folder = tmp_path / 'executions' / 'e000003'
    folder.mkdir(parents=True)
    long_error = 'RuntimeError: ' + 'x' * 5000
    (folder / 'trace.json').write_text(json.dumps([
        {'source': 'right_arm', 'operation': 'move_delta', 'arguments': {'args': [0, 0, .05]}, 'status': 'failed',
         'error': {'type': 'RuntimeError', 'message': long_error}}]))
    (folder / 'summary.json').write_text(json.dumps({'outcome': {'status': 'failed'}, 'first_failure': 'shortened…'}))
    out = tools.completion({'status': 'completed', 'execution_id': 'e000003'}, tmp_path, 'r1')
    assert long_error in out and 'shortened' not in out
