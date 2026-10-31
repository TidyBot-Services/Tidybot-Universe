"""Immutable execution profiles. No environment overrides.

R3_20261025 is immutable; its deployment binding is resolved from the pinned
trust registry (or the already authenticated service lock). This avoids putting
a deployment's own hash in its hashed source. Serialization fails until d06 is
built and registered. from_config returns a fully materialized, frozen value.
"""
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
import hashlib
import json
from pathlib import Path
from types import MappingProxyType

LEGACY = None
PROFILE = 'r3-20261025'
D06_LOCK = '/home/truares/Documents/Codex/2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-06/runtime-lock.json'
D05_SHA = '0058f2d4d3d4a7d7894f53dcb99128c0db377cb216ab3cf747cb038f12b4e1a0'


def _deployment_pin():
    from . import identity
    if identity._service_context is not None:
        lock, _ = identity.require_service_context()
        path = Path(lock['project_root']).parent.parent / 'runtime-lock.json'
        from .dev_local import is_dev_local
        if is_dev_local(lock):
            actual, lock_sha = identity.load_lock(path, tier='dev', boundary='dev_loop')
            if actual != lock:raise ValueError('planner_deployment: service lock changed')
            return dict(lock_path=str(path), sha256=lock_sha)
        if (str(path) != D06_LOCK or lock.get('provenance', {}).get('adapter_revision') != 'deployment-06'
                or lock['provenance'].get('parent_adapter_lock_sha256') != D05_SHA):
            raise ValueError('planner_deployment: service is not deployment-06')
        raw = path.read_bytes()
        if json.loads(raw) != lock:
            raise ValueError('planner_deployment: service lock changed')
        return dict(lock_path=str(path), sha256=hashlib.sha256(raw).hexdigest())
    try:
        raw = identity.DEPLOYMENT_REGISTRY.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if identity._registry_digest is not None and digest != identity._registry_digest:
            raise identity.IdentityError('Deployment trust policy changed during this process')
        matches = [(sha, entry) for sha, entry in json.loads(raw).items()
                   if entry.get('profile') == PROFILE and entry.get('allowed_tiers') == ['dev']]
        if len(matches) != 1:
            raise ValueError('planner_deployment: exactly one registered deployment-06 required')
        sha, entry = matches[0]
        if entry.get('lock_path') != D06_LOCK or entry.get('derived_from_sha256') != D05_SHA:
            raise ValueError('planner_deployment: only deployment-06 derived from deployment-05 is accepted')
        _, actual = identity.load_lock(D06_LOCK, tier='dev', boundary='dev_loop')
        if actual != sha:
            raise ValueError('planner_deployment.sha256: registered lock mismatch')
        return dict(lock_path=D06_LOCK, sha256=sha)
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError('planner_deployment: build and register deployment-06 first') from exc


@dataclass(frozen=True)
class _DeploymentBinding(Mapping):
    def __iter__(self):
        return iter(('lock_path', 'sha256'))

    def __len__(self):
        return 2

    def __getitem__(self, key):
        return _deployment_pin()[key]


@dataclass(frozen=True)
class ExecutionSettings:
    profile: str
    base_start_clearance_m: float
    base_path_clearance_m: float
    base_hold_drift_m: float
    base_hold_drift_rad: float
    goal_joint_margin_rad: float
    held_envelope_m: float
    held_envelope_rad: float
    attach_after_grasp: bool
    support_departure: str
    planner_deployment: Mapping

    def __post_init__(self):
        if not isinstance(self.planner_deployment, _DeploymentBinding):
            object.__setattr__(self, 'planner_deployment', MappingProxyType(dict(self.planner_deployment)))

    def to_dict(self):
        return {f.name: dict(self.planner_deployment) if f.name == 'planner_deployment'
                else getattr(self, f.name) for f in fields(self)}

    def canonical_json(self):
        return json.dumps(self.to_dict(), sort_keys=True, separators=(',', ':'), allow_nan=False)

    @property
    def sha256(self):
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


R3_20261025 = ExecutionSettings(PROFILE, .002, .010, .003, .003, .02, .010, .10,
                               True, 'monotone', _DeploymentBinding())


@dataclass(frozen=True)
class R4ExecutionSettings(ExecutionSettings):
    carried_proxy_support_departure: str = 'monotone'
    grasp_heading_search: tuple = (.1, -.1, .2, -.2, .3, -.3, .5, -.5)

    def to_dict(self):
        value = super().to_dict()
        value['grasp_heading_search'] = list(self.grasp_heading_search)
        return value


@dataclass(frozen=True)
class _DevLocalBinding(_DeploymentBinding):
    def __getitem__(self, key):
        pin = _deployment_pin()
        _require_dev_local(pin)
        return pin[key]


def _require_dev_local(pin):
    from .identity import load_lock
    from .dev_local import is_dev_local
    lock, actual = load_lock(pin['lock_path'], tier='dev', boundary='dev_loop')
    if not is_dev_local(lock) or actual != pin['sha256']:
        raise ValueError('planner_deployment: r4 requires an authenticated dev-local snapshot')
    from . import identity
    if identity._service_context is not None:
        service, _ = identity.require_service_context()
        if service != lock:
            raise ValueError('planner_deployment: r4 differs from authenticated service snapshot')


R4_20261027 = R4ExecutionSettings('r4-20261027', .002, .010, .003, .003, .02, .010, .10,
                                True, 'monotone', _DevLocalBinding())


@dataclass(frozen=True)
class FixtureExecutionSettings(ExecutionSettings):
    fixture_whitelist_id: str = 'robocasa-interaction-parts-v1'
    fixture_whitelist_sha256: str = ''
    fixture_scope: str = 'task_references'
    fixture_contact_mode: str = 'record_only'
    fixture_attachment: str = 'no_attachment'
    fixture_contact_base_rehold_max_translation_m: float = .05
    fixture_contact_base_rehold_max_yaw_rad: float = .10
    fixture_neighbourhood_window_m: float = .01
    fixture_press_window_m: float = .035
    fixture_contact_radius_m: float = .01
    fixture_press_contact_radius_m: float = .035


from .fixture_interaction import PROFILE as FIXTURE_PROFILE, WHITELIST_ID, WHITELIST_SHA256
R5_FIXTURE_20261031 = FixtureExecutionSettings(FIXTURE_PROFILE, .002, .010, .003, .003, .02, .010, .10,
    True, 'monotone', _DevLocalBinding(), WHITELIST_ID, WHITELIST_SHA256)


def from_config(block):
    if block is None:
        return LEGACY
    if not isinstance(block, Mapping):
        raise ValueError('execution_settings: expected a complete mapping')
    profile = (R5_FIXTURE_20261031 if block.get('profile') == FIXTURE_PROFILE else
               R4_20261027 if block.get('profile') == R4_20261027.profile else R3_20261025)
    names = {f.name for f in fields(profile)}
    for name in sorted(names - block.keys()):
        raise ValueError(f'{name}: missing field')
    for name in sorted(block.keys() - names):
        raise ValueError(f'{name}: extra field')
    for name in sorted(names - {'planner_deployment'}):
        expected = getattr(profile, name)
        if name == 'grasp_heading_search': expected = list(expected)
        if type(block[name]) is not type(expected) or block[name] != expected:
            raise ValueError(f'{name}: only {expected!r} is accepted')
    pin = block['planner_deployment']
    if not isinstance(pin, Mapping) or set(pin) != {'lock_path', 'sha256'}:
        raise ValueError('planner_deployment: exact lock_path and sha256 required')
    if profile is R4_20261027 or profile is R5_FIXTURE_20261031:
        for name in ('lock_path', 'sha256'):
            if type(pin[name]) is not str:
                raise ValueError(f'planner_deployment.{name}: expected string')
        _require_dev_local(pin)
        return replace(profile, planner_deployment=pin)
    expected = _deployment_pin()
    if pin['lock_path'] != expected['lock_path']:
        from . import identity
        from .dev_local import is_dev_local
        try:
            if type(pin['lock_path']) is not str:raise ValueError('planner_deployment.lock_path: expected string')
            lock, actual = identity.load_lock(pin['lock_path'], tier='dev', boundary='dev_loop')
            if not is_dev_local(lock):raise ValueError('planner_deployment.lock_path: not a dev-local snapshot')
            if type(pin['sha256']) is not str or pin['sha256'] != actual:
                raise ValueError('planner_deployment.sha256: dev-local lock mismatch')
        except (identity.IdentityError, OSError, TypeError) as exc:
            name = 'lock_path' if pin['lock_path'] != expected['lock_path'] else 'sha256'
            raise ValueError(f'planner_deployment.{name}: not an approved deployment') from exc
    else:
        for name in expected:
            if type(pin[name]) is not str or pin[name] != expected[name]:
                raise ValueError(f'planner_deployment.{name}: not the registered deployment-06')
    return replace(R3_20261025, planner_deployment=pin)


def effective_sha(settings=LEGACY):
    """Legacy is implicit on the wire to preserve historical response bytes."""
    return 'legacy' if settings is LEGACY else settings.sha256


def request_fields(settings=LEGACY):
    return {} if settings is LEGACY else {'execution_settings': settings.to_dict()}


def departure_floor(start_separation=None):
    return min(0., start_separation or 0.) - .0001


def minimum_arm_margin(q, names, limits, active=None):
    """Shared solve-stage and C3 boundary check: all seven joints of each active arm."""
    import numpy as np
    q, limits = np.asarray(q, float), np.asarray(limits, float)
    if q.shape != (len(names),) or limits.shape != (len(names), 2) or not np.isfinite(q).all() or not np.isfinite(limits).all():
        raise ValueError('goal_joint_margin_rad: invalid named joint state/limits')
    selected = set(names if active is None else active)
    sides = [s for s in ('left', 'right') if any(f'openarm_{s}_joint{i}' in selected for i in range(1, 8))]
    joints = [f'openarm_{s}_joint{i}' for s in sides for i in range(1, 8)]
    if not joints or any(n not in names for n in joints):
        raise ValueError('goal_joint_margin_rad: all seven named arm joints required')
    margins = {n: float(min(q[names.index(n)] - limits[names.index(n), 0],
                            limits[names.index(n), 1] - q[names.index(n)])) for n in joints}
    return {'minimum_joint_margin_rad': min(margins.values()), 'joint_margins_rad': margins}


def goal_margin(q, names, limits, active=None, *, execution_settings=LEGACY):
    if execution_settings is LEGACY:
        return None
    return {**minimum_arm_margin(q, names, limits, active),
            'joint_margin_limit_rad': execution_settings.goal_joint_margin_rad}


def receipt_fields(settings=LEGACY, *, include_legacy=False):
    """Old evidence omits the profile; readers interpret absence as legacy."""
    if settings is LEGACY:
        return dict(execution_profile='legacy', execution_settings_source='legacy',
                    planner_lock_sha256=D05_SHA) if include_legacy else {}
    value = dict(execution_profile=settings.profile, execution_settings_source='config',
                 planner_lock_sha256=settings.planner_deployment['sha256'])
    if settings.profile == FIXTURE_PROFILE:
        value.update({f.name:getattr(settings,f.name) for f in fields(settings) if f.name.startswith('fixture_')})
    if settings.planner_deployment['lock_path'] != D06_LOCK:
        from .identity import load_lock
        from .dev_local import is_dev_local
        path = settings.planner_deployment['lock_path']
        if is_dev_local(json.loads(Path(path).read_bytes())):
            lock, actual = load_lock(path, tier='dev', boundary='dev_loop')
            if actual != settings.planner_deployment['sha256']:raise ValueError('planner_deployment.sha256: mismatch')
            value.update(deployment_kind='dev-local', formal_eligible=False)
    return value
