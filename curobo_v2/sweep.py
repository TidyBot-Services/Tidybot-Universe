"""Conservative continuous command-curve geometry gate, outside the solver.

Bernstein hulls bound all joint values on each interval. Complete URDF ancestry
bounds every collision point's motion. PhysX contacts at an inflated distance
then enclose the whole interval. Refinement only strengthens a certificate;
it never plans, changes a waypoint/time, drops geometry or retries a solver.
"""
from pathlib import Path
from itertools import product
import hashlib,json,math,time,xml.etree.ElementTree as ET
import numpy as np
from scipy.spatial.transform import Rotation
from .command_curve import CommandCurve
from .client import PlanningRejected

SCHEMA='attentionbench.continuous-command-geometry.v1'
CLEARANCE=.005
# Upper bound on physics-engine queries per certificate. Recorded accepted certificates need 1100-6500 queries; the cap
# only stops a runaway search. It was 4096 together with a 2 s wall-clock limit: the wall-clock limit made the same
# command pass or fail with machine load, and ordinary 2-5 cm forward moves need about 6500 queries and 3.5 s.
# A certificate that runs out of queries stays unverified and the command is rejected; the clearance test is unchanged.
MAX_QUERIES=100000
PADDING=2e-5
_evidence=None

def configure_evidence(directory):
    global _evidence
    _evidence=Path(directory)

def canonical_input(response,order=None):
    names=response['joint_names'];order=names if order is None else order
    if len(set(names))!=len(names)or set(order)!=set(names):raise PlanningRejected('Certificate joint order mismatch')
    indices=[names.index(n)for n in order]
    try:
        q=np.asarray(response['trajectory'],float)
        times=np.asarray(response['time_from_start_seconds'],float)
        derivatives={k:np.asarray(response['trajectory_derivatives'][k],float)for k in ['velocity','acceleration']}
        if(q.ndim!=2 or len(q)<2 or q.shape[1]!=len(names) or times.shape!=(len(q),)
                or any(v.shape!=q.shape for v in derivatives.values())
                or not all(np.isfinite(v).all() for v in [q,times,*derivatives.values()])):
            raise ValueError('malformed command samples')
    except (KeyError,TypeError,ValueError) as exc:
        raise PlanningRejected('Certificate requires complete finite q/v/a with authoritative joint and time dimensions')from exc
    return dict(joint_names=list(order),trajectory=q[:,indices].tolist(),
        time_from_start_seconds=times.tolist(),
        trajectory_derivatives={k:v[:,indices].tolist()for k,v in derivatives.items()})

def input_sha256(response,order=None):
    return hashlib.sha256(json.dumps(canonical_input(response,order),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def verify_certificate(response):
    receipt=(response.get('validation')or{}).get('continuous_geometry')or{}
    if(receipt.get('schema')!=SCHEMA or receipt.get('result')!='PASS'or receipt.get('clearance_m')!=CLEARANCE
            or receipt.get('world_sha256')!=(response.get('validation')or{}).get('world_sha256')
            or (response.get('validation')or{}).get('continuous_motion',{}).get('result')!='PASS'
            or receipt.get('input_sha256')!=input_sha256(response,receipt.get('joint_names'))):
        raise PlanningRejected('Missing, failed, or stale continuous command geometry certificate; dispatch forbidden')
    return receipt

def split(control):
    rows=[control]
    while len(rows[-1])>1:rows.append((rows[-1][:-1]+rows[-1][1:])/2)
    return np.array([r[0]for r in rows]),np.array([r[-1]for r in rows[::-1]]),rows[-1][0]

def pose_of(c):
    out=np.eye(4);out[:3,3]=c['center'];out[:3,:3]=Rotation.from_quat(np.asarray(c.get('quat_wxyz',[1,0,0,0]))[[1,2,3,0]]).as_matrix();return out

def corners(half):return np.array(list(product(*[[-x,x]for x in half])),float)

def radius_of(shape):
    if hasattr(shape,'vertices'):r=float(np.max(np.linalg.norm(np.asarray(shape.vertices)*shape.scale,axis=1)))
    elif hasattr(shape,'half_size'):r=float(np.linalg.norm(shape.half_size))
    elif hasattr(shape,'half_length'):r=float(shape.half_length+shape.radius)
    elif hasattr(shape,'radius'):r=float(shape.radius)
    else:raise PlanningRejected('Unsupported moving geometry '+type(shape).__name__)
    return r+float(np.linalg.norm(shape.local_pose.p))+1e-7

class SweepValidator:
    def __init__(self,urdf,names,world,locks,attachment=None,*,execution_settings=None,measured_start=True):
        import sapien
        self.sapien=sapien;self.names=list(names);self.world=world;self.locks=locks;self.context=attachment
        self.departure_start_gaps={}
        self.execution_settings=execution_settings;self.measured_start=measured_start
        self.xml=ET.parse(urdf).getroot();self.joints={j.get('name'):j for j in self.xml.findall('joint')};self.incoming={j.find('child').get('link'):j for j in self.joints.values()}
        self.scene=sapien.Scene([sapien.physx.PhysxCpuSystem()]);self.scene.set_timestep(1/240);self.scene.physx_system.config.gravity=[0,0,0]
        loader=self.scene.create_urdf_loader();loader.fix_root_link=True;builder=loader.load_file_as_articulation_builder(str(urdf))
        for link in builder.link_builders:link.visual_records=[]
        self.robot=builder.build(fix_root_link=True,build_mimic_joints=True)
        self.actual_names=[j.name for j in self.robot.get_active_joints()];self.indices={n:i for i,n in enumerate(self.actual_names)}
        self.mapping=np.zeros((len(self.actual_names),len(names)))
        for name,i in self.indices.items():
            node=self.joints[name];mimic=node.find('mimic')
            if name in names:self.mapping[i,names.index(name)]=1.
            elif mimic is not None and mimic.get('joint')in names:self.mapping[i,names.index(mimic.get('joint'))]=float(mimic.get('multiplier','1'))
        def root(name):
            while name in self.incoming and self.incoming[name].get('type')=='fixed':name=self.incoming[name].find('parent').get('link')
            return name
        self.fixed={link.name:root(link.name)for link in self.robot.links};self.shapes=[];self.weights=[];self.groups=[]
        for link in self.robot.links:
            link.disable_gravity=True;self.shapes.extend(link.collision_shapes)
            for shape in link.collision_shapes:self.groups.append(dict(body=link.name,groups=shape.collision_groups))
            if link.collision_shapes:self.weights.append(self.reach(link.name,max(radius_of(s)for s in link.collision_shapes)))
        count=len(self.shapes);expected=len(self.xml.findall('.//collision'))
        if count!=expected:raise PlanningRejected('Continuous gate dropped URDF collision shapes')
        self.robot_shapes=count;self.ignored_pairs=[list(p)for p in loader.ignore_pairs]
        self.carried=None;self.carried_name='sweep:carried';self.allowed=set();self.supports=set();self.local_shapes=[];self.tools={};self.transforms={}
        carried_names=set()
        if attachment is not None:
            initial=np.asarray(attachment['object_pose'],float);self.initial=initial
            if 'holder'in attachment:
                side=attachment['holder'];self.tools={side:attachment['tool_frame']};self.transforms={side:np.asarray(attachment['tcp_object_transform'],float)};carried_names=set(attachment['shape_names'])
            else:
                self.tools=dict(attachment['tool_frames']);self.transforms={s:np.asarray(t,float)for s,t in attachment['tcp_object_transforms'].items()};carried_names={attachment['object_name']}
            self.primary=next(iter(self.tools));self.supports=set(attachment['support_names']);self.allowed={f'openarm_{s}_{finger}_finger'for s in self.tools for finger in ['left','right']}
            if attachment.get('handoff_receiver_fingers') is not None:
                # Test-fixture handoff (user-approved): the receiver's two fingers may touch the donor-held object,
                # exactly as the holder's do. Same declaration the planner attachment validates.
                receiver='right' if attachment.get('holder')=='left' else 'left'
                if 'holder' not in attachment or attachment['handoff_receiver_fingers']!=[f'openarm_{receiver}_{finger}_finger' for finger in ('left','right')]:
                    raise PlanningRejected('Continuous gate: handoff receiver fingers must be exactly the opposite arm finger links')
                self.allowed|=set(attachment['handoff_receiver_fingers'])
            all_names={c['name']for c in world}
            if not carried_names or not carried_names<=all_names or not self.supports<=all_names-carried_names:raise PlanningRejected('Continuous gate attachment geometry accounting mismatch')
            b=self.scene.create_actor_builder();outer=0.
            for c in world:
                if c['name']in carried_names:
                    local=np.linalg.inv(initial)@pose_of(c);self.local_shapes.append((local,np.asarray(c['half_size'],float)))
                    b.add_box_collision(half_size=c['half_size'],pose=sapien.Pose(local))
                    at_tcp=self.transforms[self.primary]@local;outer=max(outer,float(np.linalg.norm(at_tcp[:3,3])+np.linalg.norm(c['half_size'])))
            self.carried=b.build(self.carried_name);body=self.carried.find_component_by_type(sapien.physx.PhysxRigidDynamicComponent);body.disable_gravity=True
            # Monotone mode starts from a measured support overlap; PhysX would depenetrate
            # the query body. Lock it in place (still dynamic, so contacts are still generated).
            if execution_settings is not None and measured_start:body.set_locked_motion_axes([True]*6)
            self.carried_body=body;self.shapes.extend(body.collision_shapes);self.carried_weights=self.reach(self.tools[self.primary],outer+1e-7);self.weights.append(self.carried_weights)
            self.center_weights={s:self.reach(self.tools[s],float(np.linalg.norm(self.transforms[s][:3,3])))for s in self.tools}
            self.angle_weights={s:self.angle_reach(self.tools[s])for s in self.tools}
        self.static_corners={}
        for c in world:
            if c['name']in carried_names:continue
            actor_pose=pose_of(c);b=self.scene.create_actor_builder();b.add_box_collision(half_size=c['half_size']);actor=b.build_static('world:'+c['name']);actor.pose=sapien.Pose(actor_pose)
            self.shapes.extend(actor.find_component_by_type(sapien.physx.PhysxRigidBaseComponent).collision_shapes)
            self.static_corners['world:'+c['name']]=corners(c['half_size'])@actor_pose[:3,:3].T+actor_pose[:3,3]
        if self.carried is not None:
            # PhysX must not solve the contacts that the certificate already allows.
            # Give each allowed finger its own unused ignore bit, shared only with
            # the carried shapes. Distinct bits keep finger/finger contacts intact.
            fingers=[link for link in self.robot.links if link.name in self.allowed and link.collision_shapes]
            ids={shape.collision_groups[3]&0xffff for link in fingers for shape in link.collision_shapes}
            if len(ids)>1 or any(shape.collision_groups[2] for shape in self.carried_body.collision_shapes):
                raise PlanningRejected('Continuous gate cannot isolate allowed carried contacts')
            used=0
            for shape in self.shapes:used|=shape.collision_groups[2]
            bits=[1<<i for i in range(32) if not used&(1<<i)]
            if len(bits)<len(fingers):raise PlanningRejected('Continuous gate has no isolated collision groups')
            carried_bits=0
            for link,bit in zip(fingers,bits):
                carried_bits|=bit
                for shape in link.collision_shapes:
                    groups=list(shape.collision_groups);groups[2]|=bit;shape.set_collision_groups(groups)
            for shape in self.carried_body.collision_shapes:
                groups=list(shape.collision_groups);groups[2]=carried_bits
                if ids:groups[3]=(groups[3]&~0xffff)|next(iter(ids))
                shape.set_collision_groups(groups)
            self.groups=[dict(body=link.name,groups=shape.collision_groups)
                for link in self.robot.links for shape in link.collision_shapes]
        self.weights=np.array(self.weights);self.accounting=dict(declared_names=sorted(c['name']for c in world),carried_names=sorted(carried_names),static_names=sorted(c['name']for c in world if c['name']not in carried_names),bijective=True,no_shape_dropped=True)

    def ancestry(self,link):
        out=[]
        while link in self.incoming:
            node=self.incoming[link];out.append(node);link=node.find('parent').get('link')
        return out

    def reach(self,link,radius):
        out=np.zeros(len(self.actual_names))
        for node in self.ancestry(link):
            name=node.get('name');kind=node.get('type')
            if name in self.indices and kind in {'revolute','continuous','prismatic'}:out[self.indices[name]]=1. if kind=='prismatic'else radius
            if kind=='prismatic':
                limit=node.find('limit');radius+=max(abs(float(limit.get('lower'))),abs(float(limit.get('upper'))))
            origin=node.find('origin');radius+=0. if origin is None else float(np.linalg.norm(np.fromstring(origin.get('xyz','0 0 0'),sep=' ')))
        return out

    def angle_reach(self,link):
        out=np.zeros(len(self.actual_names))
        for node in self.ancestry(link):
            if node.get('name')in self.indices and node.get('type')in {'revolute','continuous'}:out[self.indices[node.get('name')]]=1.
        return out

    def full_state(self,q):
        values={**self.locks,**dict(zip(self.names,q))}
        for name,node in self.joints.items():
            mimic=node.find('mimic')
            if mimic is not None:values[name]=values.get(mimic.get('joint'),0.)*float(mimic.get('multiplier','1'))+float(mimic.get('offset','0'))
        return np.array([values.get(n,0.)for n in self.actual_names])

    def axes(self,q):
        values=dict(zip(self.actual_names,q));roots={l.get('name')for l in self.xml.findall('link')}-set(self.incoming);poses={n:np.eye(4)for n in roots};axes={};pending=list(self.joints.values())
        while pending:
            progress=False
            for node in pending[:]:
                parent=node.find('parent').get('link');child=node.find('child').get('link')
                if parent not in poses:continue
                origin=node.find('origin');t=np.eye(4)
                if origin is not None:
                    t[:3,3]=np.fromstring(origin.get('xyz','0 0 0'),sep=' ');t[:3,:3]=Rotation.from_euler('xyz',np.fromstring(origin.get('rpy','0 0 0'),sep=' ')).as_matrix()
                t=poses[parent]@t;name=node.get('name');kind=node.get('type');axis=node.find('axis');a=np.fromstring(axis.get('xyz','1 0 0')if axis is not None else'1 0 0',sep=' ')
                if kind!='fixed'and abs(np.linalg.norm(a)-1)>1e-6:raise PlanningRejected('Non-unit URDF joint axis')
                axes[name]=t[:3,:3]@a;d=np.eye(4)
                if kind in {'revolute','continuous'}:d[:3,:3]=Rotation.from_rotvec(a*values.get(name,0.)).as_matrix()
                elif kind=='prismatic':d[:3,3]=a*values.get(name,0.)
                poses[child]=t@d;pending.remove(node);progress=True
            if not progress:raise PlanningRejected('URDF ancestry has no complete root traversal')
        return axes

    def directional_bound(self,weights,delta,normal,axes):
        total=0.
        for name,index in self.indices.items():
            if weights[index]==0:continue
            node=self.joints[name];parent=node.find('parent').get('link');turn=float(self.angle_reach(parent)@delta)
            factor=abs(float(normal@axes[name]))if node.get('type')=='prismatic'else float(np.linalg.norm(np.cross(normal,axes[name])))
            total+=weights[index]*delta[index]*min(1.,factor+turn+1e-12)
        return total

    def certify(self,response,*,deadline=None,cancel_check=lambda:False):
        started=time.monotonic();deadline=float('inf') if deadline is None else deadline
        curve=CommandCurve(response);coeff=curve.coefficients
        controls=np.array([sum(coeff[:,i]*math.comb(k,i)/math.comb(5,i)for i in range(k+1))for k in range(6)]).transpose(1,0,2)
        stack=[(controls[i],float(curve.times[i]),float(curve.times[i+1]),0)for i in range(len(controls))][::-1]
        checks=leaves=depth_max=0;bound_max=step_max=0.;closure_max=[0.,0.];failure=None
        while stack:
            if cancel_check()or time.monotonic()>=deadline or checks>=MAX_QUERIES:
                failure=dict(reason='continuous_certificate_canceled_or_budget_exceeded',remaining_intervals=len(stack));break
            control,start,end,depth=stack.pop();left,right,mid=split(control);delta=np.max(abs(control-mid),axis=0)+1e-12;full_delta=abs(self.mapping)@delta;bound=float(np.max(self.weights@full_delta));threshold=CLEARANCE+2*bound+PADDING
            for shape in self.shapes:shape.contact_offset=threshold;shape.rest_offset=0.
            q=self.full_state(mid);self.robot.set_qpos(q);self.robot.set_qvel(np.zeros_like(q));self.robot.set_qf(np.zeros_like(q))
            poses={};closure_ok=True
            if self.carried is not None:
                poses={s:self.robot.find_link_by_name(t).entity_pose.to_transformation_matrix()@self.transforms[s]for s,t in self.tools.items()};pose=poses[self.primary]
                self.carried.pose=self.sapien.Pose(pose);self.carried_body.linear_velocity=np.zeros(3);self.carried_body.angular_velocity=np.zeros(3)
                if len(poses)==2:
                    a,b=poses.values();pe=float(np.linalg.norm(a[:3,3]-b[:3,3]));re=float(Rotation.from_matrix(a[:3,:3]@b[:3,:3].T).magnitude())
                    pbound=pe+sum(float(w@full_delta)for w in self.center_weights.values())+PADDING
                    rbound=re+sum(float(w@full_delta)for w in self.angle_weights.values())+2e-6
                    closure_ok=pbound<=.004 and rbound<=.03
                    if closure_ok:closure_max=[max(closure_max[0],pbound),max(closure_max[1],rbound)]
            self.scene.step();checks+=1
            step=float(np.max(self.weights@abs(np.asarray(self.robot.qpos)-q)));step_max=max(step_max,step)
            if self.carried is not None:
                moved=self.carried.pose.to_transformation_matrix();radius=max(float(np.linalg.norm(local[:3,3])+np.linalg.norm(half))for local,half in self.local_shapes)
                angle=float(Rotation.from_matrix(moved[:3,:3]@pose[:3,:3].T).magnitude());step=max(step,float(np.linalg.norm(moved[:3,3]-pose[:3,3]))+2*radius*np.sin(angle/2));step_max=max(step_max,step)
            if step>PADDING/4:
                failure=dict(reason='numerical_query_geometry_moved',point_displacement_m=step);break
            collisions=[];axes=None
            for contact in self.scene.get_contacts():
                bodies=[b.entity.name for b in contact.bodies]
                if len(bodies)==2 and all(b in self.fixed for b in bodies)and self.fixed[bodies[0]]==self.fixed[bodies[1]]:continue
                near=[p for p in contact.points if p.separation<threshold]
                if not near:continue
                if self.carried_name in bodies:
                    other=next(b for b in bodies if b!=self.carried_name)
                    if other in self.allowed:continue
                    if other in {'world:'+s for s in self.supports}:
                        if axes is None:axes=self.axes(q)
                        zbound=self.directional_bound(self.center_weights[self.primary],full_delta,np.array([0.,0.,1.]),axes)
                        departing=pose[2,3]-zbound-PADDING>=self.initial[2,3]-.0001
                        if departing:
                            object_points=np.concatenate([corners(half)@(pose@local)[:3,:3].T+(pose@local)[:3,3]for local,half in self.local_shapes]);support_points=self.static_corners[other];proved=False
                            for point in near:
                                normal=np.array(point.normal,float);normal/=np.linalg.norm(normal)
                                if normal@(pose[:3,3]-support_points.mean(axis=0))<0:normal=-normal
                                gap=float(np.min(object_points@normal)-np.max(support_points@normal));motion=self.directional_bound(self.carried_weights,full_delta,normal,axes)
                                if gap-motion-PADDING>=self.departure_floor(other,normal):proved=True;break
                            if proved:continue
                collisions.append(dict(bodies=bodies,separations=[float(p.separation)for p in near]))
            if not collisions and closure_ok:
                leaves+=1;depth_max=max(depth_max,depth);bound_max=max(bound_max,bound);continue
            if depth>=16:
                failure=dict(reason='continuous_geometry_or_closure_uncertified',interval=[start,end],point_bound_m=bound,contacts=collisions,closure_ok=closure_ok);break
            middle=(start+end)/2;stack.extend([(right,middle,end,depth+1),(left,start,middle,depth+1)])
        return dict(schema=SCHEMA,result='PASS'if failure is None else'未验证',input_sha256=input_sha256(response),joint_names=self.names,
            world_sha256=hashlib.sha256(json.dumps(self.world,sort_keys=True).encode()).hexdigest(),
            clearance_m=CLEARANCE,numerical_padding_m=PADDING,certified_intervals=leaves,queries=checks,max_depth=depth_max,
            maximum_certified_point_displacement_m=bound_max,maximum_query_step_point_displacement_m=step_max,
            robot_shapes=self.robot_shapes,world_shapes=len(self.world),geometry_accounting=self.accounting,
            closure=None if len(self.tools)!=2 else dict(max_translation_bound_m=closure_max[0],max_rotation_bound_rad=closure_max[1],position_limit_m=.004,rotation_limit_rad=.03),
            loader_ignored_pairs=self.ignored_pairs,collision_groups_sha256=hashlib.sha256(json.dumps(self.groups,sort_keys=True).encode()).hexdigest(),
            original_times_preserved=True,failure=failure,elapsed_seconds=time.monotonic()-started,
            **({'support_departure':{'mode':'monotone','start_gap_m':{k:min(v)for k,v in sorted(self.departure_start_gaps.items())}}}if self.departure_start_gaps else {}),
            method='Bernstein joint hulls; complete-URDF ancestral reach; PhysX inflated contact distance; nominal measured rigid attachments')

    def departure_floor(self,support,normal):
        """Historical 0.1 mm; explicit monotone mode keeps, never deepens, the measured start overlap."""
        from curobo_v2.execution_settings import departure_floor
        if (getattr(self,'execution_settings',None) is None or not self.measured_start
                or support not in {'world:'+s for s in self.supports}):return departure_floor()
        start=np.concatenate([corners(half)@(self.initial@local)[:3,:3].T+(self.initial@local)[:3,3]for local,half in self.local_shapes])
        gap0=float(np.min(start@normal)-np.max(self.static_corners[support]@normal))
        self.departure_start_gaps.setdefault(support,[]).append(gap0)
        return departure_floor(gap0)


def certify_trajectory(response,*,urdf,world,locks,attachment,position_limits,maximum_speed,max_acceleration,max_jerk,execution_settings=None,measured_start=True):
    started=time.monotonic();motion=dict(result='未验证');receipt=dict(schema=SCHEMA,result='未验证',input_sha256=input_sha256(response))
    try:
        curve=CommandCurve(response)
        motion=curve.certify_motion(position_limits=position_limits,maximum_speed=maximum_speed,max_acceleration=max_acceleration,max_jerk=max_jerk)
        receipt=SweepValidator(urdf,response['joint_names'],world,locks,attachment,
            **({} if execution_settings is None and measured_start else dict(execution_settings=execution_settings,measured_start=measured_start))).certify(response)
    except Exception as exc:
        receipt['failure']=dict(reason='service_continuous_validator_exception',error=f'{type(exc).__name__}: {exc}')
    if _evidence is not None:
        row=dict(**({} if execution_settings is None else {'execution_settings':execution_settings.to_dict()}),response=response,world=world,locked_joints=locks,attachment=attachment,certificate=receipt,motion=motion,urdf=str(urdf))
        path=_evidence/f'continuous-{time.time_ns()}-{receipt["input_sha256"][:12]}.json';path.write_text(json.dumps(row,allow_nan=False,indent=2)+'\n')
    return receipt,motion
