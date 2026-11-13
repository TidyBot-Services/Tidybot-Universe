import copy
import json
from pathlib import Path
import pytest
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment
from benchmarks.attention_harness.summarize_experiment import summarize,markdown
from benchmarks.attention_harness.tests.cross_seed_fakes import CrossFakeRunner
from benchmarks.attention_harness.tests.autovalidate_fakes import synthetic_creation
from benchmarks.attention_harness.memory_cross_seed import provision_cross


@pytest.mark.parametrize('r3',[False,True])
def test_service_same_scene_pairs_cross_targets_preserve_profile(tmp_path,r3_deployment,r3):
    settings=r3_deployment.to_dict() if r3 else None
    fake=CrossFakeRunner(tmp_path/'fake',tmp_path/'state',execution_settings=settings)
    note=fake.promote_origin()
    assert fake.batch['context'].get('execution_profile')==('r3-20261025' if r3 else None)
    for pair in fake.batch['pairs']:
        for arm in ('control','treatment'):
            config=fake.f.records[pair[arm+'_cell']]['config']
            assert config.get('execution_settings')==settings
    target=fake.admit_seed(note,114,fake.paths['114'])
    with synthetic_creation(fake.f):
        batch=provision_cross(fake.provision,note,'profile-probe',target)
    config=json.loads(Path(batch['config']['uri']).read_bytes())
    assert config.get('execution_settings')==settings
    assert batch['context'].get('execution_profile')==('r3-20261025' if r3 else None)
    # Service C1 is the authority: the opposite profile cannot read this note.
    ctx=copy.deepcopy(fake.failed['context'])
    if r3:ctx.pop('execution_profile')
    else:ctx['execution_profile']='r3-20261025'
    from attention_memory_service.core.store import StateConflictError
    with pytest.raises(StateConflictError,match='execution profile mismatch'):
        fake.service.authorize_dev_use(memory_id=note['memory_id'],memory_version=1,
            development_id='opposite:v1',context=ctx,source_policy_sha256='c'*64,now=__import__('time').time())


def row(key,success,profile):
    return dict(key=key,task_id='pick',seed=102,policy_id='autonomous',state='completed',reason=None,
        execution_profile=profile,created_at=0,generated_versions=1,
        versions=[dict(version=1,native_success=success,passed=success,mode='vision',settled_at=2)],
        request_units=0,logical_human_seconds=0,timeouts=0,fallbacks=0,eval_compute={},read_errors=[])


def test_summary_marks_distinct_hosts_but_ignores_legacy():
    cells=[dict(row('a',True,'legacy'),host_fingerprint_sha256='a'*64),
           dict(row('b',False,'legacy'),host_fingerprint_sha256='b'*64),
           row('c',True,'legacy')]
    value=summarize(dict(experiment_id='hosts',manifest={},cells=cells))
    assert value['host_differs'] is True
    assert value['host_fingerprint_sha256'] == ['a'*64, 'b'*64]
    assert 'host differs' in markdown(value)
    legacy=summarize(dict(experiment_id='legacy',manifest={},cells=[cells[0], cells[2]]))
    assert 'host_differs' not in legacy


def test_summary_separates_all_rates_and_tables():
    value=summarize(dict(experiment_id='mixed',manifest={},cells=[row('a',False,'legacy'),row('b',True,'r3-20261025')]))
    assert 'by_policy' not in value and 'eval_quality' not in value
    assert len(value['profiles'])==2
    rates={g['execution_profile']:g['by_policy']['autonomous']['native_success_rate'] for g in value['profiles']}
    assert rates=={'legacy':0.,'r3-20261025':1.}
    assert all(g['by_policy']['autonomous']['evaluated_cells']==1 for g in value['profiles'])
    text=markdown(value)
    assert 'not comparable' in text and text.count('| Policy | Native success |')==2
    assert 'Execution profile: legacy' in text and 'Execution profile: r3-20261025' in text
    separate=summarize([dict(experiment_id='a',manifest={},cells=[row('a',False,'legacy')]),
                        dict(experiment_id='b',manifest={},cells=[row('b',True,'r3-20261025')])])
    assert len(separate['profiles'])==2


def test_old_grasp_generation_is_separate_even_with_legacy_execution():
    a=dict(experiment_id='a',manifest={'configs':{'pick:102':{}}},cells=[row('a',False,'legacy')])
    b=dict(experiment_id='b',manifest={'configs':{'pick:102':dict(planner_mode='ik_path',ik_candidates=8,ik_max_joint_jump=1.)}},cells=[row('b',True,'legacy')])
    value=summarize([a,b])
    assert {g['grasp_generation'] for g in value['profiles']}=={'0g','pre-0g'}
    frozen=copy.deepcopy(b)
    frozen['experiment_id']='frozen'
    frozen['cells']=[row('frozen',False,'legacy')]
    frozen['manifest']['configs']['pick:102']['grasp_freeze']={
        'id':'grasp-v2-20261113','sha256':'f'*64}
    value=summarize([a,b,frozen])
    assert {g['grasp_generation'] for g in value['profiles']}=={'0g','pre-0g','grasp-v2-20261113'}
    assert len(value['profiles'])==3
    rates={g['grasp_generation']:g['by_policy']['autonomous']['native_success_rate'] for g in value['profiles']}
    assert rates=={'pre-0g':0.,'0g':1.,'grasp-v2-20261113':0.}
    assert all(g['by_policy']['autonomous']['evaluated_cells']==1 for g in value['profiles'])
