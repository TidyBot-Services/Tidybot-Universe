import hashlib, json, math
import numpy as np
import pytest
from benchmarks.attention_harness.diagnostics.grasp_chain_probe import (
    theta_quat, grasp_offset_xy, slip_metrics, sequenced_policy_source,
    variant_policy_source, summarize)
from benchmarks.attention_harness.diagnostics.grasp_variants import parse_variants,format_table
from openarm365_dev.executor import diagnostic_held_trace_enabled,held_trace_value

Q=(.3007057995042732,.9537169507482269,0,0)
ORACLE={'position_world_m':[.4969098174320634,-2.37729484580756,.9348288902306693],
        'z_range_world_m':[.92,.96]}

def test_theta_and_signed_offset():
    assert theta_quat(55)==pytest.approx(Q,abs=1e-14)
    assert theta_quat(70)==pytest.approx((.17364817766693033,.984807753012208,0,0))
    dx,dy=grasp_offset_xy(Q,10)
    assert math.hypot(dx,dy)==pytest.approx(.01)
    assert (dx,dy)==pytest.approx((-0.00819152044288992,.00573576436351046))
    assert grasp_offset_xy(Q,-10)==pytest.approx((-dx,-dy))

def test_variant_default_exact_bytes_and_lift():
    source=sequenced_policy_source((0,.25,0),Q,ORACLE,sequence='translate-first',rotation_step=.15)
    assert hashlib.sha256(source.encode()).hexdigest()=='fa35ed3c846d4a44b628eec49f53a9be53ab6771bce66c07d8db3c1114534868'
    assert variant_policy_source(source,Q)==source
    altered=variant_policy_source(source,Q,offset_mm=10,z_offset_mm=-3,lift_step_m=.03)
    assert 'oracle_center[0]+-' in altered
    assert 'grasp_z = (oracle_z[0]+oracle_z[1])/2+-0.003' in altered
    assert 'for lift_delta_z in [0.03, 0.03, 0.03, 0.010000000000000009]:' in altered

def test_slip_and_variant_table():
    samples=[dict(checkpoint='close',object_position=[0,0,0],eef_position=[0,0,0]),
             dict(checkpoint='lift_1',object_position=[.003,.004,0],eef_position=[0,0,0])]
    rows,maximum=slip_metrics(samples)
    assert maximum==pytest.approx(5)
    assert rows[1]['relative_obj_eef_mm']==pytest.approx([3,4,0])
    parsed=parse_variants('a:theta=55,hold=0,offset=0,z=0,lift=0.03;b:theta=70,offset=-5')
    assert [name for name,_ in parsed]==['a','b']
    assert '| a | 6 | 2 | True | slip | [0.02, 0.02] |' in format_table([
        dict(variant='a',object_lift_cm=6,max_slip_mm=2,monitor_aborted=True,
             aborted_by='slip',finger_openings_after_close_m=[.02,.02])])
    with pytest.raises(ValueError,match='nonzero hold'):parse_variants('a:theta=55,hold=1')

def test_held_trace_flag_and_fields(monkeypatch):
    monkeypatch.delenv('ATTN_DIAG_HELD_TRACE',raising=False)
    assert diagnostic_held_trace_enabled() is False
    monkeypatch.setenv('ATTN_DIAG_HELD_TRACE','1')
    assert diagnostic_held_trace_enabled() is True
    for invalid in ('0','true','', '2'):
        monkeypatch.setenv('ATTN_DIAG_HELD_TRACE',invalid)
        with pytest.raises(ValueError):diagnostic_held_trace_enabled()
    ref=np.eye(4);relative=np.eye(4);relative[0,3]=.002
    row=held_trace_value('banana',relative,ref,{'left':2.,'right':3.},
        {'left':[-.2],'right':[-.1]})
    assert row['object_body']=='banana' and row['rel_pos_tcp_mm']==[2.,0.,0.]
    assert row['rel_rot_deg']==0 and row['reference']==ref.tolist()
    assert row['finger_contact_force_n']['right']==3.

def test_abort_uses_final_observation(tmp_path):
    version=tmp_path/'artifacts'/'e'/'cells'/'c'/'v1';version.mkdir(parents=True)
    episode=version/'executions'/'openarm365-dev-test';episode.mkdir(parents=True)
    def obs(z):return {'robot':{'robot0_left_eef_pos':[0,0,z]},
                       'perception':{'values':{'obj_pos':[0,0,z]}}}
    (episode/'initial_public_observation.json').write_text(json.dumps(obs(.9)))
    (episode/'final_public_observation.json').write_text(json.dumps(obs(.96)))
    row=summarize(tmp_path,102,(0,.25,0),'movable_objects',False,1.,
                  'RuntimeError: Measured object/hand transform exceeds original 4mm/0.03rad bound')
    assert row['object_lift_cm']==pytest.approx(6)
    assert row['object_lifted_5cm'] is True and row['monitor_aborted'] is True
