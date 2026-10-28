"""User decision 2026-10-29: hypothetical probe base_offset up to 0.6 m / 0.9 rad.

Real base motion keeps 0.20 m / 0.3 rad per call; every probe step runs the
unchanged exact execution clearance check.
"""
import pytest
from openarm365_dev.base_motion import validate_delta, PlanningRejected
from openarm365_dev_v2.agent_probe import probe, probe_offset, state_fingerprint, validate
from benchmarks.attention_harness.tests.test_dev_agent_d1 import TARGET, broker, request, settled
from benchmarks.attention_harness.tests.test_dev_agent_d1 import workspace  # noqa: F401
from benchmarks.attention_harness.tests.test_dev_agent_d5 import owner


def test_probe_offset_bounds_and_steps():
    assert probe_offset(0,.25,0)==(2,[0,.125,0])
    assert probe_offset(.6,0,0)[0]==3 and probe_offset(0,0,.9)[0]==3
    assert probe_offset(0,.2,.3)[0]==1
    for bad in ((0,.61,0),(0,0,.91),(.5,.5,0)):
        with pytest.raises(ValueError,match='probe limit 0.6 m / 0.9 rad'):probe_offset(*bad)


def test_execution_and_base_action_stay_per_call_limited():
    with pytest.raises(PlanningRejected,match='0.20m'):validate_delta(0,.25,0)
    with pytest.raises(ValueError,match='per-call base limit 0.20 m / 0.3 rad'):
        validate('plan_dry_run',dict(action=dict(group='base',operation='move_delta',args=[0,.25,0],kwargs={})))


def test_broker_accepts_025_and_rejects_061(workspace):  # noqa: F811
    b=broker(workspace)
    try:
        b.accept(request('a',op='ik_check',payload=dict(target=TARGET,base_offset=dict(dx=0,dy=.25,dyaw=0))))
        assert settled(b,'a')['status']=='completed'
        value=b.accept(request('b',op='ik_check',payload=dict(target=TARGET,base_offset=dict(dx=0,dy=.61,dyaw=0))))
        assert value['status']=='rejected' and 'probe limit 0.6 m / 0.9 rad' in value['validation_error']
        assert b.budget.counts['probes']==1
    finally:b.close()


@pytest.mark.parametrize('clearances,accepted,worst',[((.03,.02,.025),True,2),((.03,.0093,.02),False,2)])
def test_large_offset_checks_each_step_exactly_and_restores(monkeypatch,clearances,accepted,worst):
    env,ex,planner,seen,checks,forwards=owner(monkeypatch)
    import openarm365_dev.base_motion as bm
    real=bm.check_exact_base_path;values=iter(clearances)
    def exact(env_,world,binding,delta,initial,*,execution_settings):
        import benchmarks.attention_harness.tests.test_dev_agent_d5 as d5
        monkeypatch.setattr(d5,'_clearance',next(values),raising=False)
        return stepped(env_,world,binding,delta,initial,execution_settings=execution_settings)
    def stepped(env_,world,binding,delta,initial,*,execution_settings):
        import benchmarks.attention_harness.tests.test_dev_agent_d5 as d5
        from openarm365_dev.base_motion import BasePathRejected
        c=d5._clearance;checks.append((delta.copy(),execution_settings,env.sim.data.qpos.copy()))
        audit=dict(status='clear' if c>=.01 else 'rejected',minimum_clearance_m=c,
                   minimum_clearance_pair=dict(obstacle='counter',robot_geom='chassis',clearance_m=c),
                   required_clearance_m=execution_settings.base_path_clearance_m,
                   absolute_min_distance_m=execution_settings.base_start_clearance_m)
        if c<.01:raise BasePathRejected('too close',audit)
        return audit
    monkeypatch.setattr(bm,'check_exact_base_path',exact)
    before=state_fingerprint(env,ex)
    result=probe(env,ex,planner,lambda:{},request(op='ik_check',payload=dict(target=TARGET,base_offset=dict(dx=0,dy=.45,dyaw=0))))
    clearance=result['base_clearance']
    assert clearance['steps']==3 and clearance['step_delta']['dy']==pytest.approx(.15)
    assert clearance['accepted'] is accepted and clearance['worst_step']==worst
    assert clearance['checked_steps']==(3 if accepted else 2)
    assert clearance['minimum_clearance_m']==(.02 if accepted else .0093)
    # Every checked step is a per-call-sized delta through the same settings.
    assert all(abs(c[0][1]-.15)<1e-9 and c[1] is ex.execution_settings for c in checks)
    # The arm is evaluated at the full hypothetical offset, then everything is restored.
    assert seen[0][15]==pytest.approx(.45)
    assert state_fingerprint(env,ex)==before and ex.physics_steps==0
    assert result['planning_accepted'] is True
    del real


def test_ask_help_unavailable_reason_names_the_eval_quote_rule():
    from pathlib import Path
    text=Path(__file__).resolve().parents[1].joinpath('dev_agent_attention.py').read_text()
    assert 'questions must not quote Eval or evaluation text' in text
