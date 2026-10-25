"""C5 offline rejections through the real client, planner and public boundaries."""
import copy
import io
import json
from types import SimpleNamespace as NS

import pytest

from curobo_v2.client import PlanningRejected, V2Client
from openarm365_dev import planner as module, sandbox
from openarm365_dev.owner import build_sdk
from openarm365_dev.planning_rejection import call_with_rejection_facts, rejection_fact
from openarm365_dev_v2.tests.test_mujoco_ik_mode import planner, request
from openarm365_dev_v2.tests.test_r3_config import baseline
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment

MARGIN = 'ik_joint_margin_below_execution_limit'
START = 'start_state_collision_or_constraint_violation'


def response(reason):
    if reason == MARGIN:
        values = [0.000035, 0.00018, 0.00007, 0.00009, 0.00005, 0.00006, 0.00008, 0.0001]
        return dict(status='failed', reason=reason, ik_candidates=8, joint_margin_limit_rad=.02,
                    candidate_joint_margins=[dict(minimum_joint_margin_rad=v,
                        joint_margins_rad={'openarm_left_joint1':.3, 'openarm_left_joint7':v}) for v in values])
    return dict(status='failed', reason=reason, validation=dict(joint_limits=True,
                exact_geometry={'collision_free':True}, proxy_collision_free=False,
                attachment={'sphere_count':54, 'shape_names':['object-inventory-only']}))


def client_for(tmp_path, monkeypatch, value):
    client = V2Client.__new__(V2Client)
    client.evidence = tmp_path
    client.nonce = 'test'; client.lock_sha = 'test'; client.identity = {}
    client.worlds = {'robocasa365':(1, 'world')}; client.failed = False
    client._check_live = lambda: None
    client.attest = lambda: {}
    client._request = lambda path, body: copy.deepcopy(value) if path.startswith('/plan') else {}
    monkeypatch.setattr('curobo_v2.client.time.time_ns', lambda: 123)
    return client


def configure(planner, monkeypatch, value, settings):
    planner.execution_settings = settings
    planner.client = client_for(planner.evidence, monkeypatch, value)
    planner.ledger.invalidate = lambda reason: None
    return planner


@pytest.mark.parametrize('reason', [MARGIN, START])
def test_r3_planner_appends_current_response_facts(planner, monkeypatch, r3_deployment, reason):
    value = response(reason)
    configure(planner, monkeypatch, value, r3_deployment)
    with pytest.raises(PlanningRejected) as caught:
        request(planner)
    message = str(caught.value)
    assert message.startswith('cuRoboV2 planning rejected: ' + reason + '; ')
    if reason == MARGIN:
        assert 'openarm_left_joint7 at 0.00018 rad' in message
        assert 'required 0.02 rad' in message and 'all 8 IK candidates' in message
        assert '0.000035' not in message  # Best minimum, not worst minimum.
    else:
        assert message.endswith('checks failing at the start state: sphere proxy (carried object present; exact geometry clear)')
        assert 'object-inventory-only' not in message
    assert planner.failed and planner.client.failed and not planner.ledger.issued
    evidence = json.loads((planner.evidence/'v2_client.jsonl').read_text().splitlines()[-1])
    assert evidence['response'] == value
    assert evidence['error'] == 'PlanningRejected: cuRoboV2 planning rejected: ' + reason


@pytest.mark.parametrize('exact,proxy,joints', [
    (False,False,False), (False,True,True), (True,False,True), (True,True,False),
    (None,False,None), (None,None,None)])
def test_start_names_only_explicitly_failed_checks(exact, proxy, joints):
    fact = rejection_fact(dict(reason=START, validation=dict(
        exact_geometry={'collision_free':exact}, proxy_collision_free=proxy, joint_limits=joints)))
    failing = fact.split(' (')[0]
    assert ('exact geometry' in failing) == (exact is False)
    assert ('sphere proxy' in failing) == (proxy is False)
    assert ('joint limits' in failing) == (joints is False)
    assert ('exact geometry clear' in fact) == (exact is True)


def test_contacts_limited_to_two_names_and_no_inventory():
    value = response(START)
    value['validation']['exact_geometry'] = dict(collision_free=False, contacts=[
        {'bodies':['carried-object','counter']}, {'bodies':['counter','third-body']}])
    fact = rejection_fact(value)
    assert 'exact geometry, sphere proxy' in fact
    assert 'colliding bodies: carried-object, counter' in fact
    assert 'third-body' not in fact and 'exact geometry clear' not in fact


def test_partial_margin_candidates_do_not_claim_all_failed():
    value = response(MARGIN); value['candidate_joint_margins'][1] = None
    fact = rejection_fact(value)
    assert '0.0001 rad' in fact and '7 of 8 IK candidates have recorded margins' in fact
    assert 'all 8' not in fact


@pytest.mark.parametrize('value', [
    {'reason':MARGIN}, {'reason':MARGIN,'candidate_joint_margins':[{}],'joint_margin_limit_rad':.02},
    {'reason':MARGIN,'candidate_joint_margins':[None],'joint_margin_limit_rad':.02},
    {'reason':START,'validation':None}, {'reason':'another_rejection'},
    {'reason':MARGIN,'candidate_joint_margins':[{'minimum_joint_margin_rad':float('nan')}],'joint_margin_limit_rad':.02}])
def test_missing_or_malformed_facts_omit_suffix(value):
    assert rejection_fact(value) == ''


@pytest.mark.parametrize('mode', ['stale','wrong_path','wrong_request','bad_json','missing','unrelated_error'])
def test_no_stale_or_unmatched_response(tmp_path, mode):
    value = response(MARGIN); body = {'env_id':'robocasa365'}
    original = 'cuRoboV2 planning rejected: ' + MARGIN
    row = dict(path='/plan', request=body, response=value, error='PlanningRejected: '+original)
    evidence = tmp_path/'v2_client.jsonl'
    evidence.write_text(json.dumps(row)+'\n')
    def call(path, request):
        new = copy.deepcopy(row)
        if mode == 'wrong_path': new['path'] = '/plan/ik_path'
        if mode == 'wrong_request': new['request'] = {}
        if mode == 'missing': evidence.unlink()
        elif mode != 'stale':
            with evidence.open('a') as stream:
                stream.write(('bad json' if mode == 'bad_json' else json.dumps(new))+'\n')
        raise PlanningRejected('other error' if mode == 'unrelated_error' else original)
    with pytest.raises(PlanningRejected) as caught:
        call_with_rejection_facts(NS(call=call,evidence=tmp_path), '/plan', body, NS(profile='r3-20261025'))
    assert str(caught.value) == ('other error' if mode == 'unrelated_error' else original)


@pytest.mark.parametrize('reason', [MARGIN, START])
@pytest.mark.parametrize('settings', [None, NS(profile='future-profile')])
def test_legacy_rejection_message_receipt_and_evidence_bytes(planner, monkeypatch, reason, settings):
    old = baseline('openarm365_dev/planner.py')
    results = []
    for cls in (old.MujocoV2Planner, module.MujocoV2Planner):
        for file in planner.evidence.glob('*.jsonl'): file.unlink()
        configure(planner, monkeypatch, response(reason), settings)
        planner.failed = False
        planner.plan = cls.plan.__get__(planner)
        with pytest.raises(PlanningRejected) as caught:
            request(planner)
        message = f'{type(caught.value).__name__}: {caught.value}'
        receipt = dict(ok=False, error=message, sequence=1)
        results.append((message.encode(), json.dumps(receipt).encode(),
                        {f.name:f.read_bytes() for f in planner.evidence.iterdir() if f.is_file()}))
    assert results[0] == results[1]


@pytest.mark.parametrize('reason', [MARGIN, START])
def test_sandbox_trace_dev_eval_advisor_and_memory(planner, monkeypatch, r3_deployment, tmp_path, reason):
    from benchmarks.attention_harness.dev_loop import public_feedback
    from benchmarks.attention_harness.dev_help import AdvisorProvider
    from benchmarks.attention_harness.v2_evaluator import build_eval_evidence, evaluator_messages
    from benchmarks.attention_harness.memory_provisional import derive_signature, eval_reason_class, signature_present
    from openarm365_dev_v2.tests.test_dev_feedback_projection import messages
    configure(planner, monkeypatch, response(reason), r3_deployment)
    # Fake only the native transport/processes; exercise SDK event and sandbox
    # error serialization with the actual planner/client rejection.
    def rpc(*args):
        try: request(planner)
        except PlanningRejected as exc: raise RuntimeError(f'PlanningRejected: {exc}') from exc
    events = []
    sdk = build_sdk(NS(rpc=rpc), events.append)
    process = NS(stdin=io.BytesIO(), stdout=io.BytesIO(), returncode=0, poll=lambda:0)
    monkeypatch.setattr(sandbox.subprocess, 'Popen', lambda *a,**k:process)
    monkeypatch.setattr(sandbox, '_bwrap_prefix', lambda *a:[])
    monkeypatch.setattr(sandbox.selectors, 'DefaultSelector', lambda:NS(register=lambda *a:None,
        select=lambda **k:[True], close=lambda:None))
    monkeypatch.setattr(sandbox.os, 'read', lambda *a:json.dumps(dict(kind='call',group='left_arm',
        operation='move_delta',args=[],kwargs={'dz':.01})).encode()+b'\n')
    class Output(io.BytesIO):
        def fileno(self): return 0
    process.stdout = Output()
    outcome = sandbox.execute_formal_policy(code='from robot_sdk import left_arm\nleft_arm.move_delta(dz=0.01)',
        sdk=sdk,context={},deadline=float('inf'),stderr_path=tmp_path/'stderr.txt')
    trace = dict(status=outcome.status, error=outcome.error, sdk_events=events)
    (tmp_path/'trace.json').write_text(json.dumps(trace))
    feedback = public_feedback(trace)
    original = 'RuntimeError: PlanningRejected: cuRoboV2 planning rejected: ' + reason
    suffix = feedback['public_error'][len(original):]
    assert suffix.startswith('; ') and suffix == '; ' + rejection_fact(response(reason))
    assert events[-1]['error']['message'].endswith(suffix)
    assert feedback['action_trace'][-1]['error']['message'].endswith(suffix)
    packets = [json.loads(messages(feedback)[-1]['content']),
               json.loads(AdvisorProvider().messages('open_cabinet','print(1)',feedback)[-1]['content']),
               json.loads(evaluator_messages(build_eval_evidence(tmp_path,
                   {'task_id':'open_cabinet','task_prompt':'Open cabinet'}),text_only=True)[-1]['content'])]
    for packet in packets:
        assert packet['public_feedback']['public_error'] == outcome.error
        assert packet['public_feedback']['action_trace'][-1]['error']['message'].endswith(suffix)
    before = dict(feedback,public_error=original)
    assert derive_signature(before) == derive_signature(feedback) == {
        'public_error_class':'RuntimeError: PlanningRejected','operation':'left_arm.move_delta'}
    assert signature_present(feedback, derive_signature(before))
    assert eval_reason_class({'feedback':original}) == eval_reason_class({'feedback':outcome.error}) == 'planner_rejected'
