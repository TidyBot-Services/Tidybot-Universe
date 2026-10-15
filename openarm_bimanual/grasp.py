"""Version 2 gripper owner. No planning, scene edits, or object constraints.

The host calls observe once AFTER each physical step and applies only the returned
finger drive targets BEFORE the next step. An action ending never stops this owner.
"""
from dataclasses import dataclass, field, asdict
import copy
import math
from numbers import Real
import numpy as np

SCHEMA = 'openarm.grasp/2'
SIDES = ('left', 'right')


class GraspError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise GraspError('invalid_number', name + ' must be finite')
    return float(value)


@dataclass(frozen=True)
class Limits:
    lower_m: float
    upper_m: float
    official_drive_force_limit_n: float
    zero_gap_m: float
    dt_s: float = 1 / 240
    step_budget: int = 720
    slew_m_s: float = .02
    finger_error_m: float = .001
    finger_velocity_m_s: float = .005
    mimic_error_m: float = .001
    stable_steps: int = 12
    object_position_m: float = .004
    object_rotation_rad: float = .03

    def gap(self, positions):
        return self.zero_gap_m + sum(positions)

    def displacement(self, gap):
        return (gap - self.zero_gap_m) / 2


@dataclass
class Feedback:
    step: int
    epoch: int
    q_m: list
    v_m_s: list
    target_m: list
    target_id: int
    bilateral: bool
    contact_force_n: list  # normal impulse / dt, NOT measured joint drive force
    relative_pose: list
    valid: bool = True
    identity_valid: bool = True
    raw_contacts: dict = field(default_factory=dict)
    force_contact_points: dict = field(default_factory=dict)
    full_group_completion: bool = True
    measured_drive_force_n: object = None  # unavailable on the verified SAPIEN build


@dataclass
class FingerState:
    mode: str = 'idle'
    grasped: bool = False
    holding: bool = False
    target_id: object = None
    desired_contact_force_n: float = 0
    drive_force_limit_n: float = 0
    goal_m: float = 0
    stable_steps: int = 0
    stable_confirmed: bool = False
    reference_pose: object = None
    window: list = field(default_factory=list)
    filtered_force_n: object = None
    action_id: object = None
    fault: object = None
    release_intent: bool = False


def pose_error(reference, current):
    a, b = np.asarray(reference, float), np.asarray(current, float)
    if a.shape != (4, 4) or b.shape != (4, 4) or not np.isfinite([a, b]).all():
        raise GraspError('feedback_invalid', 'Invalid object/TCP transform')
    for x in (a, b):
        if not np.allclose(x[3], [0, 0, 0, 1], atol=1e-6) or not np.allclose(x[:3, :3].T @ x[:3, :3], np.eye(3), atol=1e-5) or np.linalg.det(x[:3, :3]) < .999:
            raise GraspError('feedback_invalid', 'Transform is not rigid')
    angle = math.acos(float(np.clip((np.trace(a[:3, :3] @ b[:3, :3].T) - 1) / 2, -1, 1)))
    return float(np.linalg.norm(a[:3, 3] - b[:3, 3])), angle


class GraspCoordinator:
    """One owner for both grippers and ownership. stop_callback is mandatory."""
    def __init__(self, limits, *, epoch, target_id, stop_callback, emit=lambda row: None):
        if set(limits) != set(SIDES) or not callable(stop_callback):
            raise ValueError('Two gripper limits and native Stop callback required')
        self.limits = limits
        self.epoch = epoch
        self.target_id = target_id
        self.stop_callback = stop_callback
        self.emit = emit
        self.fingers = {s: FingerState() for s in SIDES}
        self.actions = {}
        self.requests = {}
        self.last = {}
        self.handoff = None
        self.ownership = None
        self.fault = None
        self.suspended = False

    def snapshot(self):
        return dict(schema=SCHEMA, epoch=self.epoch, formal_eligible=False,
                    grippers={s: {k: v for k, v in asdict(f).items() if k != 'window'} for s, f in self.fingers.items()},
                    ownership=self.ownership, handoff=copy.deepcopy(self.handoff), fault=self.fault,
                    controller_suspended=self.suspended)

    def parse(self, body):
        common = {'schema', 'epoch', 'arm', 'request_id', 'mode', 'drive_force_limit_n'}
        if not isinstance(body, dict) or body.get('schema') != SCHEMA:
            raise GraspError('grasp_protocol_required', 'Explicit openarm.grasp/2 request required')
        if type(body.get('epoch')) is not int or body['epoch'] != self.epoch:
            raise GraspError('stale_epoch', 'Request belongs to an invalid epoch')
        if body.get('arm') not in SIDES or body.get('mode') not in {'acquire', 'position'}:
            raise GraspError('invalid_grasp', 'Explicit arm and acquire/position mode required')
        rid = body.get('request_id')
        if not isinstance(rid, str) or not 1 <= len(rid) <= 128:
            raise GraspError('invalid_request_id', 'Bounded request id required')
        geometry = {'jaw_gap_m', 'finger_displacement_m'} & body.keys()
        if len(geometry) != 1:
            raise GraspError('invalid_units', 'Specify jaw gap OR single finger displacement')
        expected = common | geometry | ({'target_id', 'desired_contact_force_n'} if body['mode'] == 'acquire' else set())
        if set(body) != expected:
            raise GraspError('invalid_grasp', 'Missing or unknown grasp fields')
        limit = self.limits[body['arm']]
        name = next(iter(geometry))
        value = number(body[name], name)
        q = limit.displacement(value) if name == 'jaw_gap_m' else value
        if not limit.lower_m <= q <= limit.upper_m:
            raise GraspError('out_of_bounds', 'Finger displacement outside loaded model limits')
        cap = number(body['drive_force_limit_n'], 'drive_force_limit_n')
        if not 0 < cap <= limit.official_drive_force_limit_n:
            raise GraspError('force_limit', 'Drive cap exceeds loaded official URDF limit')
        desired = 0
        if body['mode'] == 'acquire':
            if type(body['target_id']) is not int or body['target_id'] != self.target_id:
                raise GraspError('wrong_target', 'Only the native designated target may be acquired')
            desired = number(body['desired_contact_force_n'], 'desired_contact_force_n')
            # Coupled prismatic pair: driver does the work of both fingers.
            if not 0 < 2 * desired <= cap:
                raise GraspError('force_limit', 'Two-finger desired load exceeds driver cap')
        return q, cap, desired

    def command(self, body, *, step):
        return self._command(body, step=step)

    def _command(self, body, *, step, release_handoff=None):
        q, cap, desired = self.parse(body)
        rid = body['request_id']
        if rid in self.requests:
            previous, aid = self.requests[rid]
            if previous != body:
                raise GraspError('duplicate_conflict', 'Request id reused with different arguments')
            return copy.deepcopy(self.actions[aid])
        if self.fault or self.suspended:
            raise GraspError('physical_guard_latched', 'Reset required after Stop or physical fault')
        if any(a['status'] == 'running' for a in self.actions.values()):
            raise GraspError('action_busy', 'Await or cancel current gripper action')
        side = body['arm']; f = self.fingers[side]
        if self.handoff and self.handoff['phase'] in {'releasing','receiver_independent'}:
            raise GraspError('handoff_release_guard', 'Await ownership confirmation before another grasp')
        # Both participants remain under coordination. The private release path
        # carries the exact current handoff object, never a JSON bypass flag.
        if self.handoff and self.handoff['phase'] not in {'completed', 'aborted'} and body['mode'] == 'position':
            if release_handoff is not self.handoff or side != self.handoff['donor']:
                raise GraspError('handoff_release_guard', 'Use coordinated donor release')
        if body['mode'] == 'acquire' and f.holding:
            if (f.target_id != body['target_id'] or desired != f.desired_contact_force_n or cap != f.drive_force_limit_n):
                raise GraspError('holding_conflict', 'Release before changing an established grip contract')
            mode = 'maintain'
        else:
            mode = body['mode']
        aid = f'{self.epoch}:grasp:{len(self.actions) + 1}'
        a = dict(id=aid, epoch=self.epoch, schema=SCHEMA, status='running', mode=mode, arm=side,
                 start_step=step, physical_steps=0, step_budget=self.limits[side].step_budget,
                 requested=copy.deepcopy(body), grasped=f.grasped, holding=f.holding,
                 ownership=self.ownership, contact_hold_committed=f.holding, error=None)
        self.actions[aid] = a; self.requests[rid] = (copy.deepcopy(body), aid)
        f.action_id = aid
        if mode != 'maintain':
            f.mode = mode; f.goal_m = q; f.target_id = self.target_id if mode == 'acquire' else None
            f.desired_contact_force_n = desired; f.drive_force_limit_n = cap
            f.window = []; f.stable_steps = 0; f.stable_confirmed=False; f.reference_pose = None
            f.grasped = f.holding = False; f.release_intent = mode == 'position'
            if self.ownership == side and release_handoff is None: self.ownership = None
        a.update(grasped=f.grasped, holding=f.holding, ownership=self.ownership,
                 contact_hold_committed=f.holding)
        self.emit(dict(event='grasp_request', step=step, request=copy.deepcopy(body), response=copy.deepcopy(a)))
        return copy.deepcopy(a)

    def begin(self, donor, receiver, *, epoch, step):
        self._roles(donor, receiver, epoch)
        if self.fault or self.suspended: raise GraspError('physical_guard_latched', 'Reset required')
        if self.handoff and self.handoff['phase'] not in {'completed', 'aborted'}:
            raise GraspError('handoff_busy', 'Previous handoff still active')
        if any(a['status']=='running' for a in self.actions.values()):
            raise GraspError('action_busy', 'Await or cancel current gripper action')
        action=self.actions.get(self.fingers[donor].action_id) or {}
        f=self.fingers[donor]
        if not f.holding or not f.stable_confirmed or f.stable_steps < self.limits[donor].stable_steps or self.ownership != donor or action.get('status')!='completed':
            raise GraspError('donor_not_grasped', 'Donor requires a current stable grip certificate')
        self.handoff = dict(id=f'{epoch}:handoff:{step}', schema=SCHEMA, formal_eligible=False,donor=donor, receiver=receiver,
                            phase='receiving', started_step=step, common_steps=0, independent_steps=0)
        self.emit(dict(event='handoff_begin', step=step, **self.snapshot()))
        return copy.deepcopy(self.handoff)

    def _roles(self, donor, receiver, epoch):
        if type(epoch) is not int or epoch != self.epoch: raise GraspError('stale_epoch', 'Invalid epoch')
        if donor not in SIDES or receiver not in SIDES or donor == receiver:
            raise GraspError('invalid_handoff', 'Distinct donor and receiver required')

    def replay_release(self, donor, receiver, *, epoch, request_id):
        """Read-only retry, scoped to the current handoff's release action."""
        self._roles(donor, receiver, epoch)
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise GraspError('invalid_request_id', 'Bounded request id required')
        if self.fault or self.suspended:
            raise GraspError('physical_guard_latched', 'Reset required after Stop or physical fault')
        if request_id not in self.requests: return None
        _, aid = self.requests[request_id]
        h=self.handoff; a=self.actions[aid]
        if (not h or (h['donor'],h['receiver']) != (donor,receiver)
                or h.get('release_action') != aid or a.get('coordination_group') != h['id']):
            raise GraspError('duplicate_conflict', 'Request id belongs to another action or handoff')
        return copy.deepcopy(a)

    def release(self, donor, receiver, *, epoch, step, request_id):
        replay=self.replay_release(donor,receiver,epoch=epoch,request_id=request_id)
        if replay is not None: return replay
        h = self.handoff
        if self.fault or self.suspended or not h or (h['donor'], h['receiver']) != (donor, receiver) or h['phase'] != 'shared' or h['common_steps'] < self.limits[receiver].stable_steps or not all(self.fingers[s].holding and self.fingers[s].stable_confirmed and self.fingers[s].stable_steps>=self.limits[s].stable_steps for s in SIDES) or (self.actions.get(self.fingers[receiver].action_id) or {}).get('status')!='completed':
            raise GraspError('receiver_not_confirmed', 'Stable common hold required before release')
        limit = self.limits[donor]
        body = dict(schema=SCHEMA, epoch=epoch, arm=donor, request_id=request_id, mode='position',
                    finger_displacement_m=limit.upper_m, drive_force_limit_n=self.fingers[donor].drive_force_limit_n)
        receipt = self._command(body, step=step, release_handoff=h)
        h.update(phase='releasing', release_action=receipt['id'], receiver_confirmed_step=step)
        receipt.update(handoff=True, coordination_group=h['id'], participating_arms=list(SIDES))
        self.actions[receipt['id']].update(handoff=True, coordination_group=h['id'], participating_arms=list(SIDES))
        # Ownership remains donor until receiver demonstrates independent holding.
        self.ownership = donor
        self.emit(dict(event='handoff_release', step=step, **self.snapshot()))
        return receipt

    def _finish(self, action, step, ok, code=None, *, terminal_status=None):
        f = self.fingers[action['arm']]
        action.update(status=terminal_status or ('completed' if ok else 'failed'), finished_step=step,
                      grasped=f.grasped, holding=f.holding, ownership=self.ownership,
                      contact_hold_committed=f.holding,
                      error=None if ok else {'code': code, 'message': 'Grasp v2 evidence gate failed'})
        self.emit(dict(event='grasp_action_finished', step=step, action=copy.deepcopy(action)))

    def abort(self, reason, *, step, epoch=None, status='failed'):
        if self.suspended: return
        self.suspended = True
        self.fault = reason
        for f in self.fingers.values():
            f.mode = 'safety_hold'; f.holding = f.grasped = f.stable_confirmed = False; f.fault = reason
        self.ownership = None
        if self.handoff and self.handoff['phase'] != 'completed':
            self.handoff.update(phase='aborted', reason=reason, finished_step=step)
        for a in self.actions.values():
            if a['status'] == 'running':
                self._finish(a, step, False, reason, terminal_status=status)
        # Stop host commits bounded drive hold and invalidates all queued commands.
        self.stop_callback(reason)
        self.epoch = self.epoch + 1 if epoch is None else epoch
        self.emit(dict(event='grasp_stop', step=step, **self.snapshot()))

    def observe(self, samples):
        if set(samples) != set(SIDES):
            self.abort('feedback_invalid', step=max((f.step for f in samples.values()), default=0)); return
        step = samples['left'].step
        # Count executed steps even when that frame invalidates sensor evidence.
        if type(step) is int:
            for action in self.actions.values():
                if action['status']=='running':action['physical_steps']=max(0,step-action['start_step'])
        if self.suspended:
            self.last = samples
            return
        try:
            if samples['right'].step != step: raise GraspError('feedback_invalid', 'Two grippers must share a physical clock')
            for side in SIDES:
                x = samples[side]; l = self.limits[side]; f = self.fingers[side]
                vals = np.asarray([x.q_m, x.v_m_s, x.target_m, x.contact_force_n], float)
                if (type(x.step) is not int or x.step<0 or type(x.epoch) is not int
                    or any(type(flag) is not bool for flag in (x.valid,x.identity_valid,x.bilateral,x.full_group_completion))
                    or not x.valid or not x.identity_valid or x.epoch != self.epoch or type(x.target_id) is not int or x.target_id != self.target_id
                    or vals.shape != (4, 2) or not np.isfinite(vals).all() or np.any(np.asarray(x.contact_force_n) < 0)
                    or any(not isinstance(v,Real) or isinstance(v,(bool,np.bool_)) for row in (x.q_m,x.v_m_s,x.target_m,x.contact_force_n) for v in row)
                    or (side in self.last and x.step != self.last[side].step + 1)):
                    raise GraspError('runtime_identity_failed' if x.identity_valid is False else 'feedback_invalid', 'Invalid or missing physical feedback')
                pose_error(x.relative_pose, x.relative_pose)
                if min(x.q_m) < l.lower_m - .0001 or max(x.q_m) > l.upper_m + .0001 or abs(x.q_m[0] - x.q_m[1]) > l.mimic_error_m:
                    raise GraspError('gripper_limit_or_mimic', 'Original position/mimic safety gate')
                raw = np.asarray(x.contact_force_n)
                f.filtered_force_n = (raw if f.filtered_force_n is None else .25 * raw + .75 * np.array(f.filtered_force_n)).tolist()
                bilateral = bool(x.bilateral and min(x.contact_force_n) > 0)
                if f.mode in {'acquire', 'hold'}:
                    if f.reference_pose is None and bilateral: f.reference_pose = copy.deepcopy(x.relative_pose)
                    pe = pose_error(f.reference_pose, x.relative_pose) if f.reference_pose is not None else (0, 0)
                    safe_pose = pe[0] <= l.object_position_m and pe[1] <= l.object_rotation_rad
                    finger_error = max(abs(np.array(x.q_m) - np.array(x.target_m)))
                    speed_ok = max(abs(np.array(x.v_m_s))) <= l.finger_velocity_m_s
                    if f.holding and (not bilateral or not safe_pose or finger_error > l.finger_error_m):
                        raise GraspError('contact_lost' if not bilateral else 'object_slip' if not safe_pose else 'hold_tracking', 'Certified hold invalidated')
                    if f.holding and not speed_ok:
                        raise GraspError('hold_velocity', 'Certified finger speed exceeds frozen threshold')
                    force_in_range = min(x.contact_force_n)>=.5*f.desired_contact_force_n and max(x.contact_force_n)<=1.5*f.desired_contact_force_n
                    stable = bilateral and safe_pose and speed_ok and finger_error <= l.finger_error_m and force_in_range
                    if f.holding and not force_in_range:
                        raise GraspError('holding_force_unstable', 'Established grip force left its certified range')
                    if stable:
                        f.window.append(dict(q=x.q_m, pose=x.relative_pose, force=x.contact_force_n))
                        f.window = f.window[-l.stable_steps:]
                    else:
                        f.window = []
                        if not f.holding: f.reference_pose = None
                    f.stable_steps = len(f.window)
                    f.stable_confirmed=False
                    if len(f.window) == l.stable_steps:
                        positions = np.asarray([w['q'] for w in f.window])
                        forces = np.asarray([w['force'] for w in f.window])
                        # Motion and contact stability are independent of retargeting.
                        motion_ok = np.max(np.ptp(positions, axis=0)) <= .0002
                        force_ok = (np.min(forces) >= .5 * f.desired_contact_force_n and
                                    np.max(forces) <= 1.5 * f.desired_contact_force_n and
                                    np.max(np.ptp(forces, axis=0)) <= .5 * f.desired_contact_force_n)
                        pose_ok = all(pose_error(f.window[0]['pose'], w['pose'])[0] <= .0002 and pose_error(f.window[0]['pose'], w['pose'])[1] <= .002 for w in f.window)
                        f.stable_confirmed=bool(motion_ok and force_ok and pose_ok)
                        if f.stable_confirmed:
                            f.grasped = f.holding = True; f.mode = 'hold'
                            if self.ownership is None and (not self.handoff or self.handoff['phase'] in {'completed','aborted'}):
                                self.ownership = side
                    if bilateral:
                        # Bounded normal-force outer loop. q-target remains within
                        # frozen tracking tolerance; never a convergence certificate.
                        delta = .0005 * (float(np.mean(f.filtered_force_n)) - f.desired_contact_force_n) * l.dt_s
                        f.goal_m = float(np.clip(f.goal_m + np.clip(delta, -l.slew_m_s * l.dt_s, l.slew_m_s * l.dt_s),
                                                max(l.lower_m, max(x.q_m) - l.finger_error_m),
                                                min(l.upper_m, min(x.q_m) + l.finger_error_m)))
                action = self.actions.get(f.action_id)
                if action and action['status'] == 'running':
                    action['physical_steps'] = step - action['start_step']
                    if action['physical_steps'] >= l.step_budget:
                        legacy_ok = (max(abs(np.array(x.q_m) - np.array(x.target_m))) <= l.finger_error_m and max(abs(np.array(x.v_m_s))) <= l.finger_velocity_m_s)
                        action['legacy_position_completion'] = bool(legacy_ok)
                        action['legacy_full_group_completion']=x.full_group_completion
                        needs_grip=action['mode'] in {'acquire','maintain'}
                        grip_ok=f.holding and f.stable_confirmed
                        ok = bool(legacy_ok and x.full_group_completion and (grip_ok if needs_grip else True))
                        code='grasp_not_confirmed' if needs_grip and not grip_ok else 'convergence_timeout'
                        self._finish(action, step, ok, code)
                        if not ok: raise GraspError(code, 'Fixed action budget ended without required physical evidence')
            self.last = samples
            self._advance_handoff(step)
        except (GraspError, ValueError, TypeError) as exc:
            self.abort(getattr(exc, 'code', 'feedback_invalid'), step=step)
        self.emit(dict(event='grasp_sample', step=step, feedback={s: asdict(x) for s, x in samples.items()}, **self.snapshot()))

    def _advance_handoff(self, step):
        h = self.handoff
        if not h or h['phase'] in {'completed', 'aborted'}: return
        donor, receiver = h['donor'], h['receiver']
        if h['phase'] in {'receiving', 'shared'}:
            if not self.fingers[donor].holding:
                raise GraspError('donor_hold_lost', 'Donor must hold throughout acquisition')
            if all(self.fingers[s].holding and self.fingers[s].stable_confirmed for s in SIDES):
                h['common_steps'] += 1
                if h['common_steps'] >= self.limits[receiver].stable_steps and h['phase'] != 'shared':
                    h.update(phase='shared', shared_step=step)
                    self.emit(dict(event='handoff_shared', step=step, **self.snapshot()))
            else:
                h['common_steps']=0
                h['phase']='receiving'
        elif h['phase'] in {'releasing', 'receiver_independent'}:
            if not self.fingers[receiver].holding: raise GraspError('receiver_hold_lost', 'Receiver lost grip during release')
            a = self.actions[h['release_action']]
            if a['status'] == 'failed': raise GraspError('release_failed', 'Donor release failed')
            x = self.last[donor]; limit = self.limits[donor]
            released = (not x.bilateral and not any(x.contact_force_n) and
                        min(x.q_m) >= limit.upper_m - limit.finger_error_m and
                        max(abs(np.array(x.v_m_s))) <= limit.finger_velocity_m_s)
            if released and a['status'] == 'completed' and self.fingers[receiver].stable_confirmed:
                if h['phase'] != 'receiver_independent':
                    h.update(phase='receiver_independent', independent_step=step)
                    self.emit(dict(event='handoff_receiver_independent', step=step, **self.snapshot()))
                h['independent_steps'] += 1
                if h['independent_steps'] >= self.limits[receiver].stable_steps:
                    self.ownership = receiver
                    h.update(phase='completed', finished_step=step, ownership=receiver)
                    a.update(ownership=receiver, handoff_completed=True)
                    self.emit(dict(event='handoff_completed', step=step, **self.snapshot()))
            else: h['independent_steps'] = 0

    def targets(self):
        return {} if self.suspended else {s: f.goal_m for s, f in self.fingers.items() if f.mode in {'position', 'acquire', 'hold'}}
