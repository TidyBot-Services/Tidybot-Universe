import dataclasses
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
import numpy as np
import pytest
from curobo_v2 import execution_settings as es
from curobo_v2 import identity
from curobo_v2.attachment import SingleArmAttachment, effective_held_envelope, attachment_inflation
from test_curobo_v2_attachment import measured
from test_ik_path import fake_core


@pytest.fixture
def block(monkeypatch):
    monkeypatch.setattr(es, '_deployment_pin', lambda: dict(lock_path=es.D06_LOCK, sha256='a'*64))
    return es.R3_20261025.to_dict()


def test_exact_profile_and_canonical(block):
    assert es.from_config(None) is es.LEGACY
    value = es.from_config(block)
    assert value.to_dict() == block
    assert value.sha256 == hashlib.sha256(json.dumps(block, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert es.request_fields() == {} and es.effective_sha() == 'legacy'
    with pytest.raises(dataclasses.FrozenInstanceError): value.profile = 'bad'
    with pytest.raises(TypeError): value.planner_deployment['sha256'] = 'b'
    block['planner_deployment']['sha256'] = 'b'
    assert value.planner_deployment['sha256'] == 'a'*64


@pytest.mark.parametrize('field', [f.name for f in dataclasses.fields(es.ExecutionSettings)])
def test_partial_and_wrong_values_refused(block, field):
    bad = dict(block); bad.pop(field)
    with pytest.raises(ValueError, match=field): es.from_config(bad)
    bad = dict(block); bad[field] = 'incorrect'
    with pytest.raises(ValueError, match=field): es.from_config(bad)


@pytest.mark.parametrize('field', ['lock_path', 'sha256'])
def test_wrong_deployment_refused(block, field):
    block['planner_deployment'][field] = 'bad'
    with pytest.raises(ValueError, match='planner_deployment.'+field): es.from_config(block)


def test_extra_key_and_bool_as_float_refused(block):
    with pytest.raises(ValueError, match='extra'): es.from_config(dict(block, extra=True))
    with pytest.raises(ValueError, match='attach_after_grasp'): es.from_config(dict(block, attach_after_grasp=1))
    with pytest.raises(ValueError, match='held_envelope_m'): es.from_config(dict(block, held_envelope_m=float('nan')))


def test_environment_cannot_change_values(monkeypatch, block):
    for name in ('ATTN_DIAG_HELD_ENVELOPE_M', 'ATTN_DIAG_JOINT_MARGIN_RAD', 'ATTN_DIAG_SUPPORT_DEPARTURE', 'ATTN_DIAG_PLANNER_LOCK'):
        monkeypatch.setenv(name, 'nonsense')
    assert es.from_config(block).to_dict() == block
    assert effective_held_envelope() == (.004, .03)


def test_attachment_envelope_inflation_consistent(block):
    ctx, world = measured()
    old = SingleArmAttachment(ctx, world, [ctx['tool_frame']])
    new = SingleArmAttachment(ctx, world, [ctx['tool_frame']], execution_settings=es.from_config(block))
    n = new.sphere_count
    base = old.spheres[:n].copy()
    # Compare both to their uninflated conservative cover.
    from curobo_v2.attachment import cover_cuboid
    base = np.concatenate([cover_cuboid(c, old.transform @ np.linalg.inv(old.initial_pose)) for c in old.cuboids])
    assert np.array_equal(new.spheres[:n, :3], old.spheres[:n, :3])
    assert np.allclose(new.spheres[:n, 3], base[:, 3] + attachment_inflation(base, (.010, .10)))
    assert new.artifact()['grasp_translation_envelope_m'] == .010
    assert new.artifact()['grasp_rotation_envelope_rad'] == .10


def test_seventh_joint_margin_and_incomplete_arm():
    names = [f'openarm_left_joint{i}' for i in range(1, 8)]
    q = np.zeros(7); q[-1] = .99
    result = es.goal_margin(q, names, [[-1, 1]]*7, execution_settings=es.R3_20261025)
    assert result['minimum_joint_margin_rad'] == pytest.approx(.01)
    with pytest.raises(ValueError, match='seven'): es.minimum_arm_margin(q[:6], names[:6], [[-1, 1]]*6)


def test_ik_path_selects_next_margin_candidate(monkeypatch):
    cls, core, q, targets, recorded = fake_core(monkeypatch, [.01, .1])
    core.execution_settings = es.R3_20261025
    core.limits[0] = [0., 1.]  # The nearest candidate has only 0.01 rad margin.
    result = cls.plan_ik_path(core, q, targets=targets, active_joints=core.joint_names[:7], ik_candidates=2)
    assert result[-1, 0] == .1
    assert core.last_ik_path_metrics['candidate_reasons'][0] == 'ik_joint_margin_below_execution_limit'


def test_public_ik_selects_margin_candidate(monkeypatch):
    cls, core, q, targets, recorded = fake_core(monkeypatch, [.01, .1])
    core.execution_settings = es.R3_20261025
    core.tool_frames = list(targets)
    planner = next(iter(core._models.values()))[0]; planner.tool_frames = list(targets)
    core.limits[0] = [0., 1.]
    result = cls.solve_ik(core, current_q=q, targets=targets, active_joints=core.joint_names[:7])
    assert result[0] == .1 and recorded['return_seeds'] == 8


def test_registry_location():
    assert identity.DEPLOYMENT_REGISTRY == Path(identity.__file__).resolve().with_name('trusted_deployments.json')


@pytest.mark.parametrize('tier,boundary,accepted', [('dev','dev_loop',True), ('dev','dev',True), ('formal','formal',False), ('dev','formal',False), ('dev','formal_eval',False), ('dev','other',False), (None,None,False)])
def test_tier_restriction(tmp_path, monkeypatch, tier, boundary, accepted):
    lock = dict(project_root='/project', files={}, official='unchanged')
    parent = tmp_path/'parent.json'; parent.write_text(json.dumps(lock))
    parent_sha = identity.digest(parent)
    registry = tmp_path/'registry.json'
    entry = dict(lock_path=str(tmp_path/'lock.json'), official_commit=identity.OFFICIAL_COMMIT,
                 parent_sha256=parent_sha, allowed_tiers=['dev'])
    registry.write_text(json.dumps({'lock':entry}))
    monkeypatch.setattr(identity, 'OFFICIAL_PARENT_LOCK', parent)
    monkeypatch.setattr(identity, 'TRUSTED_LOCKS', {parent_sha:identity.OFFICIAL_COMMIT})
    monkeypatch.setattr(identity, 'DEPLOYMENT_REGISTRY', registry)
    monkeypatch.setattr(identity, '_registry_digest', None)
    assert identity.approved_deployment(lock, 'lock', entry['lock_path'], tier=tier, boundary=boundary) is accepted
    # Unrestricted entries retain historical behavior, even without caller metadata.
    entry.pop('allowed_tiers'); registry.write_text(json.dumps({'lock':entry}))
    monkeypatch.setattr(identity, '_registry_digest', None)
    assert identity.approved_deployment(lock, 'lock', entry['lock_path'])
    registry.write_text('{}')
    with pytest.raises(identity.IdentityError, match='changed'): identity.approved_deployment(lock, 'lock', entry['lock_path'])


def test_profile_binding_requires_registered_d06(tmp_path,monkeypatch):
    registry=tmp_path/'registry.json';registry.write_text('{}')
    monkeypatch.setattr(identity,'DEPLOYMENT_REGISTRY',registry)
    monkeypatch.setattr(identity,'_registry_digest',None)
    monkeypatch.setattr(identity,'_service_context',None)
    with pytest.raises(ValueError,match='planner_deployment'):es.R3_20261025.to_dict()
    entry=dict(lock_path=es.D06_LOCK,profile=es.PROFILE,allowed_tiers=['dev'],derived_from_sha256=es.D05_SHA)
    registry.write_text(json.dumps({'a'*64:entry}))
    calls=[]
    monkeypatch.setattr(identity,'load_lock',lambda path,**kw:calls.append((path,kw)) or ({},'a'*64))
    assert es.R3_20261025.planner_deployment['sha256']=='a'*64
    assert calls[-1]==(es.D06_LOCK,dict(tier='dev',boundary='dev_loop'))


def test_authenticated_service_binding(tmp_path,monkeypatch):
    lock_path=tmp_path/'deployment-06/runtime-lock.json';lock_path.parent.mkdir()
    lock=dict(project_root=str(lock_path.parent/'runtime/project'),provenance=dict(adapter_revision='deployment-06',parent_adapter_lock_sha256=es.D05_SHA))
    lock_path.write_text(json.dumps(lock))
    monkeypatch.setattr(es,'D06_LOCK',str(lock_path))
    monkeypatch.setattr(identity,'_service_context',(lock,'/robot.yml'))
    pin=es._deployment_pin()
    assert pin==dict(lock_path=str(lock_path),sha256=identity.digest(lock_path))
    lock_path.write_text('{}')
    with pytest.raises(ValueError,match='service lock changed'):es._deployment_pin()
