"""Offline coverage for the native tracking diagnostic and matrix renderer."""
import json
from types import SimpleNamespace as NS
from unittest.mock import MagicMock,patch

import numpy as np
import pytest

from openarm365_dev import executor
from curobo_v2.client import PlanningRejected
from benchmarks.attention_harness.diagnostics import motion_tracking_matrix as matrix


def test_diagnostic_defaults_and_overrides(monkeypatch):
    monkeypatch.delenv('ATTN_DIAG_SETTLE_STEPS',raising=False)
    monkeypatch.delenv('ATTN_DIAG_TRACKING_BOUND',raising=False)
    assert executor.diagnostic_settle_steps()==20
    assert executor.diagnostic_tracking_bound()==0.004
    monkeypatch.setenv('ATTN_DIAG_SETTLE_STEPS','100')
    monkeypatch.setenv('ATTN_DIAG_TRACKING_BOUND','0.0125')
    assert executor.diagnostic_settle_steps()==100
    assert executor.diagnostic_tracking_bound()==0.0125


@pytest.mark.parametrize('raw',['','0','-1','1.5','abc',' 20','+20'])
def test_invalid_settle_rejected(monkeypatch,raw):
    monkeypatch.setenv('ATTN_DIAG_SETTLE_STEPS',raw)
    with pytest.raises(ValueError,match='ATTN_DIAG_SETTLE_STEPS'):
        executor.diagnostic_settle_steps()


@pytest.mark.parametrize('raw',['','0','-0.1','nan','inf','-inf','abc',' 0.004','1_0'])
def test_invalid_bound_rejected(monkeypatch,raw):
    monkeypatch.setenv('ATTN_DIAG_TRACKING_BOUND',raw)
    with pytest.raises(ValueError,match='ATTN_DIAG_TRACKING_BOUND'):
        executor.diagnostic_tracking_bound()


@pytest.mark.parametrize('travel',[0,1,79,80,81,100])
@pytest.mark.parametrize('settle',[20,60,100])
def test_official_time_limit_ignores_extra_settle(travel,settle):
    # Original condition: travel + the original 20 settle steps > 100.
    assert executor.official_timing_exceeds_ceiling(travel+settle,settle)==(travel+20>100)


def fake_apply(monkeypatch,*,position,settle=None,bound=None,travel_seconds=.02):
    if settle is None:monkeypatch.delenv('ATTN_DIAG_SETTLE_STEPS',raising=False)
    else:monkeypatch.setenv('ATTN_DIAG_SETTLE_STEPS',str(settle))
    if bound is None:monkeypatch.delenv('ATTN_DIAG_TRACKING_BOUND',raising=False)
    else:monkeypatch.setenv('ATTN_DIAG_TRACKING_BOUND',str(bound))
    calls=[];events=[];invalidations=[]
    target=np.array([.01,.02])
    def sampler(response,**kwargs):
        calls.append(kwargs)
        return np.array([[0.,0.],[.005,.01],target]),{'certificate':'unchanged'}
    monkeypatch.setattr(executor,'sample_for_original_drive',sampler)
    planner=NS(names=['joint1','joint2'],claim=lambda plan,epoch:{'plan_id':plan,'time_from_start_seconds':[travel_seconds]},
        state=lambda:np.array(position),spec={'robot_cfg':{'kinematics':{'cspace':{'max_acceleration':1,'max_jerk':1}}}},
        ledger=NS(invalidate=invalidations.append))
    obj=NS(check_live=lambda:None,planner=planner,env=NS(control_timestep=.01,
        robots=[NS(_ref_arm_joint_vel_indexes=[0,1])],sim=NS(data=NS(qvel=np.array([.007,-.003])))),
        epoch='fixed',sequence=0,physics_steps=0,goals=np.zeros(2),moving=False,active_request=None,
        max_speed={'joint1':1,'joint2':1},limits=np.array([[-1,1],[-1,1]]),journal='unused',failed=False)
    obj.log=lambda path,row:events.append(row)
    def control_step():obj.sequence+=1
    obj.control_step=control_step
    return obj,calls,events,invalidations


def test_default_apply_retains_original_budget_steps_and_receipt(monkeypatch):
    obj,calls,events,_=fake_apply(monkeypatch,position=[.011,.02])
    receipt=executor.NativeExecutor.apply(obj,{'plan_id':'p'},request_id='req')
    assert calls[0]['step_budget']==22
    assert calls[0]['settle_steps']==20
    assert obj.sequence==22  # two sampled drive steps and twenty settle steps
    assert [event['event'] for event in events]==['intent','receipt']
    assert receipt==dict(status='completed',request_id='req',epoch='fixed',start_sequence=0,
        sequence=22,physics_steps=0,before_qpos=[.011,.02],after_qpos=[.011,.02],
        maximum_endpoint_joint_error_rad=.0009999999999999992)


def test_failure_logs_joint_evidence_before_unchanged_exception(monkeypatch):
    obj,calls,events,invalidations=fake_apply(monkeypatch,position=[.02,-.001],settle=60)
    with pytest.raises(RuntimeError,match='^Native joint tracking exceeds 4mrad endpoint bound$'):
        executor.NativeExecutor.apply(obj,{'plan_id':'p'},request_id='req')
    assert calls[0]['step_budget']==62
    assert obj.sequence==62
    assert [event['event'] for event in events]==['intent','tracking_error']
    evidence=events[-1]
    assert evidence['request_id']=='req'
    assert evidence['settle_steps']==60 and evidence['bound']==.004
    assert evidence['error']==pytest.approx(.021)
    assert evidence['joint_errors']==pytest.approx({'joint1':.01,'joint2':-.021})
    assert evidence['max_error_joint']=='joint2'
    assert evidence['target']==[.01,.02]
    assert evidence['current_state']==[.02,-.001]
    assert evidence['qvel']==[.007,-.003]
    assert obj.failed and invalidations==['native_failure']


def test_failure_qvel_missing_is_null(monkeypatch):
    obj,_,events,_=fake_apply(monkeypatch,position=[.02,.02])
    obj.env.robots[0]=NS()
    with pytest.raises(RuntimeError):executor.NativeExecutor.apply(obj,{'plan_id':'p'},request_id='req')
    assert events[-1]['qvel'] is None


def test_overridden_bound_can_complete_with_original_receipt_shape(monkeypatch):
    obj,calls,events,_=fake_apply(monkeypatch,position=[.02,.02],settle=60,bound=.02)
    receipt=executor.NativeExecutor.apply(obj,{'plan_id':'p'},request_id='req')
    assert calls[0]['step_budget']==62 and obj.sequence==62
    assert receipt['maximum_endpoint_joint_error_rad']==.01
    assert [event['event'] for event in events]==['intent','receipt']
    assert set(receipt)=={'status','request_id','epoch','start_sequence','sequence',
                          'physics_steps','before_qpos','after_qpos','maximum_endpoint_joint_error_rad'}


def test_apply_ceiling_still_rejects_only_travel_over_80_steps(monkeypatch):
    obj,calls,_,_=fake_apply(monkeypatch,position=[.01,.02],settle=100,travel_seconds=.8)
    executor.NativeExecutor.apply(obj,{'plan_id':'p'},request_id='req')
    assert calls[0]['step_budget']==180
    assert obj.sequence==102  # sampler provides two drive samples, then 100 settle steps
    obj,calls,_,_=fake_apply(monkeypatch,position=[.01,.02],settle=100,travel_seconds=.81)
    with pytest.raises(PlanningRejected,match='Official timing exceeds 100-control-step action ceiling'):
        executor.NativeExecutor.apply(obj,{'plan_id':'p'},request_id='req')
    assert calls==[] and obj.sequence==0


def test_matrix_dry_run_and_only(capsys,tmp_path):
    out=tmp_path/'unused'
    assert matrix.main(['--dry-run','--out',str(out)])==0
    rows=[json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(rows)==14
    assert rows[0]['name']=='L+z-0.0001m-s20'
    assert rows[-1]['name']=='R+z-0.001m-s60'
    assert not out.exists()
    assert matrix.main(['--dry-run','--only',rows[0]['name'],'--out',str(out)])==0
    assert len(capsys.readouterr().out.splitlines())==1
    assert not out.exists()


def test_matrix_table_and_summary(tmp_path):
    trial=matrix.default_trials()[0]
    episode=tmp_path/'episode';episode.mkdir()
    feedback=tmp_path/'public-feedback.json'
    feedback.write_text(json.dumps({'action_trace':[{'operation':'move_delta','status':'failed',
        'error':{'message':'tracking failed'}}]}))
    (episode/'native-actions.jsonl').write_text(json.dumps({'event':'tracking_error','error':.021,
        'max_error_joint':'joint2'})+'\n')
    (episode/'native-physics.jsonl').write_text(json.dumps({'qvel':[.003,.004]})+'\n')
    row=matrix.summarize(trial,feedback,episode)
    assert row['result']=='failed' and row['move_error']=='tracking failed'
    assert row['maximum_error_mrad']==21 and row['max_error_joint']=='joint2'
    assert row['endpoint_qvel_norm']==pytest.approx(.005)
    table=matrix.markdown_table([row])
    assert '| left | 0.0001 | 20 | failed | 21.000 | joint2 | 0.005 |' in table


def test_matrix_success_uses_receipt_error(tmp_path):
    trial=matrix.default_trials()[-1]
    episode=tmp_path/'episode';episode.mkdir()
    feedback=tmp_path/'public-feedback.json'
    feedback.write_text(json.dumps({'action_trace':[{'operation':'move_delta','status':'completed'}]}))
    (episode/'native-actions.jsonl').write_text(json.dumps({'event':'receipt',
        'maximum_endpoint_joint_error_rad':.002})+'\n')
    row=matrix.summarize(trial,feedback,episode)
    assert row['result']=='completed' and row['maximum_error_mrad']==2
    assert row['max_error_joint'] is None and row['endpoint_qvel_norm'] is None
    assert '| right | 0.001 | 60 | completed | 2.000 | — | — |' in matrix.markdown_table([row])


def test_v2_owner_passes_diagnostic_environment_to_worker(monkeypatch,tmp_path):
    from openarm365_dev_v2.owner import NativeOwnerV2
    monkeypatch.setenv('ATTN_DIAG_SETTLE_STEPS','60')
    monkeypatch.setenv('ATTN_DIAG_TRACKING_BOUND','0.004')
    owner=NativeOwnerV2(python='python',config=tmp_path/'config',evidence=tmp_path,
                        deadline=100,cancel_check=lambda:False)
    process=MagicMock();process.pid=999999;process.args=[]
    with patch('openarm365_dev_v2.owner.subprocess.Popen',return_value=process) as spawn,\
            patch('openarm365_dev_v2.owner.selectors.DefaultSelector'),\
            patch.object(owner,'read',return_value={'kind':'ready'}):
        owner.start()
    worker_env=spawn.call_args.kwargs['env']
    assert worker_env['ATTN_DIAG_SETTLE_STEPS']=='60'
    assert worker_env['ATTN_DIAG_TRACKING_BOUND']=='0.004'
