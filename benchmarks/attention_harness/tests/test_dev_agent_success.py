import json
from pathlib import Path
import time

import pytest

from benchmarks.attention_harness import dev_agent_success as success
from benchmarks.attention_harness.dev_agent_eval import Evaluator
from benchmarks.attention_harness.tests.test_dev_agent_d4 import eval_factory, seed_record, ws  # noqa: F401

COND = success.load('pick_place_counter_to_sink', 102)
SINK = [0.2999998883379904, -1.7962400049525014, 0.9861874579216556]


def final(obj, left=(0.45, -1.4, 1.30), right=(0.41, -1.35, 1.09)):
    return {'perception': {'values': {'obj_pos': list(obj), 'sink_pos': SINK}},
            'robot': {'robot0_left_eef_pos': list(left), 'robot0_right_eef_pos': list(right)}}


def test_frozen_condition_is_public_geometry_only():
    assert COND['sha256'] == success.digest({k: v for k, v in COND.items() if k != 'sha256'})
    text = json.dumps(COND)
    for word in ('native_success', 'object_in_sink', 'native_result', 'check_success', 'reward'):
        assert word not in text
    assert COND['container']['public_scene_check']['value'] == SINK
    box = COND['region']['axis_aligned_box_world_m']
    assert box['y'][0] == pytest.approx(-1.9724, abs=1e-4) and box['x'][1] == pytest.approx(.5196, abs=1e-4)
    assert success.load('pick_place_counter_to_cabinet', 102) is None


def test_tampered_condition_refused(tmp_path, monkeypatch):
    raw = json.loads((success.ROOT/'pick_place_counter_to_sink-seed102.json').read_text())
    raw['region']['interior_sites_world_m'][0][1] -= .2
    monkeypatch.setattr(success, 'ROOT', tmp_path)
    (tmp_path/'pick_place_counter_to_sink-seed102.json').write_text(json.dumps(raw))
    with pytest.raises(ValueError): success.load('pick_place_counter_to_sink', 102)


@pytest.mark.parametrize('obj,inside', [
    ((0.414, -2.0535, 0.860), False),   # d7e2e2 v2-v7: beside the basin, native false
    ((0.497, -2.377, 0.935), False),    # untouched on the counter
    ((0.471, -1.889, 0.751), True),     # r6 v3: native success
    ((0.30, -1.70, 0.95), False),       # above the interior box top
])
def test_rule_matches_recorded_native_outcomes(obj, inside):
    value = success.check(COND, final(obj))
    assert value['inside'] is inside
    assert value['passed'] is inside


def test_gripper_must_be_far():
    value = success.check(COND, final((0.471, -1.889, 0.751), left=(0.47, -1.89, 0.85)))
    assert value['inside'] and not value['grippers_far'] and not value['passed']


def test_redaction_hides_bounds_but_keeps_object_position():
    text = 'Object at y=-2.054 is outside: the basin spans y -1.972 to -1.424 and x 0.140..0.520.'
    out = success.redact(text, COND)
    assert '-2.054' in out and '-1.972' not in out and '-1.424' not in out and '0.520' not in out and '0.140' not in out


def test_dev_view_of_real_smoke_verdict_leaks_no_rule():
    """Verbatim gpt-6.1-sol verdict from the 10-29 offline smoke, whose first redaction leaked."""
    obs = final((0.496910, -2.377295, 0.934829), left=(0.4500, -2.2, 1.0994), right=(0.41, -1.35, 1.09))
    verdict = dict(passed=False, reason='The settled object position, (0.496910, -2.377295, 0.934829) m, is outside the sink: '
                   'projections 1 and 3 fail containment. The left gripper also remains only 0.209900 m away, failing the required clearance.',
                   advice='After settling, verify basin containment and both gripper distances exceed 0.25 m.',
                   evidence=[dict(source='final_observation.json: perception.values.obj_pos; success_condition.json: '
                                         'region.interior_sites_world_m and region.projection_ranges',
                                  claim='Its three rule-defined projections are -1.304486 (below range), -0.188615 (inside range), '
                                        'and 0.195 (above range).'),
                             dict(source='success_condition.json: rule.gripper_far_m', claim='The left gripper fails the required 0.25 m clearance.')])
    view = success.dev_view(verdict, COND, obs)
    text = json.dumps(view)
    for leaked in ('success_condition', 'interior_sites', 'projection_ranges', 'gripper_far', '1.304486', '0.188615', '0.25 m'):
        assert leaked not in text, leaked
    assert '(0.496910, -2.377295, 0.934829)' in view['reason'] and 'outside the sink' in view['reason']
    assert view['passed'] is False and 'Eval-only success rule' in view['evidence'][0]['source']


def test_eval_workspace_gets_condition_and_dev_does_not(ws):
    execution = seed_record(ws)
    ws.publish('executions/e000001/final_observation.json', final((0.414, -2.0535, 0.860)))
    verdict = dict(passed=False, reason='obj_pos y -2.054 is below the basin low bound -1.972',
                   evidence=[dict(claim='basin x from 0.140 to 0.520', source='success_condition.json')],
                   advice='Release over the basin; y must exceed -1.972.')
    ev = Evaluator(ws, command_factory=eval_factory(ws, json.dumps(verdict)), success_condition=COND)
    result = ev.evaluate(execution, deadline=time.monotonic()+30)
    root = ws.private/'eval/v0001'
    view = json.loads((root/'ws'/success.FILE).read_bytes())
    assert view['sha256'] == COND['sha256'] and 'derivation' not in view
    assert (root/'ws/final_observation.json').is_file()
    assert json.loads((root/'verdict.json').read_bytes())['reason'] == verdict['reason']
    assert result['passed'] is False and '-2.054' in result['reason']
    for text in (result['reason'], result['advice'], result['evidence'][0]['claim']):
        assert '1.972' not in text and '0.520' not in text and '0.140' not in text
    # Dev's workspace never contains the file or the region bounds.
    for path in ws.ws.rglob('*'):
        if path.is_file():
            assert path.name != success.FILE
            assert b'interior_sites_world_m' not in path.read_bytes()


def test_without_condition_eval_workspace_is_unchanged(ws):
    execution = seed_record(ws)
    ev = Evaluator(ws, command_factory=eval_factory(ws))
    ev.evaluate(execution, deadline=time.monotonic()+30)
    root = ws.private/'eval/v0001/ws'
    assert not (root/success.FILE).exists() and not (root/'final_observation.json').exists()


def test_manifest_freezes_condition_only_for_matching_configs():
    configs = {'pick_place_counter_to_sink:102': dict(task_id='pick_place_counter_to_sink', seed=102),
               'pick_place_counter_to_sink:101': dict(task_id='pick_place_counter_to_sink', seed=101)}
    frozen = success.for_configs(configs)
    assert list(frozen) == ['pick_place_counter_to_sink:102'] and frozen['pick_place_counter_to_sink:102'] == COND
    assert 'never quote the region' in success.EVAL_RULE
