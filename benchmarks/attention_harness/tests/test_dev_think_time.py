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
    write(root, 'docs/sdk.json', {'joint_limits': {f'openarm_left_joint{i}': [-1, 1] for i in range(1, 8)}})
    (root/'docs/tools.md').write_text(TOOLS_V2)
    (root/'work').mkdir()
    (root/'work/notes.md').write_text('')
    return root, folder, trace


def test_diagnose_complete_distance_contacts_movement_and_margins(tmp_path):
    root, folder, trace = fixture(tmp_path)
    value = diag.diagnose(root, '7')
    assert list(value)[:2] == ['outcome', 'first_failure']
    first = value['first_failure']
    assert first['index'] == 2 and first['call'] == 'left_arm.move_to'
    assert first['arguments'] == trace[2]['arguments'] and len(first['previous']) == 2
    assert first['planner_reason'] == ['ik_path_joint_jump']
    arm = value['at_failure']['arms']['left']
    assert arm['distance_to_target_m'] == pytest.approx(.3)
    assert arm['eef_quat'] == [0, 0, 0, 1] and arm['gripper_opening'] == [.02, .02]
    assert value['at_failure']['row_index'] == 30
    assert [v['row_index'] for v in value['contacts']] == list(range(10, 51))
    assert 'hidden_unrelated' not in json.dumps(value['contacts'])
    assert value['objects_moved']['cup']['moved_m'] == .02
    assert [v['margin_rad'] for v in value['joint_margins']] == pytest.approx([.01, .1, .2])
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
    assert 'joint_margins' not in value and any('no named joint limits' in n for n in value['notes'])
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


def test_diagnose_opens_only_execution_and_sdk(tmp_path, monkeypatch):
    root, folder, _ = fixture(tmp_path)
    opened = []
    real_open, real_path_open = builtins.open, Path.open
    def check(path):
        path = Path(path).resolve()
        assert folder in path.parents or path == root/'docs/sdk.json', path
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
