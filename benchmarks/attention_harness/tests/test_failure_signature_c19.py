"""Actual client/C5 public formats; no client initialization or external access."""
import json
from types import SimpleNamespace

import pytest

from curobo_v2.client import V2Client, PlanningRejected
from openarm365_dev.planning_rejection import call_with_rejection_facts
from benchmarks.attention_harness.memory_provisional import derive_signature, signature_present

MARGIN='ik_joint_margin_below_execution_limit'
COLLISION='start_state_collision_or_constraint_violation'


@pytest.mark.parametrize('response,suffix', [
    (dict(status='rejected',reason='official_ik_non_success'),None),
    (dict(status='official_motion_planner_non_success'),None),
    (dict(status='rejected',reason=MARGIN,joint_margin_limit_rad=.05,
          candidate_joint_margins=[dict(minimum_joint_margin_rad=.01,joint_margins_rad={'openarm_left_joint7':.01})]),'best evaluated IK candidate'),
    (dict(status='rejected',reason=COLLISION,validation=dict(joint_limits=False)), 'checks failing at the start state'),
])
@pytest.mark.parametrize('wrapper',['PlanningRejected: ','RuntimeError: PlanningRejected: '])
def test_executor_emitted_formats(tmp_path,response,suffix,wrapper):
    client=object.__new__(V2Client)
    client.nonce='fake';client.lock_sha='0'*64;client.evidence=tmp_path
    client.worlds={'default':('fake','fake')}
    client._check_live=lambda:None;client.attest=lambda:None
    client._request=lambda *args:response
    with pytest.raises(PlanningRejected) as caught:
        call_with_rejection_facts(client,'/plan/ik',{},SimpleNamespace(profile='r3-20261025'))
    error=str(caught.value)
    if suffix:assert '; '+suffix in error
    else:assert ';' not in error
    feedback=dict(public_error=wrapper+error,action_trace=[dict(source='left_arm',operation='move_delta',status='failed')])
    signature=derive_signature(feedback)
    assert signature==dict(public_error_class=wrapper[:-2],operation='left_arm.move_delta',reason_code=response.get('reason',response['status']))
    assert signature_present(feedback,signature)


def test_rerun_mailbox_is_immutable_and_preserves_origin_and_guidance(tmp_path):
    from benchmarks.attention_harness.tests.positive_control_fakes import PositiveFake,atomic
    from benchmarks.attention_harness.memory_positive_control import main,queue_rerun,local_origin
    from benchmarks.attention_harness.core.dev_store import DevConflict
    from unittest.mock import patch
    fake=PositiveFake(tmp_path/'fake',tmp_path/'worker')
    root=fake.provision.state_root;source=root/'positive-controls/run8.json'
    before=source.read_bytes()
    assert main(['--state-dir',str(root),'--rerun-spec',str(source),'--control-id','run8-heading-lift-r3-b'])==0
    queued=json.loads((root/'positive-control-queue/run8-heading-lift-r3-b.json').read_bytes())['spec']
    assert queued==dict(fake.spec,control_id='run8-heading-lift-r3-b')
    assert source.read_bytes()==before
    with patch('benchmarks.attention_harness.dev_planner_memory.context',lambda *a,**kw:fake.failed['context']):
        assert local_origin(fake.provision,queued)['key']!=local_origin(fake.provision,fake.spec)['key']
    queue_rerun(root,source,queued['control_id'])  # Identical retry is harmless.
    queue_rerun(root,root/'positive-control-queue/pc-run8.json',queued['control_id'])
    with pytest.raises(DevConflict):queue_rerun(root,source,fake.spec['control_id'])
    changed=dict(fake.spec,guidance='different guidance');atomic(root/'changed.json',changed)
    with pytest.raises(DevConflict):queue_rerun(root,root/'changed.json',queued['control_id'])


def test_positive_control_c19_fake_e2e(tmp_path):
    from benchmarks.attention_harness.tests.signature_c19_fakes import lifecycle
    lifecycle(tmp_path)


def test_legacy_planner_note_origin_preparation_keeps_frozen_signature(tmp_path):
    from benchmarks.attention_harness.tests.signature_c19_fakes import planner_errors,SIGNATURE
    from benchmarks.attention_harness.tests.autovalidate_fakes import AutomaticOriginFakeRunner,synthetic_creation
    from benchmarks.attention_harness.tests.test_memory_origin_evidence import run
    legacy={k:v for k,v in SIGNATURE.items() if k!='reason_code'}
    with planner_errors(legacy):
        fake=AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker')
        note=fake.service.validation_queue()[0]
        run(fake)
        with synthetic_creation(fake.f):resolved=fake.provision.resolve(note)
    assert resolved['signature']==legacy
    assert fake.service.provenance(fake.mid)['failure_signature']==legacy


def test_provisional_staging_derives_reason_and_freezes_proposal(tmp_path,monkeypatch):
    from pathlib import Path
    from benchmarks.attention_harness import memory_provisional,dev_planner_memory
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    from benchmarks.attention_harness.tests.test_memory_provisional import write_version,ADVICE,REJECTED
    store=DevV2Store(tmp_path/'ledger.sqlite3')
    monkeypatch.setattr(store,'cell',lambda key:dict(experiment_id='fake',policy_id='fake',jobs=[]))
    monkeypatch.setattr(dev_planner_memory,'memory_scope',lambda *a:dict(level='scene'))
    monkeypatch.setattr(dev_planner_memory,'context',lambda *a:dict(namespace='fake',task_id='fake'))
    (tmp_path/'config.json').write_text('{}')
    write_version(tmp_path,1,public=REJECTED)
    helped=dict(REJECTED,public_error='RuntimeError: PlanningRejected: cuRoboV2 planning rejected: official_ik_non_success')
    directory=write_version(tmp_path,2,public=helped,help_value=ADVICE)
    ref=memory_provisional.stage(store,'fake',2,directory)
    before=Path(ref['uri']).read_bytes();proposal=json.loads(before)
    assert proposal['failure_signature']['reason_code']=='official_motion_planner_non_success'
    assert proposal['effective_reason']=='signature_changed'
    assert memory_provisional.stage(store,'fake',2,directory)==ref
    assert Path(ref['uri']).read_bytes()==before


def test_second_control_replays_same_origin_under_new_seals(tmp_path):
    from pathlib import Path
    from unittest.mock import patch
    from benchmarks.attention_harness.tests.signature_c19_fakes import lifecycle,planner_errors,SIGNATURE
    from benchmarks.attention_harness.tests.autovalidate_fakes import synthetic_creation,atomic
    from benchmarks.attention_harness.memory_positive_control import queue_rerun,ControlClient
    from benchmarks.attention_harness.memory_validation_worker import tick
    fake,state,record=lifecycle(tmp_path)
    root=fake.provision.state_root;old_id=fake.spec['control_id']
    pc=fake.authority.positive_controls;before=pc.get(old_id);decision=pc.get_decision(old_id)
    receipts={p:p.read_bytes() for p in (root/'origins').glob('*/receipt.json')}
    queue_rerun(root,root/'positive-control-queue'/f'{old_id}.json','pc-run8-b')
    fake.spec=json.loads((root/'positive-controls/pc-run8-b.json').read_bytes())
    fake.proxy=ControlClient(fake.remote,fake.spec,fake.provision)
    fake.provision.client=fake.proxy;fake.origin_runner.client=fake.proxy
    fake.service.authorize_dev_v2_validation=fake.proxy.authorize_dev_v2_validation
    fake.service.import_dev_v2_trial=fake.proxy.import_dev_v2_trial
    fake.meter_results*=2
    atomic(root/'autovalidate.json',dict(enabled=True,daily_run_limit=None,cross_seed=dict(enabled=False)))
    deps=fake.deps(root)
    with planner_errors(),patch('benchmarks.attention_harness.dev_planner_memory.context',lambda *a,**kw:fake.failed['context']):
        for _ in range(50):
            with synthetic_creation(fake.f):tick(state,deps)
            fake.phase='completed'
            result=state.get('positive_controls',{}).get('pc-run8-b',{})
            if result.get('same_done'):break
    assert result.get('verdict'),state
    assert result['verdict']['decision']=='would_pass',state
    assert pc.get('pc-run8-b')['failure_signature']==SIGNATURE
    assert pc.get('pc-run8-b')['spec']==dict(before['spec'],control_id='pc-run8-b')
    assert pc.get(old_id)==before and pc.get_decision(old_id)==decision
    assert fake.replays==4 and len(list((root/'origins').glob('*/receipt.json')))==2
    assert all(p.read_bytes()==value for p,value in receipts.items())


@pytest.mark.parametrize('script',['autovalidate_fake_e2e.py','cross_seed_fake_e2e.py',
    'motion_c12_fake_e2e.py','positive_control_fake_e2e.py','signature_c19_fake_e2e.py'])
def test_all_fake_scripts(script):
    import runpy
    from pathlib import Path
    runpy.run_path(str(Path(__file__).resolve().parents[3]/'scripts'/script),run_name='__main__')
