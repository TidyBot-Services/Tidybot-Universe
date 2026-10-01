"""Binding violations must be rejected before any launcher/Service execution."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('identity_review',Path(__file__).with_name('review_identity_package.py'))
reviewer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(reviewer)


def arguments(suite='robocasa',condition='autonomous'):
    task='counter_to_sink' if suite=='robocasa' else 'cube_lift'
    return reviewer.read(reviewer.PACKAGE/f'entries/{suite}_{task}_seed101/{condition}/launch_arguments.json')['fixed_arguments']


def replace(args,flag,value):
    args=list(args);args[args.index(flag)+1]=str(value);return args


@pytest.mark.parametrize('suite',['robosuite','robocasa'])
def test_current_binding_accepts_exact_approved_bytes(suite):
    lock,args=reviewer.inspect_launch(arguments(suite))
    assert lock['approved_policy_sha256']==reviewer.sha(args.code)
    assert not args.artifact_root.exists()


def test_old_expected_entry_sha_rejected_for_new_policy():
    old=reviewer.read(reviewer.OLD/'entries/robocasa_counter_to_sink_seed101/autonomous/m1_entry_lock.json')
    with pytest.raises(ValueError,match='launch expected entry SHA mismatch'):
        reviewer.inspect_launch(replace(arguments(),'--expected-entry-sha256',old['sha256']))


def test_old_policy_sha_rejected_for_new_source():
    with pytest.raises(ValueError,match='policy differs from approved SHA-256'):
        reviewer.inspect_launch(replace(arguments(),'--approved-policy-sha256','e22f944fddcae410095d91cf6ef61a1626db4bcdb88479b9ac19281895f11780'))


def test_modified_policy_cannot_use_existing_approval(tmp_path):
    args=arguments();code=Path(args[args.index('--code')+1]);changed=tmp_path/'altered.py'
    changed.write_text(code.read_text()+'\nbase.move_delta(dx=.01,dy=0,dtheta=0,frame="local")\n')
    with pytest.raises(ValueError,match='policy differs from approved SHA-256'):
        reviewer.inspect_launch(replace(args,'--code',changed))


def test_wrong_config_sha_is_rejected():
    with pytest.raises(ValueError,match='config differs from approved SHA-256'):
        reviewer.inspect_launch(replace(arguments(),'--approved-config-sha256','0'*64))


def test_changed_condition_cannot_reuse_entry_identity():
    with pytest.raises(ValueError,match='launch expected entry SHA mismatch'):
        reviewer.inspect_launch(replace(arguments(),'--attention-policy','reactive_help'))


def test_heldout_seed_cannot_enter_dev_binding():
    with pytest.raises(ValueError):
        reviewer.inspect_launch(replace(arguments(),'--seed','1001'))


def test_missing_single_call_guard_is_rejected():
    args=arguments();args.remove('--single-glm-call')
    with pytest.raises(ValueError,match='retain single-call'):
        reviewer.inspect_launch(args)


def test_unapproved_memory_contract_is_rejected(tmp_path):
    args=arguments(condition='full_trace_aware_attention_planner')
    original=Path(args[args.index('--memory-contract')+1]);data=json.loads(original.read_text())
    data['automatic_promotion']=True;changed=tmp_path/'contract.json';changed.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='Memory contract differs from approved SHA-256'):
        reviewer.inspect_launch(replace(args,'--memory-contract',changed))


def test_generation_source_location_must_match(tmp_path):
    args=arguments();code=Path(args[args.index('--code')+1]);copy=tmp_path/'same_bytes.py';copy.write_bytes(code.read_bytes())
    with pytest.raises(ValueError,match='generation receipt does not match approved source'):
        reviewer.inspect_launch(replace(args,'--code',copy))
