"""Authenticated official V2 planning from real MuJoCo state, with no fallback."""
import json
import os
from pathlib import Path
import numpy as np
import yaml
from scipy.spatial.transform import Rotation
from curobo_v2.identity import IdentityError,load_lock,verify_files,verify_client_imports
from curobo_v2.client import PlanningRejected
from curobo_v2.session import V2Service
from curobo_v2.execution_settings import from_config,request_fields,receipt_fields,goal_margin
from curobo_v2.trajectory import TrajectoryLedger
from curobo_v2.ik_path_jump import DEFAULT_MAX_JOINT_JUMP,diagnostic_max_joint_jump
from .environment import joint_address, mount_pose, tcp_pose
from .world import snapshot
from .planning_rejection import call_with_rejection_facts

def contact_exemption_mode(configured):
    if configured not in ('none','movable_objects'):
        raise ValueError('contact_exempt_objects must be none or movable_objects')
    override=os.environ.get('ATTN_DIAG_CONTACT_EXEMPT')
    if override is not None and override not in ('none','movable_objects'):
        raise ValueError('ATTN_DIAG_CONTACT_EXEMPT must be none or movable_objects')
    return override if override is not None else configured

class MujocoV2Planner:
    def __init__(self, env, *, evidence, deadline, cancel_check, contact_exempt_objects='none',
                 planner_mode='plan',ik_candidates=None,ik_max_joint_jump=None,execution_settings=None):
        self.execution_settings=None if execution_settings is None else from_config(execution_settings.to_dict())
        self.env=env;self.evidence=Path(evidence);self.evidence.mkdir(parents=True,exist_ok=True)
        self.planner_mode=planner_mode;self.ik_candidates=ik_candidates;self.ik_max_joint_jump=ik_max_joint_jump
        self.contact_exemption_configured=contact_exempt_objects
        self.contact_exemption_mode=contact_exemption_mode(contact_exempt_objects)
        lock_path=Path('/home/truares/Documents/Codex/2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-05/runtime-lock.json')
        trust={} if self.execution_settings is None else dict(tier='dev',boundary='dev_loop')
        expected_sha='0058f2d4d3d4a7d7894f53dcb99128c0db377cb216ab3cf747cb038f12b4e1a0'
        if self.execution_settings is not None:
            lock_path=Path(self.execution_settings.planner_deployment['lock_path'])
            expected_sha=self.execution_settings.planner_deployment['sha256']
        lock,sha=load_lock(lock_path,**trust)
        self.planner_lock_sha256=sha
        if sha!=expected_sha:
            raise IdentityError('OpenArm365 dual-arm deployment lock changed')
        verify_files(lock);verify_client_imports(lock)
        config=Path(lock['project_root'])/'config/openarm_fixed.yml'
        self.spec=yaml.safe_load(config.read_text());self.names=self.spec['service']['joint_names']
        self.locks=list(self.spec['robot_cfg']['kinematics']['lock_joints'])
        self.tools=self.spec['robot_cfg']['kinematics']['tool_frames']
        self.chassis_binding=self.spec['service']['native_chassis']
        self.addresses=[joint_address(env.sim,n) for n in self.names]
        self.lock_addresses={n:joint_address(env.sim,n) for n in self.locks}
        self.ledger=TrajectoryLedger();self.sequence=0;self.failed=False;self.prepared=set()
        self.attachment_provider=lambda:None
        self.owner=V2Service(lock_path=lock_path,robot_config=config,evidence=self.evidence,
            deadline=deadline,cancel_check=cancel_check,**trust,
            **({} if self.execution_settings is None else dict(execution_settings=self.execution_settings)))
        self.client=self.owner.__enter__()

    def state(self):return np.asarray(self.env.sim.data.qpos[self.addresses],float)
    def body(self, active):
        return dict(env_id='robocasa365',current_q=self.state().tolist(),joint_names=self.names,
            locked_joints={n:float(self.env.sim.data.qpos[a]) for n,a in self.lock_addresses.items()},active_joints=list(active),**request_fields(getattr(self,"execution_settings",None)))
    def guard(self, operation):
        if self.failed:raise PlanningRejected('Authenticated 365 planner latched; reset required')
        try:return operation()
        except BaseException:
            self.failed=True;self.ledger.invalidate('planner_failure');self.owner.close('planner_failure');raise
    def world_snapshot(self):
        if self.contact_exemption_mode=='none':
            return snapshot(self.env,chassis_binding=self.chassis_binding)
        from .world import movable_object_roots
        roots=movable_object_roots(self.env)
        held=self.attachment_provider()
        attached=held['target_id'] if held else getattr(self, '_heading_world_target', None)
        return snapshot(self.env,chassis_binding=self.chassis_binding,
            exempt_body_ids=set(roots.values()),attached_body_id=attached)
    def upload(self):
        world=self.world_snapshot();self.sequence+=1
        (self.evidence/f'world-{self.sequence:04d}.json').write_text(json.dumps(world,indent=2)+'\n')
        receipt=self.client.call('/world/cuboids',dict(env_id='robocasa365',cuboids=world['cuboids']))
        if (self.contact_exemption_mode!='none' or self.contact_exemption_configured!='none'
                or os.environ.get('ATTN_DIAG_CONTACT_EXEMPT') is not None):
            from .world import movable_object_roots
            roots=movable_object_roots(self.env)
            held=self.attachment_provider()
            audit=dict(mode=self.contact_exemption_mode,configured=self.contact_exemption_configured,
                source='ATTN_DIAG_CONTACT_EXEMPT' if os.environ.get('ATTN_DIAG_CONTACT_EXEMPT') is not None else 'config',
                objects=[{'name':name,'body_id':bid} for name,bid in roots.items()
                    if self.contact_exemption_mode!='none' and (not held or bid!=held['target_id'])],
                attached_object=next((name for name,bid in roots.items() if held and bid==held['target_id']),None),
                exempted_geom_count=world.get('exempted_geom_count',0),world_sha256=world['world_sha256'],
                world_sha256_unexempted=world.get('world_sha256_unexempted',world['world_sha256']))
            receipt={**receipt,'contact_exemption':audit}
        return world,receipt
    def prepare(self, active):
        def op():
            self.upload();body=self.body(active)
            self.client.call('/warmup',{k:v for k,v in body.items() if k!='env_id'})
            fk=self.client.call('/fk',body);errors={}
            for side in ('left','right'):
                tool=f'openarm_{side}_hand_tcp';got=fk['poses'][tool]
                actual=np.linalg.inv(mount_pose(self.env))@tcp_pose(self.env,side)
                rotation=Rotation.from_quat(np.array(got['quaternion_wxyz'])[[1,2,3,0]]).as_matrix()
                pe=float(np.linalg.norm(actual[:3,3]-got['position']))
                re=float(Rotation.from_matrix(rotation@actual[:3,:3].T).magnitude())
                errors[tool]=dict(position_error_m=pe,rotation_error_rad=re)
            (self.evidence/'alignment.json').write_text(json.dumps(errors,indent=2)+'\n')
            if any(e['position_error_m']>1e-5 or e['rotation_error_rad']>1e-5 for e in errors.values()):
                raise IdentityError('Actual 365 robot/TCP differs from authenticated V2 URDF')
            self.prepared.add(tuple(active));return errors
        return self.guard(op)
    def plan(self,path,extra,active,epoch,*,planner_settings=None):
        def op():
            settings=getattr(self,'execution_settings',None)
            if settings is not None and 'target_qpos' in extra:
                self.check_goal_margin(extra['target_qpos'],active)
            search_request=extra.get('grasp_search')
            self._heading_world_target=(self.env.obj_body_id[search_request['object_name']]
                                        if search_request is not None else None)
            if tuple(active) not in self.prepared:self.prepare(active)
            world,receipt=self.upload();body=self.body(active);body.update(extra)
            from .attachment import planner_context
            attachment=self.attachment_provider()
            retention=(attachment.get('support_retention') if attachment is not None
                       and getattr(settings, 'carried_proxy_support_departure', None)=='monotone' else None)
            signature=({k:attachment[k] for k in ('holder','target_id')} if retention is not None else attachment)
            body['attachment']=planner_context(self.env,signature,world,self.tools,self.evidence)
            if retention is not None:
                from .attachment import retain_declared_supports
                body['attachment']=retain_declared_supports(body['attachment'],retention,world['inventory'])
            if 'grasp_search' in body:
                request=body['grasp_search']
                public_name=request.pop('object_name')
                if attachment is not None:raise ValueError('grasp_search requires unattached object')
                # This is lookahead geometry, never an executor grasp signature.
                signature=dict(holder=request.pop('holder'),target_id=self.env.obj_body_id[public_name])
                request['attachment']=planner_context(self.env,signature,world,self.tools,self.evidence)

            warm={k:v for k,v in body.items() if k not in {'targets','target_qpos','ik_candidates','max_joint_jump','grasp_search'}}
            if 'targets' in body:warm['tool_frames']=list(body['targets'])
            self.client.call('/warmup',warm)
            # Legacy calls retain their exact path and evidence behavior.
            response=(self.client.call(path,body) if settings is None else
                call_with_rejection_facts(self.client,path,body,settings))
            if settings is not None:
                response['goal_margin']=self.check_goal_margin(response['trajectory'][-1],active)
                response.update(receipt_fields(settings))
            if path in {'/plan','/plan/ik_path'}:
                response['planner_mode']='ik_path' if path=='/plan/ik_path' else 'plan'
                response['planner_mode_source']='config'
            if path=='/plan/ik_path':
                response['ik_candidates']=body.get('ik_candidates',1)
                response['ik_candidates_source']='config' if 'ik_candidates' in body else 'default'
                response['max_joint_jump']=body.get('max_joint_jump',DEFAULT_MAX_JOINT_JUMP)
                response['max_joint_jump_source']='config' if 'max_joint_jump' in body else 'default'
            if planner_settings:response.update(planner_settings)
            if retention is not None:
                from copy import deepcopy
                response['support_retention']=deepcopy(body['attachment']['support_retention'])
            pid=self.ledger.issue(response,epoch=epoch,state=body['current_q'],world=world['cuboids'],
                identity=self.client.identity,attachment=attachment)
            response.update(plan_id=pid,epoch=epoch,world_receipt=receipt)
            if 'contact_exemption' in receipt:response['contact_exemption']=receipt['contact_exemption']
            (self.evidence/f'trajectory-{self.sequence:04d}.json').write_text(json.dumps(response,indent=2)+'\n')
            return response
        return self.guard(op)
    def check_goal_margin(self,target,active):
        import xml.etree.ElementTree as ET
        joints={j.get('name'):j for j in ET.parse(self.spec['robot_cfg']['kinematics']['urdf_path']).getroot().findall('joint')}
        limits=np.array([[float(joints[n].find('limit').get(b)) for b in ('lower','upper')] for n in self.names])
        margin=goal_margin(target,self.names,limits,active,execution_settings=self.execution_settings)
        if margin['minimum_joint_margin_rad']<margin['joint_margin_limit_rad']:
            raise PlanningRejected(f"Goal joint margin {margin['minimum_joint_margin_rad']:.9g} rad below {margin['joint_margin_limit_rad']:.9g} rad")
        return margin
    def plan_joints(self,target,*,side,epoch):
        active=self.names if side=='both' else [n for n in self.names if n.startswith(f'openarm_{side}_joint')]
        return self.plan('/plan/joint',dict(target_qpos=np.asarray(target,float).tolist()),active,epoch)
    def plan_pose(self,position,orientation,*,side,epoch,grasp_search=None):
        if side not in {'left','right'}:raise ValueError('Physical side required')
        local=np.linalg.inv(mount_pose(self.env));pos=(local@np.r_[position,1])[:3]
        rot=local[:3,:3]@orientation;quat=Rotation.from_matrix(rot).as_quat()[[3,0,1,2]].tolist()
        active=[f'openarm_{side}_joint{i}' for i in range(1,8)]
        mode=os.environ.get('ATTN_PLANNER_MODE',self.planner_mode)
        if mode not in {'plan','ik_path'}:
            raise ValueError('ATTN_PLANNER_MODE must be plan or ik_path')
        max_joint_jump=diagnostic_max_joint_jump(mode)
        settings=dict(planner_mode=mode,
            planner_mode_source='ATTN_PLANNER_MODE' if 'ATTN_PLANNER_MODE' in os.environ else 'config')
        extra=dict(targets={f'openarm_{side}_hand_tcp':dict(position=pos.tolist(),quaternion_wxyz=quat)})
        if grasp_search is not None:
            from curobo_v2.grasp_heading import check_sdk_request
            check_sdk_request(grasp_search, self.env.obj_body_id)
            if not getattr(getattr(self, 'execution_settings', None), 'grasp_heading_search', ()):
                raise ValueError('grasp_search requires r4-20261027')
            tool=f'openarm_{side}_hand_tcp'
            carry={stage+'_targets':{tool:dict(
                position=(local@np.r_[grasp_search[stage+'_position'],1])[:3].tolist(),
                quaternion_wxyz=quat)} for stage in ('lift','place')}
            extra['grasp_search']=dict(**carry, object_name=grasp_search['object_name'], holder=side,
                                       world_vertical=(local[:3,:3]@np.array([0.,0.,1.])).tolist())
        if mode=='ik_path':
            raw=os.environ.get('ATTN_IK_CANDIDATES')
            if raw is not None:
                if not raw.isdecimal() or int(raw)<1:
                    raise ValueError('ATTN_IK_CANDIDATES must be a positive integer')
                extra['ik_candidates']=int(raw)
            else:extra['ik_candidates']=self.ik_candidates if self.ik_candidates is not None else 1
            settings['ik_candidates_source']=('ATTN_IK_CANDIDATES' if raw is not None else
                'config' if self.ik_candidates is not None else 'default')
            if max_joint_jump is not None:settings['max_joint_jump_source']='override'
            else:max_joint_jump=self.ik_max_joint_jump
            if max_joint_jump is not None:extra['max_joint_jump']=max_joint_jump
        return self.plan('/plan' if mode=='plan' else '/plan/ik_path',extra,active,epoch,planner_settings=settings)
    def claim(self,pid,epoch):
        def op():
            self.client.attest()
            return self.ledger.claim(pid,epoch=epoch,state=self.state(),world=self.world_snapshot()['cuboids'],
                identity=self.client.identity,attachment=self.attachment_provider())
        return self.guard(op)
    def close(self):
        self.ledger.invalidate('close');return self.owner.close()
