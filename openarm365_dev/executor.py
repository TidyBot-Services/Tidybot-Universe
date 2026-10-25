"""Native MuJoCo tracking of one-use authenticated V2 command curves."""
import hashlib,json,math,os,re,time,uuid
from functools import wraps
from pathlib import Path
import xml.etree.ElementTree as E
import numpy as np
from curobo_v2.execution_settings import receipt_fields
from curobo_v2.command_curve import sample_for_original_drive
from curobo_v2.client import PlanningRejected
from curobo_v2.attachment import effective_held_envelope, held_envelope_receipt
from .environment import PHYSICAL_TO_NATIVE,tcp_pose
from .world import is_descendant,movable_object_roots
from .base_motion import (BASE_SETTLE_CONSECUTIVE_STEPS, BASE_SETTLE_MAX_STEPS,
                          base_is_stationary, check_exact_base_path, base_joint_delta,
                          validate_delta, guard_base_and_arms)
from .base_motion import base_drift_limits, base_drift


def record_base_drift(method):
    """Record per-request drift only for explicit execution settings.

    Base moves measure drift before driving and coast after stopping. Commanded
    travel is separately reported as displacement, never compared to a hold limit.
    The separate journal row survives a rejected plan or a physics-step abort.
    """
    @wraps(method)
    def request(self, *args, **kwargs):
        limits = base_drift_limits(getattr(self, "execution_settings", None))
        if limits is None:
            return method(self, *args, **kwargs)
        self._drift_max = [0., 0.]
        self._drift_displacement_max = [0., 0.]
        self._drift_settle_reference = None
        self._drift_reference = self.base_hold.copy()
        self._drift_limits, self._drift_operation = limits, method.__name__
        status, error = 'failed', None
        self.record_drift_sample()
        try:
            result = method(self, *args, **kwargs)
            status = 'completed'
            result['base_drift'] = self.drift_receipt(limits, method.__name__)
            return result
        except BaseException as exc:
            error = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            self.record_drift_sample()
            self.log(self.journal, dict(event='base_drift', request_id=kwargs['request_id'],
                status=status, reason=error, base_drift=self.drift_receipt(limits, method.__name__)))
            del self._drift_max, self._drift_reference, self._drift_limits, self._drift_operation
            del self._drift_displacement_max, self._drift_settle_reference
    return request

CONTACT_EVENT_LIMIT=200
GRIPPER_MIMIC_CONTACT_BOUND_M = 0.010
# The observed banana rotates during the first post-close steps. Require ten
# adjacent TCP-relative samples below sensor-scale per-step motion before anchoring.
HELD_REFERENCE_STABLE_STEPS = 10
HELD_REFERENCE_POSITION_STEP_M = .0002  # 0.2 mm per control step
HELD_REFERENCE_ROTATION_STEP_RAD = math.radians(.05)  # 0.05 degree per step
HELD_REFERENCE_MAX_WAIT_STEPS = 60

def held_reference_mode(configured):
    if configured not in ('fixed','settled'):
        raise ValueError('held_reference_mode must be fixed or settled')
    override=os.environ.get('ATTN_DIAG_HELD_REFERENCE_MODE')
    if override is not None and override not in ('fixed','settled'):
        raise ValueError('ATTN_DIAG_HELD_REFERENCE_MODE must be fixed or settled')
    return dict(configured=configured,source='ATTN_DIAG_HELD_REFERENCE_MODE' if override is not None else 'config',
                mode=override if override is not None else configured)

def held_reference_step(previous,current,stable_count):
    """Pure adjacent-pose stability test; return streak and measured step motion."""
    from scipy.spatial.transform import Rotation
    displacement=float(np.linalg.norm(current[:3,3]-previous[:3,3]))
    rotation=float(Rotation.from_matrix(current[:3,:3]@previous[:3,:3].T).magnitude())
    stable_count=stable_count+1 if (displacement<=HELD_REFERENCE_POSITION_STEP_M and
        rotation<=HELD_REFERENCE_ROTATION_STEP_RAD) else 0
    return stable_count,displacement,rotation

def gripper_mimic_contact_bound(side, exemption_mode, contacts):
    """Relax only the finger side touching a movable object in this physics step."""
    if exemption_mode == 'movable_objects' and any(
            'finger' in row['robot_geom_name'] and
            f'openarm_{side}_' in row['robot_geom_name'] for row in contacts.values()):
        # A 44 mm single-finger stroke can be pushed back by one-sided contact;
        # cap the permitted asymmetry at 10 mm, never the full stroke.
        return GRIPPER_MIMIC_CONTACT_BOUND_M
    return .001

def contact_transitions(previous,current,physical_step):
    """Pure edge detector; keys identify robot/object geom pairs."""
    rows=[]
    for key in sorted(set(previous)-set(current)):
        rows.append(dict(event='movable_contact_end',physical_step=physical_step,**previous[key]))
    for key in sorted(set(current)-set(previous)):
        rows.append(dict(event='movable_contact_begin',physical_step=physical_step,**current[key]))
    return rows

def bounded_contact_events(events,already_recorded,limit=CONTACT_EVENT_LIMIT):
    available=max(0,limit-already_recorded)
    return events[:available],len(events)-min(len(events),available)

def is_expected_movable_contact(model,robot_body,other_body,robot_geom_name,
                                robot_root,chassis_name,movable_roots):
    """Classify one oriented robot/object pair using the observed-contact boundary."""
    return ((is_descendant(model,robot_body,robot_root) or robot_geom_name==chassis_name)
            and any(is_descendant(model,other_body,root) for root in movable_roots))

def observed_movable_contacts(env,robot_root,chassis_name):
    """Read MuJoCo contacts without changing any physical collision decision."""
    import mujoco
    sim=env.sim;m=sim.model;roots=movable_object_roots(env);observed={}
    for index,con in enumerate(sim.data.contact):
        if con.dist>=0:continue
        g1,g2=int(con.geom1),int(con.geom2)
        b1,b2=int(m.geom_bodyid[g1]),int(m.geom_bodyid[g2])
        for robot_geom,other_geom,robot_body,other_body in ((g1,g2,b1,b2),(g2,g1,b2,b1)):
            robot_name=m.geom_id2name(robot_geom) or ''
            if not is_expected_movable_contact(m,robot_body,other_body,robot_name,
                    robot_root,chassis_name,roots.values()):continue
            force=np.zeros(6);mujoco.mj_contactForce(m._model,sim.data._data,index,force)
            observed[(robot_geom,other_geom)]={'robot_geom_name':robot_name,
                'object_body_name':m.body_id2name(other_body),
                'contact_force_n':float(force[0]),'distance_m':float(con.dist)}
            break
    return observed

def diagnostic_settle_steps():
    raw=os.environ.get('ATTN_DIAG_SETTLE_STEPS','20')
    if not re.fullmatch(r'[0-9]+',raw) or int(raw)<=0:
        raise ValueError('ATTN_DIAG_SETTLE_STEPS must be a positive integer')
    return int(raw)

def diagnostic_held_trace_enabled():
    value=os.environ.get('ATTN_DIAG_HELD_TRACE')
    if value not in (None,'1'):
        raise ValueError('ATTN_DIAG_HELD_TRACE must be 1 when set')
    return value=='1'

def held_trace_value(body,relative,reference,forces,distances):
    """Format measured grasp data without changing the attachment reference."""
    from scipy.spatial.transform import Rotation
    return dict(object_body=body,rel_pos_tcp_mm=(relative[:3,3]*1000).tolist(),
        rel_rot_deg=math.degrees(Rotation.from_matrix(
            relative[:3,:3]@reference[:3,:3].T).magnitude()),
        reference=reference.tolist(),finger_contact_force_n=forces,
        finger_object_distances_mm=distances)

def finger_object_contact_measurements(env,side,bid):
    import mujoco
    sim=env.sim;m=sim.model;forces={'left':0.,'right':0.};distances={'left':[],'right':[]}
    for index,con in enumerate(sim.data.contact):
        if con.dist>0:continue
        for fg,og in ((int(con.geom1),int(con.geom2)),(int(con.geom2),int(con.geom1))):
            name=m.geom_id2name(fg) or ''
            if f'openarm_{side}_' not in name or not is_descendant(m,int(m.geom_bodyid[og]),bid):continue
            finger=next((f for f in ('left','right') if f'_{f}_finger_' in name),None)
            if finger is None:continue
            force=np.zeros(6);mujoco.mj_contactForce(m._model,sim.data._data,index,force)
            forces[finger]+=max(0.,float(force[0]))
            distances[finger].append(float(con.dist)*1000)
            break
    return forces,{finger:sorted(values) for finger,values in distances.items()}

def diagnostic_tracking_bound():
    raw=os.environ.get('ATTN_DIAG_TRACKING_BOUND','0.004')
    if not re.fullmatch(r'\+?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?',raw):
        raise ValueError('ATTN_DIAG_TRACKING_BOUND must be a positive finite decimal')
    try: value=float(raw)
    except ValueError as exc:raise ValueError('ATTN_DIAG_TRACKING_BOUND must be a positive finite decimal') from exc
    if not math.isfinite(value) or value<=0:
        raise ValueError('ATTN_DIAG_TRACKING_BOUND must be a positive finite decimal')
    return value

def official_timing_exceeds_ceiling(count,settle_steps):
    return count-settle_steps+20>100

def tracking_error_event(request_id,settle_steps,bound,names,target,current,qvel):
    errors=np.asarray(current)-np.asarray(target)
    maximum=int(np.argmax(abs(errors)))
    return dict(event='tracking_error',request_id=request_id,settle_steps=settle_steps,bound=bound,
        error=float(abs(errors[maximum])),joint_errors={name:float(value) for name,value in zip(names,errors)},
        max_error_joint=names[maximum],target=np.asarray(target).tolist(),current_state=np.asarray(current).tolist(),
        qvel=None if qvel is None else np.asarray(qvel).tolist())

class NativeExecutor:
    def __init__(self,env,planner,*,evidence,deadline,cancel_check,frame_callback=None,held_reference_configured='fixed',execution_settings=None):
        self.execution_settings=execution_settings
        self.env,self.planner=env,planner;self.evidence=Path(evidence)
        self.deadline,self.cancel_check=deadline,cancel_check;self.frame_callback=frame_callback
        self.epoch=uuid.uuid4().hex;self.sequence=0;self.physics_steps=0;self.failed=False
        self.goals=planner.state().copy();self.grips={'left':0.,'right':0.}
        self.parts=env.robots[0].composite_controller._action_split_indexes
        if any(self.parts[n][1]-self.parts[n][0]!=7 for n in ('left','right')):
            raise ValueError('Native dual-arm joint controller layout mismatch')
        if (self.parts['base'][1]-self.parts['base'][0]!=3 or
                env.robots[0].composite_controller.part_controllers['base'].name!='JOINT_VELOCITY'):
            raise ValueError('Native 3-axis velocity base controller layout mismatch')
        self.base_initial=env.sim.data.qpos[env.robots[0]._ref_base_joint_pos_indexes].copy()
        self.base_hold=self.base_initial.copy();self.base_moving=False;self.base_arm_start=None
        self.robot_root=env.sim.model.body_name2id('robot0_openarm_body_link0')
        xml=E.parse(planner.spec['robot_cfg']['kinematics']['urdf_path']).getroot()
        joints={j.get('name'):j for j in xml.findall('joint')}
        self.limits=np.array([[float(joints[n].find('limit').get(k)) for k in ('lower','upper')] for n in planner.names])
        self.max_speed={n:float(joints[n].find('limit').get('velocity')) for n in planner.names}
        self.gripper_limits={side:np.array([[float(joints[f'openarm_{side}_finger_joint{i}'].find('limit').get(k))
            for k in ('lower','upper','velocity')] for i in (1,2)]) for side in PHYSICAL_TO_NATIVE}
        self.moving=False;self.gripping=False;self.active_request=None
        self.held=None;self.held_transform=None;self.grasp_counts={'left':{},'right':{}}
        self.held_trace_enabled=diagnostic_held_trace_enabled()
        self.held_reference=held_reference_mode(held_reference_configured)
        self.held_trace_references={}
        self.contact_active={};self.contact_event_count=0;self.contact_event_overflow=0
        self.planner.attachment_provider=lambda:self.held.copy() if self.held else None
        self.journal=self.evidence/'native-actions.jsonl';self.physics=self.evidence/'native-physics.jsonl'
        self._step2=env.sim.step2
        def step2():
            self._step2()
            if self.moving:self.monitor()
        env.sim.step2=step2

    def log(self,path,value):
        value = dict(value, **receipt_fields(getattr(self,"execution_settings",None)))
        if hasattr(self, '_drift_max') and value.get('event') in ('receipt', 'base_failure', 'base_tracking'):
            value = dict(value, base_drift=self.drift_receipt(self._drift_limits, self._drift_operation))
        diagnostic = held_envelope_receipt(getattr(self, "execution_settings", None))
        if diagnostic:value = dict(value, **diagnostic)
        with path.open('a') as f:
            f.write(json.dumps(value,sort_keys=True,allow_nan=False)+'\n');f.flush();os.fsync(f.fileno())

    def check_live(self):
        if self.failed:raise RuntimeError('Native executor latched after failure; reset required')
        if self.cancel_check():raise InterruptedError('operator cancelled')
        if time.monotonic()>=self.deadline:raise TimeoutError('native execution deadline')

    def action(self):
        a=np.zeros(len(self.env.action_spec[0]))
        for side,native in PHYSICAL_TO_NATIVE.items():
            indices=[self.planner.names.index(f'openarm_{side}_joint{i}') for i in range(1,8)]
            start,end=self.parts[native];a[start:end]=self.goals[indices]
            start,end=self.parts[native+'_gripper'];a[start:end]=self.grips[side]
        if self.base_moving:
            start,end=self.parts['base'];a[start:end]=self.base_command
        return a

    def monitor(self):
        self.physics_steps+=1
        trace_enabled=getattr(self,'held_trace_enabled',False)
        sim=self.env.sim;r=self.env.robots[0];q=self.planner.state()
        v=np.asarray(sim.data.qvel[r._ref_arm_joint_vel_indexes],float)
        if np.any(q<self.limits[:,0]) or np.any(q>self.limits[:,1]):raise RuntimeError('Measured native joint limit exceeded')
        if np.any(abs(v)>np.array([self.max_speed[n] for n in self.planner.names])):
            raise RuntimeError('Measured native joint velocity exceeded URDF limit')
        base=sim.data.qpos[r._ref_base_joint_pos_indexes]
        if hasattr(self, '_drift_max'):
            self.record_drift_sample()
        guard_base_and_arms(base,self.base_hold,q,self.base_arm_start if self.base_moving else None,
            execution_settings=getattr(self,"execution_settings",None))
        gripper_q={side:np.asarray(sim.data.qpos[r._ref_gripper_joint_pos_indexes[native]],float) for side,native in PHYSICAL_TO_NATIVE.items()}
        gripper_v={side:np.array([sim.data.qvel[sim.model.get_joint_qvel_addr(name)] for name in r.gripper[native].joints])
            for side,native in PHYSICAL_TO_NATIVE.items()}
        chassis_name=self.planner.chassis_binding.get('native_geom','')
        contact_now=observed_movable_contacts(self.env,self.robot_root,chassis_name)
        exemption_mode=getattr(self.planner,'contact_exemption_mode','none')
        relaxed_mimic={}
        for side,values in gripper_q.items():
            lim=self.gripper_limits[side]
            if np.any(values<lim[:,0]) or np.any(values>lim[:,1]) or np.any(abs(gripper_v[side])>lim[:,2]):
                raise RuntimeError('Measured native gripper position/velocity exceeds original URDF limit')
            mimic_bound=gripper_mimic_contact_bound(side,exemption_mode,contact_now)
            if mimic_bound==.001:
                if abs(values[0]-values[1])>.001:raise RuntimeError('Measured gripper mimic error exceeds original 1mm bound')
            else:
                mimic_error=float(abs(values[0]-values[1]))
                if mimic_error>mimic_bound:raise RuntimeError('Measured gripper mimic error exceeds contact 10mm bound')
                relaxed_mimic[side]=(mimic_error,mimic_bound)
        contacts=[]
        from .attachment import bilateral_contacts,object_pose
        bilateral=bilateral_contacts(self.env)
        for side in PHYSICAL_TO_NATIVE:
            for bid in set(self.grasp_counts[side])|bilateral[side]:
                self.grasp_counts[side][bid]=self.grasp_counts[side].get(bid,0)+1 if bid in bilateral[side] else 0
        held_error=None
        if self.held and not self.gripping:
            side=self.held['holder'];bid=self.held['target_id']
            if bid not in bilateral[side]:raise RuntimeError('Measured bilateral grasp contact lost during transport')
            relative=np.linalg.inv(tcp_pose(self.env,side))@object_pose(self.env,bid)
            from scipy.spatial.transform import Rotation
            translation,rotation=effective_held_envelope(getattr(self, "execution_settings", None))
            if (np.linalg.norm(relative[:3,3]-self.held_transform[:3,3])>translation or
                    Rotation.from_matrix(relative[:3,:3]@self.held_transform[:3,:3].T).magnitude()>rotation):
                held_error=('Measured object/hand transform exceeds '
                            f'configured {translation*1000:g}mm/{rotation:g}rad bound'
                            if held_envelope_receipt(getattr(self, "execution_settings", None)) else
                            'Measured object/hand transform exceeds original 4mm/0.03rad bound')
                if not trace_enabled:raise RuntimeError(held_error)
        objects=set(getattr(self.env,'obj_body_id',{}).values())
        exemption_enabled=exemption_mode=='movable_objects'
        movable_roots=tuple(movable_object_roots(self.env).values()) if exemption_enabled else ()
        expected_movable_contacts=[]
        for con in sim.data.contact:
            g1,g2=int(con.geom1),int(con.geom2);b1,b2=int(sim.model.geom_bodyid[g1]),int(sim.model.geom_bodyid[g2])
            a=is_descendant(sim.model,b1,self.robot_root);b=is_descendant(sim.model,b2,self.robot_root)
            chassis1=exemption_enabled and sim.model.geom_id2name(g1)==chassis_name
            chassis2=exemption_enabled and sim.model.geom_id2name(g2)==chassis_name
            if con.dist>=0 or not (a or b or chassis1 or chassis2):continue
            if a and b:
                if sim.model.body_weldid[b1]==sim.model.body_weldid[b2]:continue
                n1=sim.model.geom_id2name(g1) or '';n2=sim.model.geom_id2name(g2) or ''
                opposing_fingers=('finger' in n1 and 'finger' in n2 and
                    any(f'openarm_{s}_' in n1 and f'openarm_{s}_' in n2 for s in ('left','right')))
                if opposing_fingers:continue
                contacts.append([n1,n2,float(con.dist)]);continue
            if exemption_enabled:
                expected=False
                for robot_geom,other_geom,robot_body,other_body in ((g1,g2,b1,b2),(g2,g1,b2,b1)):
                    robot_name=sim.model.geom_id2name(robot_geom) or ''
                    if is_expected_movable_contact(sim.model,robot_body,other_body,robot_name,
                            self.robot_root,chassis_name,movable_roots):
                        expected_movable_contacts.append([robot_name,sim.model.geom_id2name(other_geom),float(con.dist)])
                        expected=True
                        break
                if expected:continue
                contacts.append([sim.model.geom_id2name(g1),sim.model.geom_id2name(g2),float(con.dist)])
                continue
            robot_geom=g1 if a else g2;other_body=b2 if a else b1
            robot_name=sim.model.geom_id2name(robot_geom) or ''
            allowed=('finger' in robot_name and
                ((self.gripping and any(is_descendant(sim.model,other_body,obj) for obj in objects)) or
                 (self.held and is_descendant(sim.model,other_body,self.held['target_id']))))
            if not allowed:contacts.append([sim.model.geom_id2name(g1),sim.model.geom_id2name(g2),float(con.dist)])
        events,dropped=bounded_contact_events(contact_transitions(self.contact_active,contact_now,self.physics_steps),self.contact_event_count)
        for event in events:self.log(self.journal,dict(request_id=self.active_request,**event))
        self.contact_event_count+=len(events);self.contact_event_overflow+=dropped
        self.contact_active=contact_now
        row=dict(request_id=self.active_request,physics_step=self.physics_steps,qpos=q.tolist(),qvel=v.tolist(),
            base_qpos=base.tolist(),gripper_qpos={side:v.tolist() for side,v in gripper_q.items()},
            gripper_qvel={side:v.tolist() for side,v in gripper_v.items()},
            tcp={side:tcp_pose(self.env,side).tolist() for side in PHYSICAL_TO_NATIVE},unexpected_contacts=contacts)
        row['bilateral_contact_objects']={side:sorted(values) for side,values in bilateral.items()}
        if relaxed_mimic:
            row['mimic_error_m']={side:pair[0] for side,pair in relaxed_mimic.items()}
            row['mimic_bound_m']={side:pair[1] for side,pair in relaxed_mimic.items()}
        if expected_movable_contacts:row['expected_movable_contacts']=expected_movable_contacts
        if trace_enabled:
            trace=[]
            targets={(side,bid) for side,values in bilateral.items() for bid in values}
            if self.held:targets.add((self.held['holder'],self.held['target_id']))
            for side,bid in sorted(targets):
                relative=np.linalg.inv(tcp_pose(self.env,side))@object_pose(self.env,bid)
                reference=self.held_trace_references.setdefault((side,bid),relative.copy())
                forces,distances=finger_object_contact_measurements(self.env,side,bid)
                trace.append(held_trace_value(sim.model.body_id2name(bid),relative,reference,forces,distances))
            if trace:row['held_trace']=trace
        self.log(self.physics,row)
        if held_error:raise RuntimeError(held_error)
        if contacts:raise RuntimeError('Unexpected physical robot/world contact')

    def control_step(self):
        if self.sequence>=self.env.horizon:raise RuntimeError('Native control-step budget exhausted')
        self.check_live();self.env.step(self.action());self.sequence+=1
        if self.frame_callback:self.frame_callback(self.sequence)

    def apply(self,response,*,request_id):
        self.check_live()
        settle_steps=diagnostic_settle_steps();bound=diagnostic_tracking_bound()
        plan_id=response['plan_id']
        response=self.planner.claim(plan_id,self.epoch)
        dt=float(self.env.control_timestep)
        count=math.ceil(response['time_from_start_seconds'][-1]/dt)+settle_steps
        if official_timing_exceeds_ceiling(count,settle_steps):raise PlanningRejected('Official timing exceeds 100-control-step action ceiling')
        kin=self.planner.spec['robot_cfg']['kinematics']['cspace']
        samples,certificate=sample_for_original_drive(response,joint_names=self.planner.names,
            maximum_speed=self.max_speed,position_limits=self.limits,max_acceleration=kin['max_acceleration'],
            max_jerk=kin['max_jerk'],dt=dt,step_budget=count,settle_steps=settle_steps)
        self.active_request=request_id;start=self.sequence;before=self.planner.state().copy()
        intent=dict(event='intent',request_id=request_id,epoch=self.epoch,
            plan_id=plan_id,start_sequence=start,command_curve=certificate)
        if 'max_joint_jump' in response:
            intent.update(max_joint_jump=response['max_joint_jump'],
                max_joint_jump_source=response['max_joint_jump_source'])
        self.log(self.journal,intent)
        if response.get('contact_exemption'):
            audit=dict(event='contact_exemption',request_id=request_id,**response['contact_exemption'])
            if getattr(self.planner,'contact_exemption_mode','none')=='movable_objects':
                audit['physical_monitor_allows_movable_contacts']=True
            self.log(self.journal,audit)
        self.contact_event_count=0;self.contact_event_overflow=0
        self.moving=True
        try:
            for target in samples[1:]:self.goals=target.copy();self.control_step()
            for _ in range(settle_steps):self.control_step()
            current=self.planner.state()
            error=float(np.max(abs(current-samples[-1])))
            if error>bound:
                try:
                    r=self.env.robots[0]
                    qvel=np.asarray(self.env.sim.data.qvel[r._ref_arm_joint_vel_indexes],float)
                    if not np.all(np.isfinite(qvel)):qvel=None
                except (AttributeError,IndexError,KeyError,TypeError):qvel=None
                self.log(self.journal,tracking_error_event(request_id,settle_steps,bound,
                    self.planner.names,samples[-1],current,qvel))
                raise RuntimeError('Native joint tracking exceeds 4mrad endpoint bound')
            receipt=dict(status='completed',request_id=request_id,epoch=self.epoch,start_sequence=start,
                sequence=self.sequence,physics_steps=self.physics_steps,before_qpos=before.tolist(),
                after_qpos=self.planner.state().tolist(),maximum_endpoint_joint_error_rad=error)
            receipt.update(receipt_fields(getattr(self,"execution_settings",None)))
            receipt.update(held_envelope_receipt(getattr(self, "execution_settings", None)))
            self.log(self.journal,dict(event='receipt',**receipt));return receipt
        except BaseException:
            self.failed=True;self.epoch=uuid.uuid4().hex;self.planner.ledger.invalidate('native_failure');raise
        finally:
            if self.contact_event_overflow:self.log(self.journal,dict(event='movable_contact_overflow',
                request_id=request_id,physical_step=self.physics_steps,dropped=self.contact_event_overflow,limit=CONTACT_EVENT_LIMIT))
            self.moving=False;self.active_request=None

    def record_drift_sample(self):
        if not hasattr(self, '_drift_max'):
            return
        base = self.env.sim.data.qpos[self.env.robots[0]._ref_base_joint_pos_indexes]
        measured = base_drift(base, self._drift_reference)
        self._drift_displacement_max = [max(a, b) for a, b in zip(self._drift_displacement_max, measured)]
        if self._drift_operation == 'base_move':
            if self._drift_settle_reference is not None:
                measured = base_drift(base, self._drift_settle_reference)
            elif getattr(self, 'base_moving', False):
                return
        self._drift_max = [max(a, b) for a, b in zip(self._drift_max, measured)]

    def drift_receipt(self, limits, operation):
        return dict(max_translation_m=self._drift_max[0], max_yaw_rad=self._drift_max[1],
            translation_limit_m=limits[0], yaw_limit_rad=limits[1],
            reference_base_qpos=self._drift_reference.tolist(),
            settle_reference_base_qpos=None if self._drift_settle_reference is None else self._drift_settle_reference.tolist(),
            measurement_scope='pre-drive hold and post-stop coast' if operation == 'base_move' else 'held base',
            max_displacement_translation_m=self._drift_displacement_max[0],
            max_displacement_yaw_rad=self._drift_displacement_max[1],
            includes_commanded_motion=False)

    @record_base_drift
    def move_joints(self,target,*,side,request_id):
        self.check_live()
        # Held-object transport needs its own measured attachment model. Until
        # that adapter is available this entry fails closed, never ignores it.
        if self.any_object_contact() and not self.held:raise PlanningRejected('Measured object contact requires a confirmed bilateral grasp before arm motion')
        response=self.planner.plan_joints(target,side=side,epoch=self.epoch)
        return self.apply(response,request_id=request_id)

    @record_base_drift
    def base_move(self,dx,dy,dyaw,*,request_id):
        self.check_live()
        delta=validate_delta(dx,dy,dyaw)
        from .world import snapshot
        before=np.asarray(self.env.sim.data.qpos[self.env.robots[0]._ref_base_joint_pos_indexes],float).copy()
        if base_drift_limits(getattr(self,"execution_settings",None)) is not None:
            guard_base_and_arms(before,self.base_hold,None,execution_settings=self.execution_settings)
        elif np.max(abs(before-self.base_hold))>.001:
            raise RuntimeError('Uncommanded base drift exceeded 1mm/1mrad')
        world=snapshot(self.env,chassis_binding=self.planner.chassis_binding)
        held=getattr(self,'held',None)
        try: collision=check_exact_base_path(self.env,world,self.planner.chassis_binding,delta,self.base_initial,
            **({'held_body_id':held['target_id']} if held else {}),
            **({} if getattr(self,'execution_settings',None) is None else dict(execution_settings=self.execution_settings)))
        except PlanningRejected as exc:
            self.log(self.journal,dict(event='base_collision_rejected',request_id=request_id,
                before_base_qpos=before.tolist(),delta=delta.tolist(),world_sha256=world['world_sha256'],
                collision_check=getattr(exc,'collision_check',None),reason=str(exc)))
            raise
        target=before+base_joint_delta(delta,before,self.base_initial)
        arm_before=self.planner.state().copy();self.goals=arm_before.copy()
        self.base_arm_start=arm_before;self.active_request=request_id;start=self.sequence
        self.log(self.journal,dict(event='base_intent',request_id=request_id,epoch=self.epoch,
            start_sequence=start,before_base_qpos=before.tolist(),target_base_qpos=target.tolist(),
            delta_body=delta.tolist(),collision_check=collision,arm_before_qpos=arm_before.tolist()))
        self.base_moving=True;self.moving=True;self.base_command=np.zeros(3)
        self.contact_event_count=0;self.contact_event_overflow=0
        started=time.monotonic()
        settle_start=None;settle_steps=0
        try:
            # At 20 Hz, 80 steps allow a .20 m / .30 rad command to settle.
            # The native controller maps normalized actions to .15/.15/.30
            # velocity actuators and rotates XY actions back to reset axes.
            for _ in range(80):
                actual=np.asarray(self.env.sim.data.qpos[self.env.robots[0]._ref_base_joint_pos_indexes],float)
                error=target-actual
                if np.linalg.norm(error[:2])<=.005 and abs(error[2])<=.005:break
                desired=np.clip(4.0*error,[-.12,-.12,-.24],[.12,.12,.24])
                angle=float(actual[2]-self.base_initial[2]);co,si=math.cos(angle),math.sin(angle)
                normalized=desired/np.array([.15,.15,.3])
                self.base_command=np.array([co*normalized[0]-si*normalized[1],
                    si*normalized[0]+co*normalized[1],normalized[2]])
                if np.any(abs(self.base_command)>1+1e-9):
                    raise RuntimeError('Base closed-loop command exceeds native action bounds')
                self.control_step()
            self.base_command=np.zeros(3)
            settle_start=np.asarray(self.env.sim.data.qpos[self.env.robots[0]._ref_base_joint_pos_indexes],float).copy()
            if hasattr(self, '_drift_max'):
                self._drift_settle_reference=settle_start.copy()
            previous=settle_start
            stationary_steps=0
            for _ in range(BASE_SETTLE_MAX_STEPS):
                self.control_step()
                settle_steps+=1
                current=np.asarray(self.env.sim.data.qpos[self.env.robots[0]._ref_base_joint_pos_indexes],float).copy()
                guard_base_and_arms(current,self.base_hold,self.planner.state(),arm_before)
                stationary_steps=(stationary_steps+1 if base_is_stationary(previous,current,self.env.control_timestep)
                                  else 0)
                previous=current
                if stationary_steps>=BASE_SETTLE_CONSECUTIVE_STEPS:break
            else:
                raise RuntimeError(f'Base did not settle within {BASE_SETTLE_MAX_STEPS} control steps')
            after=np.asarray(self.env.sim.data.qpos[self.env.robots[0]._ref_base_joint_pos_indexes],float).copy()
            final_error=target-after;arm_change=self.planner.state()-arm_before
            result=dict(status='completed',request_id=request_id,epoch=self.epoch,start_sequence=start,
                sequence=self.sequence,physics_steps=self.physics_steps,before_base_qpos=before.tolist(),
                after_base_qpos=after.tolist(),target_base_qpos=target.tolist(),
                base_error=final_error.tolist(),arm_joint_change=arm_change.tolist(),
                base_settle_steps=settle_steps,base_settle_displacement=(after-settle_start).tolist(),
                elapsed_seconds=time.monotonic()-started,collision_check=collision)
            self.log(self.journal,dict(event='base_tracking',**result))
            if np.linalg.norm(final_error[:2])>.005 or abs(final_error[2])>.005:
                raise RuntimeError('Base motion failed to converge within 5mm/5mrad')
            if np.max(abs(arm_change))>.004:
                raise RuntimeError('Arm joint drift during base motion exceeded 4mrad')
            self.base_hold=after.copy();self.planner.prepared.clear();self.planner.ledger.invalidate('base_moved')
            result.update(receipt_fields(getattr(self,"execution_settings",None)))
            result.update(held_envelope_receipt(getattr(self, "execution_settings", None)))
            self.log(self.journal,dict(event='receipt',**result));return result
        except BaseException as exc:
            after=np.asarray(self.env.sim.data.qpos[self.env.robots[0]._ref_base_joint_pos_indexes],float).copy()
            self.log(self.journal,dict(event='base_failure',request_id=request_id,start_sequence=start,
                sequence=self.sequence,before_base_qpos=before.tolist(),after_base_qpos=after.tolist(),
                target_base_qpos=target.tolist(),base_error=(target-after).tolist(),
                arm_joint_change=(self.planner.state()-arm_before).tolist(),
                base_settle_steps=settle_steps,
                base_settle_displacement=(after-settle_start).tolist() if settle_start is not None else None,
                elapsed_seconds=time.monotonic()-started,collision_check=collision,
                reason=f'{type(exc).__name__}: {exc}'))
            self.failed=True;self.epoch=uuid.uuid4().hex;self.planner.ledger.invalidate('base_failure');raise
        finally:
            if self.contact_event_overflow:self.log(self.journal,dict(event='movable_contact_overflow',
                request_id=request_id,physical_step=self.physics_steps,dropped=self.contact_event_overflow,limit=CONTACT_EVENT_LIMIT))
            self.base_command=np.zeros(3);self.base_moving=False;self.moving=False;self.base_arm_start=None;self.active_request=None

    def any_object_contact(self):
        sim=self.env.sim;objects=set(getattr(self.env,'obj_body_id',{}).values())
        for con in sim.data.contact:
            if con.dist>0:continue
            bodies=[int(sim.model.geom_bodyid[g]) for g in (con.geom1,con.geom2)]
            if any(is_descendant(sim.model,b,self.robot_root) for b in bodies) and any(any(is_descendant(sim.model,b,obj) for obj in objects) for b in bodies):return True
        return False

    @record_base_drift
    def move_delta(self,side,dx,dy,dz,rotation_delta=None,*,request_id):
        self.check_live()
        from scipy.spatial.transform import Rotation
        delta=np.array([dx,dy,dz],float);rotation=np.array(rotation_delta if rotation_delta is not None else [0,0,0],float)
        if (delta.shape!=(3,) or rotation.shape!=(3,) or not np.isfinite(np.r_[delta,rotation]).all()
                or np.any(abs(delta)>.05) or np.any(abs(rotation)>.5)):
            raise ValueError('World-frame deltas limited to .05m/.5rad per coordinate')
        if self.any_object_contact() and not self.held:raise PlanningRejected('Object contact requires confirmed bilateral grasp before arm motion')
        current=tcp_pose(self.env,side)
        response=self.planner.plan_pose(current[:3,3]+delta,Rotation.from_rotvec(rotation).as_matrix()@current[:3,:3],side=side,epoch=self.epoch)
        return self.apply(response,request_id=request_id)

    @record_base_drift
    def gripper(self,side,command,*,request_id):
        self.check_live()
        settings=getattr(self,"execution_settings",None)
        require_attachment = settings is not None and settings.attach_after_grasp
        if side not in PHYSICAL_TO_NATIVE or command not in (-1.,1.):raise ValueError('Explicit physical gripper/command required')
        self.active_request=request_id;self.gripping=True;self.moving=True;start=self.sequence
        self.contact_event_count=0;self.contact_event_overflow=0
        self.log(self.journal,dict(event='gripper_intent',request_id=request_id,side=side,command=command,sequence=start))
        wait_steps=0
        try:
            if command<0 and self.held and self.held['holder']==side:
                self.held=None;self.held_transform=None;self.planner.ledger.invalidate('release')
            if command<0 and self.held_trace_enabled:
                self.held_trace_references={key:value for key,value in self.held_trace_references.items() if key[0]!=side}
            # Native position actuators have soft limits; keep commanded endpoints
            # 1mm inside the original [0,.044] range. Never clamp measured state.
            self.grips[side]=command*(1-2*.001/.044)
            for _ in range(20):self.control_step()
            if command>0:
                candidates=[bid for bid,count in self.grasp_counts[side].items() if count>=12]
                if len(candidates)>1:raise RuntimeError('Ambiguous measured grasp target')
                if require_attachment and not candidates:
                    raise PlanningRejected('Close did not confirm bilateral grasp; attached transport forbidden')
                if candidates:
                    if self.held and self.held['holder']!=side:raise RuntimeError('Dual-held transport is outside this dev profile')
                    from .attachment import object_pose
                    self.held=dict(holder=side,target_id=candidates[0])
                    reference=np.linalg.inv(tcp_pose(self.env,side))@object_pose(self.env,candidates[0])
                    if self.held_reference['mode']=='settled':
                        initial_reference=reference
                        stable_count=0;max_displacement=0.;max_rotation=0.
                        for _ in range(HELD_REFERENCE_MAX_WAIT_STEPS):
                            self.control_step()
                            current=np.linalg.inv(tcp_pose(self.env,side))@object_pose(self.env,candidates[0])
                            stable_count,_,_=held_reference_step(reference,current,stable_count)
                            from scipy.spatial.transform import Rotation
                            displacement=float(np.linalg.norm(current[:3,3]-initial_reference[:3,3]))
                            rotation=float(Rotation.from_matrix(
                                current[:3,:3]@initial_reference[:3,:3].T).magnitude())
                            wait_steps+=1
                            max_displacement=max(max_displacement,displacement)
                            max_rotation=max(max_rotation,rotation)
                            reference=current
                            if stable_count>=HELD_REFERENCE_STABLE_STEPS:break
                        self.log(self.journal,dict(event='held_reference',request_id=request_id,side=side,
                            object_body_id=candidates[0],wait_steps=wait_steps,
                            max_displacement_mm=max_displacement*1000,max_rotation_deg=math.degrees(max_rotation),
                            reference=reference.tolist(),stable=stable_count>=HELD_REFERENCE_STABLE_STEPS,
                            held_reference_unstable=stable_count<HELD_REFERENCE_STABLE_STEPS))
                    self.held_transform=reference
                    self.planner.ledger.invalidate('grasp_acquired')
            result=dict(status='completed',request_id=request_id,physical_side=side,start_sequence=start,sequence=self.sequence)
            result.update(held_reference_wait_steps=wait_steps,held_reference=self.held_reference)
            if require_attachment:
                result.update(attach_after_grasp=True, attached_object=self.held.copy() if self.held else None)
            result.update(receipt_fields(getattr(self,"execution_settings",None)))
            result.update(held_envelope_receipt(getattr(self, "execution_settings", None)))
            self.log(self.journal,dict(event='receipt',**result));return result
        except BaseException:
            self.failed=True;self.epoch=uuid.uuid4().hex;self.planner.ledger.invalidate('gripper_failure');raise
        finally:
            if self.contact_event_overflow:self.log(self.journal,dict(event='movable_contact_overflow',
                request_id=request_id,physical_step=self.physics_steps,dropped=self.contact_event_overflow,limit=CONTACT_EVENT_LIMIT))
            self.active_request=None;self.gripping=False;self.moving=False

    def close(self):
        self.epoch=uuid.uuid4().hex;self.planner.ledger.invalidate('stop')
        self.env.sim.step2=self._step2
        return self.planner.close()
