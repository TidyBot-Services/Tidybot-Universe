#!/usr/bin/env python3
"""Standalone OpenArm Phase 0. No Panda, SDK, Runner, service, or benchmark imports."""
from pathlib import Path
import argparse, datetime, hashlib, importlib.metadata as md, json, os, sys, traceback, xml.etree.ElementTree as ET
import numpy as np
import sapien
from PIL import Image
import trimesh

ROOT=Path(__file__).resolve().parent
CFG=json.loads((ROOT/'acceptance_config.json').read_text())
TOL=CFG['tolerances']

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def convert(x):
 if isinstance(x,np.ndarray):return x.tolist()
 if isinstance(x,np.generic):return x.item()
 if isinstance(x,Path):return str(x)
 raise TypeError(type(x).__name__)
def write(path,obj):Path(path).write_text(json.dumps(obj,indent=2,default=convert,allow_nan=False))
def origin(node):
 t=np.eye(4)
 if node is None:return t
 xyz=np.fromstring(node.get('xyz','0 0 0'),sep=' ');rpy=np.fromstring(node.get('rpy','0 0 0'),sep=' ')
 from scipy.spatial.transform import Rotation
 t[:3,:3]=Rotation.from_euler('xyz',rpy).as_matrix();t[:3,3]=xyz
 return t

def fk(xml,qmap):
 poses={'world':np.eye(4)};pending=list(xml.findall('joint'))
 while pending:
  progressed=False
  for j in pending[:]:
   parent=j.find('parent').get('link');child=j.find('child').get('link')
   if parent not in poses:continue
   t=origin(j.find('origin'));m=np.eye(4);val=qmap.get(j.get('name'),0)
   if j.get('type')!='fixed':
    axis=np.fromstring(j.find('axis').get('xyz'),sep=' ')
    if j.get('type')=='prismatic':m[:3,3]=axis*val
    else:
     from scipy.spatial.transform import Rotation
     m[:3,:3]=Rotation.from_rotvec(axis*val).as_matrix()
   poses[child]=poses[parent]@t@m;pending.remove(j);progressed=True
  if not progressed:raise ValueError('disconnected URDF')
 return poses

class CheckBook:
 def __init__(self):self.rows=[]
 def check(self,name,passed,details=None):
  self.rows.append({'check':name,'passed':bool(passed),'details':details})
  if not passed:print('FAIL',name,json.dumps(details,default=convert)[:800],flush=True)
 def passed(self):return all(x['passed'] for x in self.rows)

class Rig:
 def __init__(self,urdf,render=False):
  self.urdf=Path(urdf);self.xml=ET.parse(urdf).getroot()
  p=CFG['physics']
  sapien.physx.set_scene_config(gravity=p['gravity'],enable_enhanced_determinism=True,cpu_workers=1)
  self.scene=sapien.Scene() if render else sapien.Scene([sapien.physx.PhysxCpuSystem()])
  self.scene.set_timestep(p['dt'])
  loader=self.scene.create_urdf_loader();loader.fix_root_link=True
  self.builder=loader.load_file_as_articulation_builder(str(urdf))
  self.extra_pairs=[sorted(p) for p in loader.ignore_pairs]
  lockpath=self.urdf.parent/'implementation_lock.json'
  if lockpath.exists():
   expected=sorted(sorted(x['links']) for x in json.loads(lockpath.read_text())['extra_disabled_pairs'])
   if sorted(self.extra_pairs)!=expected:raise ValueError('SRDF ignored or exceptions do not match implementation lock')
  self.mimics=self.builder.mimic_joint_records.copy()
  if not render:
   for b in self.builder.link_builders:b.visual_records=[]
  self.robot=self.builder.build(fix_root_link=True,build_mimic_joints=True)
  self.robot.set_solver_position_iterations(p['solver_position_iterations'])
  self.robot.set_solver_velocity_iterations(p['solver_velocity_iterations'])
  self.joints=self.robot.get_active_joints();self.names=[j.name for j in self.joints]
  self.idx={n:i for i,n in enumerate(self.names)}
  self.arm=np.array([i for i,n in enumerate(self.names) if '_joint' in n and 'finger' not in n],int)
  self.finger=np.array([i for i,n in enumerate(self.names) if 'finger' in n],int)
  self.qlimits=np.array(self.robot.get_qlimits(),float)
  self.jxml={j.get('name'):j for j in self.xml.findall('joint')}
  c=CFG['controller']
  for joint in self.joints:
   j=self.jxml[joint.name];effort=float(j.find('limit').get('effort'))
   joint.max_velocity=float(j.find('limit').get('velocity'))
   follower=j.find('mimic') is not None
   finger='finger' in joint.name
   joint.set_drive_properties(0 if follower else c['finger_stiffness' if finger else 'arm_stiffness'],0 if follower else c['finger_damping' if finger else 'arm_damping'],effort,mode=c['mode'])
  self.goal=self.make_q(CFG['initial']);self.command=self.goal.copy()
  self.robot.set_root_pose(sapien.Pose());self.robot.set_qpos(self.goal);self.robot.set_qvel(np.zeros(self.robot.dof))
  self.apply_drive()
 def make_q(self,values,base=None):
  q=np.zeros(self.robot.dof) if base is None else base.copy()
  for side in ['left','right']:
   if side in values:
    for i,v in enumerate(values[side],1):q[self.idx[f'openarm_{side}_joint{i}']]=v
   if side+'_gripper' in values:
    q[self.idx[f'openarm_{side}_finger_joint1']]=values[side+'_gripper']
  for m in self.mimics:q[self.idx[m.joint]]=m.multiplier*q[self.idx[m.mimic]]+m.offset
  if ((q<self.qlimits[:,0])|(q>self.qlimits[:,1])).any():raise ValueError('target outside limits')
  return q
 def apply_drive(self):
  for j,v in zip(self.joints,self.command):
   if self.jxml[j.name].find('mimic') is None:j.set_drive_target(float(v));j.set_drive_velocity_target(0)
 def step(self):
  c=CFG['controller'];slew=np.full(self.robot.dof,c['target_slew_arm_rad_s']*CFG['physics']['dt'])
  slew[self.finger]=c['target_slew_finger_m_s']*CFG['physics']['dt']
  self.command+=np.clip(self.goal-self.command,-slew,slew);self.apply_drive()
  self.robot.set_qf(self.robot.compute_passive_force(gravity=True,coriolis_and_centrifugal=True))
  self.scene.step()
 def close(self):
  self.scene.clear()

 def contacts(self):
  rows=[]
  for c in self.scene.physx_system.get_contacts():
   a,b=[x.entity.name for x in c.bodies]
   rows.append({'bodies':[a,b],'points':[{'position':p.position.tolist(),'normal':p.normal.tolist(),'separation_m':float(p.separation),'impulse_Ns':p.impulse.tolist()} for p in c.points]})
  return rows

def model_audit(rig,out):
 book=CheckBook();xml=rig.xml;links={n.get('name'):n for n in xml.findall('link')}
 joints=xml.findall('joint');mesh_rows=[];inertia_rows=[];axis_rows=[]
 def semantic(node):
  return [node.tag,dict(node.attrib),[semantic(c) for c in node]]
 official=ET.parse(ROOT/'model/official.urdf').getroot()
 book.check('official mechanical joint tree unchanged',list(map(semantic,joints))==list(map(semantic,official.findall('joint'))))
 expected_inertials=ET.fromstring(ET.tostring(official))
 lock=json.loads((rig.urdf.parent/'implementation_lock.json').read_text())
 for change in lock['inertia_changes']:
  node=expected_inertials.find("link[@name='"+change['link']+"']/inertial/inertia")
  book.check(change['link']+' original inertia matches declared before',dict(node.attrib)==change['before'])
  node.attrib=change['after']
  expected_inertials.find("link[@name='"+change['link']+"']/inertial/origin").attrib=change['origin_after']
 source=ET.parse(ROOT/'sources/mujoco-v1-before.xml').getroot()
 for change in lock['inertia_changes']:
  official_finger=source.find(".//body[@name='"+change['link']+"']/inertial")
  target=xml.find("link[@name='"+change['link']+"']/inertial")
  v=target.find('inertia').attrib;diag=np.fromstring(official_finger.get('diaginertia'),sep=' ')
  book.check(change['link']+' inertia matches locked official MuJoCo local frame',np.array_equal([float(v[k]) for k in ['ixx','iyy','izz']],diag) and all(float(v[k])==0 for k in ['ixy','ixz','iyz']) and official_finger.get('quat')=='1.0 0.0 0.0 0.0' and np.array_equal(np.fromstring(target.find('origin').get('xyz'),sep=' '),np.fromstring(official_finger.get('pos'),sep=' ')+np.array(change['geometry_frame_translation_m'])))
 book.check('all inertials unchanged except four source-backed inertial frame/tensor corrections',[(x.get('name'),semantic(x.find('inertial')) if x.find('inertial') is not None else None) for x in xml.findall('link')]==[(x.get('name'),semantic(x.find('inertial')) if x.find('inertial') is not None else None) for x in expected_inertials.findall('link')])
 original_geometry=ET.parse(ROOT/'model/compat_v4/robot.urdf').getroot()

 book.check('URDF links 26 joints 25',len(links)==26 and len(joints)==25)
 for side in ['left','right']:
  arm=[j for j in joints if j.get('name') in [f'openarm_{side}_joint{i}' for i in range(1,8)]]
  book.check(side+' seven revolute joints',len(arm)==7 and all(j.get('type')=='revolute' for j in arm))
  driver=rig.jxml[f'openarm_{side}_finger_joint1'];follower=rig.jxml[f'openarm_{side}_finger_joint2']
  book.check(side+' independent gripper and mimic',driver.get('type')=='prismatic' and driver.find('mimic') is None and follower.find('mimic').get('joint')==driver.get('name'))
 book.check('simulator dof 18 with 16 independent controls',rig.robot.dof==18 and len(rig.mimics)==2,{'loaded_dof':rig.robot.dof,'independent':rig.robot.dof-len(rig.mimics)})
 actual=[];exceptions=[]
 for l in rig.robot.links:
  lx=links[l.name];inert=lx.find('inertial')
  book.check(l.name+' finite loaded mass inertia',np.isfinite(l.mass) and np.isfinite(l.inertia).all() and np.isfinite(l.entity_pose.to_transformation_matrix()).all())
  if inert is not None:
   mass=float(inert.find('mass').get('value'));i=inert.find('inertia');v={k:float(val) for k,val in i.attrib.items()}
   matrix=np.array([[v['ixx'],v['ixy'],v['ixz']],[v['ixy'],v['iyy'],v['iyz']],[v['ixz'],v['iyz'],v['izz']]])
   eig=np.linalg.eigvalsh(matrix)
   book.check(l.name+' SPD inertia',np.isfinite(matrix).all() and (eig>0).all() and mass>0)
   book.check(l.name+' loaded mass and principal inertia',np.isclose(l.mass,mass,rtol=TOL['mass_relative']) and np.allclose(np.sort(l.inertia),eig,rtol=TOL['inertia_relative'],atol=0))
   triangle_ok=bool(eig[-1]<=eig[0]+eig[1])
   inertia_rows.append({'link':l.name,'mass_kg':mass,'matrix_kg_m2':matrix,'principal_kg_m2':eig,'rigid_body_triangle_inequality':triangle_ok,'triangle_excess_kg_m2':max(0,float(eig[-1]-eig[0]-eig[1]))})
   # Exact physical plausibility check; a positive matrix alone is insufficient.
   book.check(l.name+' rigid body inertia triangle inequality',triangle_ok,{'principal':eig})
  cols=lx.findall('collision');book.check(l.name+' all collision shapes present',len(l.collision_shapes)==len(cols),{'expected':len(cols),'actual':len(l.collision_shapes)})
  reference=original_geometry.find("link[@name='"+l.name+"']")
  reference_cols=reference.findall('collision')
  if reference_cols:
   col=reference_cols[0];m=col.find('geometry/mesh');path=ROOT/'model/compat_v4'/m.get('filename');scale=np.fromstring(m.get('scale','1 1 1'),sep=' ')
   mesh=trimesh.load(path,force='mesh',process=False);expected=np.asarray(mesh.vertices)*scale
   got=np.concatenate([np.asarray(shape.vertices)*np.asarray(shape.scale) for shape in l.collision_shapes])
   bbox_error=float(np.max(np.abs(np.array([expected.min(0),expected.max(0)])-np.array([got.min(0),got.max(0)]))))
   book.check(l.name+' collision unit mirror bbox',bbox_error<=TOL['mesh_bbox_abs_m'] and np.isfinite(got).all(),{'error_m':bbox_error,'original_envelope_source':str(path)})
   book.check(l.name+' collision local origins preserved',all(semantic(c.find('origin'))==semantic(col.find('origin')) for c in cols))
   mesh_rows.append({'link':l.name,'kind':'collision','path':str(path),'scale':scale,'bbox_m':[expected.min(0),expected.max(0)],'simulator_bbox_m':[got.min(0),got.max(0)],'bbox_error_m':bbox_error,'groups':[shape.get_collision_groups() for shape in l.collision_shapes],'convex_hull':True,'shape_count':len(cols)})
  rc=l.entity.find_component_by_type(sapien.render.RenderBodyComponent)
  if rc:
   book.check(l.name+' render geometry present',len(rc.render_shapes)>0)
   for s in rc.render_shapes:
    vertices=np.concatenate([part.vertices for part in s.parts])
    book.check(l.name+' finite visual vertices',np.isfinite(vertices).all())
   visual_box=rc.compute_global_aabb_tight()
   book.check(l.name+' visual AABB valid',np.isfinite(visual_box).all() and (visual_box[1]>=visual_box[0]).all())
   mesh_rows.append({'link':l.name,'kind':'visual','world_aabb_m':visual_box})
  actual.append({'name':l.name,'mass_kg':l.mass,'principal_inertia_kg_m2':l.inertia,'cmass_local_pose':l.cmass_local_pose.to_transformation_matrix(),'collision_shapes':len(l.collision_shapes)})
 for j in joints:
  exceptions.append([j.find('parent').get('link'),j.find('child').get('link')])
  if j.get('type')!='fixed':
   axis=np.fromstring(j.find('axis').get('xyz'),sep=' ')
   book.check(j.get('name')+' finite unit axis',np.isfinite(axis).all() and abs(np.linalg.norm(axis)-1)<=TOL['axis_norm_abs'])
   actual_joint=rig.robot.find_joint_by_name(j.get('name'));expected_parent=origin(j.find('origin'))[:3,:3]@axis
   actual_child_axis=actual_joint.pose_in_child.to_transformation_matrix()[:3,0];actual_parent_axis=actual_joint.pose_in_parent.to_transformation_matrix()[:3,0]
   axis_error=max(float(np.max(abs(actual_child_axis-axis))),float(np.max(abs(actual_parent_axis-expected_parent))))
   book.check(j.get('name')+' simulator axes match official',axis_error<=TOL['axis_norm_abs'],axis_error)
   limit=j.find('limit');actual_limit=rig.qlimits[rig.idx[j.get('name')]];expected_limit=np.array([float(limit.get('lower')),float(limit.get('upper'))]);limit_error=float(np.max(abs(actual_limit-expected_limit)))
   book.check(j.get('name')+' simulator joint limits match official',limit_error<=TOL['arm_limit_slack_rad'] if j.get('type')=='revolute' else limit_error<=TOL['finger_limit_slack_m'],limit_error)
   axis_rows.append({'joint':j.get('name'),'official_axis':axis,'actual_axis_child':actual_child_axis,'actual_axis_parent':actual_parent_axis,'axis_error_abs':axis_error,'official_limits':expected_limit,'actual_limits':actual_limit,'limit_error':limit_error})
 poses=fk(xml,dict(zip(rig.names,rig.robot.qpos)))
 for l in rig.robot.links:
  matrix=l.entity_pose.to_transformation_matrix();poserr=float(np.max(abs(matrix[:3,3]-poses[l.name][:3,3])));roterr=float(np.max(abs(matrix[:3,:3]-poses[l.name][:3,:3])))
  book.check(l.name+' URDF axes FK agreement',poserr<=TOL['fk_position_m'] and roterr<=TOL['fk_rotation_matrix_abs'],{'position_m':poserr,'rotation_abs':roterr})
 for node in xml.iter('mesh'):
  path=rig.urdf.parent/node.get('filename');book.check('resource '+node.get('filename'),path.is_file() and path.stat().st_size>0)
  if path.suffix=='.dae':
   d=ET.parse(path).getroot();arrays=d.findall('.//{*}float_array');unit=d.find('.//{*}unit')
   book.check('DAE finite '+path.name,all(np.isfinite(np.fromstring(a.text,sep=' ')).all() for a in arrays))
   # URDF explicitly scales the CAD data from mm; record Collada metadata as well.
   mesh_rows.append({'kind':'DAE metadata','file':str(path),'unit':unit.attrib if unit is not None else None})
 result={'passed':book.passed(),'checks':book.rows,'simulator_joints':[{'name':j.name,'dof':j.dof,'type':j.type} for j in rig.robot.joints],'simulator_axes_and_limits':axis_rows,'simulator_active_order':rig.names,'loaded_dof':rig.robot.dof,'independent_controls':16,'mimic_records':[vars(m) for m in rig.mimics],'mimic_implementation':'SAPIEN native fixed tendons; followers have zero drive stiffness/damping','links':actual,'meshes':mesh_rows,'inertias':inertia_rows,'automatic_parent_child_exceptions':exceptions,'extra_disabled_pairs':rig.extra_pairs,'no_global_self_collision_filter':True,'zero_geometry_links':['world','openarm_left_hand_tcp','openarm_right_hand_tcp'],'massless_marker_runtime_epsilon':'SAPIEN builder assigns 1e-6 kg/inertia to marker links; fixed base/tcp; official geometry mass unchanged'}
 write(out/'model_audit.json',result);print('MODEL',result['passed'],flush=True);return result

def control(urdf,out):
 book=CheckBook();finals=[];resets=[]
 for reset in range(CFG['test_counts']['independent_scene_resets']):
  rig=Rig(urdf); qlogs=[];vlogs=[];clogs=[];stages=[];stepid=0
  try:
   for a in CFG['actions']:
    old=rig.goal.copy();rig.goal=rig.make_q(a,old);unchanged=np.where(abs(rig.goal-old)<1e-12)[0]
    held_error=np.zeros(rig.robot.dof);mimic_error=0.;limit_violation=np.zeros(rig.robot.dof);rooterr=0.;finite=True
    max_command_delta=np.zeros(rig.robot.dof)
    for _ in range(a['steps']):
     prev=rig.command.copy();rig.step();q=rig.robot.qpos.copy();v=rig.robot.qvel.copy();
     finite=finite and np.isfinite(q).all() and np.isfinite(v).all() and np.isfinite(rig.robot.qf).all()
     held_error[unchanged]=np.maximum(held_error[unchanged],abs(q[unchanged]-old[unchanged]))
     limit_violation=np.maximum(limit_violation,np.maximum(rig.qlimits[:,0]-q,q-rig.qlimits[:,1]))
     max_command_delta=np.maximum(max_command_delta,abs(rig.command-prev))
     for m in rig.mimics:mimic_error=max(mimic_error,abs(float(q[rig.idx[m.joint]]-m.multiplier*q[rig.idx[m.mimic]]-m.offset)))
     rooterr=max(rooterr,float(np.max(abs(rig.robot.root_pose.to_transformation_matrix()-np.eye(4)))))
     qlogs.append(q);vlogs.append(v);clogs.append(rig.command.copy())
     contacts=rig.contacts()
     if contacts:
      with (out/f'control_contacts_{reset}.jsonl').open('a') as f:f.write(json.dumps({'step':stepid,'stage':a['name'],'contacts':contacts})+'\n')
     stepid+=1
    err=abs(q-rig.goal);prefix=f'reset {reset} {a["name"]}'
    book.check(prefix+' finite',finite)
    book.check(prefix+' convergence arm',np.max(err[rig.arm])<=TOL['arm_position_rad'],float(np.max(err[rig.arm])))
    book.check(prefix+' convergence fingers',np.max(err[rig.finger])<=TOL['finger_position_m'],float(np.max(err[rig.finger])))
    book.check(prefix+' final velocity arm',np.max(abs(v[rig.arm]))<=TOL['arm_final_velocity_rad_s'],float(np.max(abs(v[rig.arm]))))
    book.check(prefix+' final velocity fingers',np.max(abs(v[rig.finger]))<=TOL['finger_final_velocity_m_s'],float(np.max(abs(v[rig.finger]))))
    heldarms=np.intersect1d(unchanged,rig.arm);heldfingers=np.intersect1d(unchanged,rig.finger)
    book.check(prefix+' held arm',not len(heldarms) or np.max(held_error[heldarms])<=TOL['held_arm_position_rad'],float(np.max(held_error[heldarms])) if len(heldarms) else 0.)
    book.check(prefix+' held gripper',not len(heldfingers) or np.max(held_error[heldfingers])<=TOL['held_finger_position_m'],float(np.max(held_error[heldfingers])) if len(heldfingers) else 0.)
    book.check(prefix+' mimic',mimic_error<=TOL['mimic_error_m'],mimic_error)
    book.check(prefix+' arm limits',np.max(limit_violation[rig.arm])<=TOL['arm_limit_slack_rad'],float(np.max(limit_violation[rig.arm])))
    book.check(prefix+' finger limits',np.max(limit_violation[rig.finger])<=TOL['finger_limit_slack_m'],float(np.max(limit_violation[rig.finger])))
    book.check(prefix+' fixed root',rooterr<=TOL['root_pose_abs'],rooterr)
    slew=np.full(rig.robot.dof,CFG['controller']['target_slew_arm_rad_s']*CFG['physics']['dt']);slew[rig.finger]=CFG['controller']['target_slew_finger_m_s']*CFG['physics']['dt']
    book.check(prefix+' bounded drive commands',np.all(max_command_delta<=slew+1e-10))
    stages.append({'name':a['name'],'physical_steps':a['steps'],'target':rig.goal,'qpos':q,'qvel':v,'error':err,'held_error_max':held_error,'mimic_error_max_m':mimic_error,'limit_violation':limit_violation,'root_error':rooterr})
    print(prefix,'armerr',float(np.max(err[rig.arm])),'griperr',float(np.max(err[rig.finger])),flush=True)
   np.savez_compressed(out/f'trajectory_{reset}.npz',qpos=qlogs,qvel=vlogs,drive_target=clogs,joint_names=rig.names,dt=CFG['physics']['dt'])
   finals.append(np.array([s['qpos'] for s in stages]));resets.append({'reset':reset,'physical_steps':stepid,'stages':stages})
  finally:rig.close()
 for i in range(1,len(finals)):
  diff=abs(finals[i]-finals[0]);armerr=float(np.max(diff[:,rig.arm]));fingererr=float(np.max(diff[:,rig.finger]))
  book.check(f'repeat reset {i} arm',armerr<=TOL['repeat_arm_position_rad'],armerr)
  book.check(f'repeat reset {i} fingers',fingererr<=TOL['repeat_finger_position_m'],fingererr)
 result={'passed':book.passed(),'checks':book.rows,'resets':resets,'total_physical_steps':sum(x['physical_steps'] for x in resets),'joint_order':rig.names}
 write(out/'control.json',result);return result

def colliding(rows,predicate):
 return [x for x in rows if predicate(*x['bodies']) and any(p['separation_m']<0 and np.linalg.norm(p['impulse_Ns'])>0 for p in x['points'])]

def collisions(urdf,out):
 book=CheckBook();rig=Rig(urdf);scene=rig.scene
 try:
  # Isolated mechanism negative control; no robot collision filter changes.
  s=sapien.Scene([sapien.physx.PhysxCpuSystem()]);s.set_timestep(CFG['physics']['dt'])
  b=s.create_actor_builder();b.add_box_collision(half_size=[.1,.1,.1]);one=b.build_static('control_static')
  b=s.create_actor_builder();b.add_box_collision(half_size=[.1,.1,.1]);two=b.build('control_dynamic');two.pose=sapien.Pose([.5,0,0]);s.step()
  separated=s.physx_system.get_contacts();book.check('separated-box contact-free control',len(separated)==0)
  two.pose=sapien.Pose([.18,0,0]);two.find_component_by_type(sapien.physx.PhysxRigidDynamicComponent).set_linear_velocity([0,0,0]);s.step()
  overlap=[{'bodies':[x.entity.name for x in c.bodies],'points':[{'separation_m':float(p.separation),'impulse_Ns':p.impulse.tolist(),'position':p.position.tolist(),'normal':p.normal.tolist()} for p in c.points]} for c in s.physx_system.get_contacts()]
  book.check('known-box-overlap detected with impulse',bool(colliding(overlap,lambda a,b:True)),overlap);s.clear()
  probes=[]
  for side in ['left','right']:
   link=rig.robot.find_link_by_name(f'openarm_{side}_link3');shape=link.collision_shapes[0];vertices=np.array(shape.vertices)*np.array(shape.scale)
   local=np.mean(vertices,axis=0);pose=link.entity_pose*shape.local_pose;pos=pose.to_transformation_matrix()[:3,:3]@local+pose.p
   b=scene.create_actor_builder();b.add_box_collision(half_size=[.025,.025,.025]);probe=b.build_static('environment_probe_'+side);probe.pose=sapien.Pose(pos)
   rig.step();rows=rig.contacts();hit=colliding(rows,lambda a,b:'environment_probe_'+side in [a,b] and any(x.startswith('openarm_'+side) for x in [a,b]))
   book.check(side+' robot-environment overlap detectable',bool(hit),hit)
   probes.append({'side':side,'probe_position':pos,'all_contacts':rows});scene.remove_entity(probe)
   rig.robot.set_qpos(rig.make_q(CFG['initial']));rig.robot.set_qvel(np.zeros(rig.robot.dof))
  rng=np.random.default_rng(CFG['test_counts']['seed']);samples=[];hits=[]
  for i in range(CFG['test_counts']['collision_scene_samples']):
   if i==0:q=rig.make_q(CFG['initial'])
   elif i==1:q=np.clip(np.zeros(rig.robot.dof),rig.qlimits[:,0],rig.qlimits[:,1]);q[rig.finger]=.02
   else:
    q=rng.uniform(rig.qlimits[:,0],rig.qlimits[:,1]);q[rig.finger]=.02
    for m in rig.mimics:q[rig.idx[m.joint]]=q[rig.idx[m.mimic]]
   rig.robot.set_qpos(q);rig.robot.set_qvel(np.zeros(rig.robot.dof));rig.command=q.copy();rig.goal=q.copy();rig.apply_drive();rig.step()
   rows=rig.contacts();cross=colliding(rows,lambda a,b:(a.startswith('openarm_left_') and b.startswith('openarm_right_')) or (b.startswith('openarm_left_') and a.startswith('openarm_right_')))
   samples.append({'index':i,'qpos_before':q,'qpos_after':rig.robot.qpos.copy(),'contact_pairs':[x['bodies'] for x in rows],'cross_arm_impulse_contacts':len(cross)})
   if cross:
    hits.append({'index':i,'qpos_before':q,'qpos_after':rig.robot.qpos.copy(),'contacts':cross})
  book.check('same-articulation cross-arm physical collision detected',bool(hits),{'hit_configurations':len(hits)})
  groups=[s.get_collision_groups() for l in rig.robot.links for s in l.collision_shapes]
  filtered=[]
  shapes=[(l.name,s) for l in rig.robot.links for s in l.collision_shapes]
  for i,(a,sa) in enumerate(shapes):
   for b,sb in shapes[i+1:]:
    ga=sa.get_collision_groups();gb=sb.get_collision_groups()
    if a!=b and ga[2]&gb[2] and (ga[3]&0xffff)==(gb[3]&0xffff):filtered.append(sorted([a,b]))
  filtered=[list(x) for x in sorted(set(tuple(x) for x in filtered))]
  expected=sorted(rig.extra_pairs)
  book.check('only documented local exception masks',sorted(filtered)==expected,{'actual':filtered,'expected':expected})
  book.check('all cross-arm shape pairs enabled',all(not (a.startswith('openarm_left_') and b.startswith('openarm_right_')) for a,b in filtered),filtered)
  result={'passed':book.passed(),'checks':book.rows,'separated_control_count':len(separated),'known_overlap':overlap,'robot_environment_probes':probes,'cross_arm_hits':hits,'sample_count':len(samples),'joint_order':rig.names,'collision_groups':groups,'masked_pairs':filtered,'note':'qpos injection is limited to isolated collision test; these are intentional penetrations, not safe trajectories or Safety validation'}
  write(out/'collision_samples.json',samples);write(out/'collision.json',result);print('COLLISION',result['passed'],len(hits),'hits',flush=True);return result
 finally:rig.close()

def camera_test(rig,out):
 book=CheckBook();scene=rig.scene;c=CFG['camera'];scene.set_ambient_light([.6,.6,.6]);scene.add_directional_light([1,1,-2],[1,1,1],shadow=True);scene.add_ground(0)
 b=scene.create_actor_builder();b.add_box_visual(half_size=c['calibration_box_half_size'],material=[.9,.15,.05]);b.add_box_collision(half_size=c['calibration_box_half_size']);box=b.build_static('calibration');box.pose=sapien.Pose(c['calibration_box_center'])
 cam=scene.add_camera('phase0_camera',c['width'],c['height'],c['fovy_rad'],c['near_m'],c['far_m'])
 pos=np.array(c['position']);forward=np.array(c['look_at'])-pos;forward/=np.linalg.norm(forward);left=np.cross([0,0,1],forward);left/=np.linalg.norm(left);up=np.cross(forward,left)
 matrix=np.eye(4);matrix[:3,:3]=np.stack([forward,left,up],axis=1);matrix[:3,3]=pos;cam.entity.pose=sapien.Pose(matrix)
 rows=[]
 for frame in range(CFG['test_counts']['camera_frames']):
  rig.step();scene.update_render();cam.take_picture()
  rgb=np.array(cam.get_picture('Color'));position=np.array(cam.get_picture('Position'));seg=np.array(cam.get_picture('Segmentation'));valid=(position[...,3]<1)&np.isfinite(position).all(-1);depth=-position[...,2];metric=np.where(valid,depth,0).astype(np.float32)
  K=cam.get_intrinsic_matrix();M=cam.get_model_matrix();E=cam.get_extrinsic_matrix();Wopt=M@np.diag([1,-1,-1,1])
  prefix=f'frame {frame}'
  book.check(prefix+' RGB shape',rgb.shape==(c['height'],c['width'],4))
  book.check(prefix+' RGB finite normalized',np.isfinite(rgb).all() and rgb.min()>=0 and rgb.max()<=1 and np.std(rgb[...,:3])>0)
  book.check(prefix+' metric depth dimensions',metric.shape==(c['height'],c['width']))
  book.check(prefix+' valid finite depth',np.isfinite(metric).all() and np.mean(valid)>=c['min_foreground_fraction'] and (depth[valid]>c['near_m']).all() and (depth[valid]<c['far_m']).all())
  expected_f=c['height']/(2*np.tan(c['fovy_rad']/2));book.check(prefix+' intrinsics calibrated',np.allclose(K,[[expected_f,0,c['width']/2],[0,expected_f,c['height']/2],[0,0,1]],atol=.001))
  book.check(prefix+' pose finite consistent optical/OpenGL',np.isfinite(M).all() and np.isfinite(E).all() and np.allclose(M[:3,3],pos,atol=1e-6) and np.allclose(E@Wopt,np.eye(4)[:3],atol=1e-5))
  known=[]
  # Five face points with known metric world coordinates, independent of depth output.
  for y,z in [(0,1.0),(-.06,1.0),(.06,1.0),(0,.94),(0,1.06)]:
   point=np.array([c['calibration_box_center'][0]+c['calibration_box_half_size'][0],y,z]);opt=E@np.r_[point,1];uv=K@opt;uv=uv[:2]/uv[2];u,v=np.rint(uv).astype(int)
   inside=0<=u<c['width'] and 0<=v<c['height'];observed=position[v,u,:3] if inside else np.zeros(3);world=M[:3,:3]@observed+M[:3,3];projected=K@(E@np.r_[world,1]);projected=projected[:2]/projected[2]
   derr=abs(float(metric[v,u]-opt[2])) if inside else 1e9;werr=float(np.linalg.norm(world-point));perr=float(np.linalg.norm(projected-[u,v]))
   book.check(prefix+' known point depth '+str((y,z)),inside and valid[v,u] and derr<=TOL['camera_depth_m'],derr)
   book.check(prefix+' known point world '+str((y,z)),werr<=TOL['camera_world_point_m'],werr)
   book.check(prefix+' pixel reprojection '+str((y,z)),perr<=TOL['camera_pixel_reprojection_px'],perr)
   known.append({'point_world_m':point,'projected_uv':uv,'pixel':[u,v],'expected_optical_depth_m':opt[2],'observed_depth_m':float(metric[v,u]) if inside else 0,'reconstructed_world_m':world,'depth_error_m':derr,'world_error_m':werr,'reprojection_px':perr})
  np.savez_compressed(out/f'camera_{frame}.npz',rgb=rgb[...,:3],depth_m=metric,valid_depth=valid,position_opengl_m=position,segmentation=seg,K=K,T_world_opengl=M,T_world_optical=Wopt,T_world_sapien=matrix,extrinsic_world_optical=E)
  Image.fromarray(np.clip(rgb[...,:3]*255,0,255).astype(np.uint8)).save(out/f'rgb_{frame}.png')
  Image.fromarray((np.clip(metric/c['far_m'],0,1)*255).astype(np.uint8)).save(out/f'depth_{frame}.png')
  rows.append({'frame':frame,'rgb_min':float(rgb.min()),'rgb_max':float(rgb.max()),'valid_fraction':float(np.mean(valid)),'depth_min_m':float(depth[valid].min()),'depth_max_m':float(depth[valid].max()),'K':K,'T_world_sapien':matrix,'T_world_opengl':M,'T_world_optical':Wopt,'E_world_optical':E,'known_points':known})
 result={'passed':book.passed(),'checks':book.rows,'frames':rows,'render_device_summary':sapien.render.get_device_summary(),'depth_units':'meters, optical Z (not Euclidean range)','coordinates':c['depth']}
 write(out/'camera.json',result);print('CAMERA',result['passed'],flush=True);return result

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--out',required=True);parser.add_argument('--model',default=str(ROOT/'model/compat_v6/robot.urdf'));parser.add_argument('--only',choices=['all','model','control','collision','camera'],default='all');args=parser.parse_args();out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=False)
 configsha=sha(ROOT/'acceptance_config.json');expected=(ROOT/'acceptance_config.sha256').read_text().split()[0]
 if configsha!=expected:raise ValueError('frozen config SHA changed')
 for p,v in CFG['dependencies'].items():
  if md.version(p)!=v:raise ValueError('dependency mismatch '+p)
 # Protect every official resource before each run.
 if sha(ROOT/'model/official.urdf')!=CFG['model']['official_sha256']:raise ValueError('official URDF modified')
 for f in CFG['model']['mesh_files'].values():
  if sha(ROOT/f['relative_path'])!=f['sha256']:raise ValueError('official mesh modified')
 lockpath=Path(args.model).parent/'implementation_lock.json'
 lock=json.loads(lockpath.read_text()) if lockpath.exists() else None
 if lock:
  for rel,digest in lock.get('mesh_sha256',{}).items():
   if sha(Path(args.model).parent/rel)!=digest:raise ValueError('compat mesh lock changed: '+rel)
  if sha(args.model)!=lock['urdf_sha256'] or sha(Path(args.model).with_suffix('.srdf'))!=lock['srdf_sha256']:raise ValueError('implementation model lock changed')
 summary={'implementation_lock':lock,'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'config_sha256':configsha,'script_sha256':sha(__file__),'model_sha256':sha(args.model),'model':str(Path(args.model).resolve()),'dependencies':CFG['dependencies'],'checks':{},'scope':'OpenArm standalone SAPIEN feasibility only; no AttentionBench dual-arm support claim'}
 write(out/'run_manifest.json',summary)
 for category in ['model','control','collision','camera']:
  if args.only!='all' and args.only!=category:continue
  try:
   if category in ['model','camera']:
    rig=Rig(args.model,render=True)
    try:r=model_audit(rig,out) if category=='model' else camera_test(rig,out)
    finally:rig.close()
   elif category=='control':r=control(args.model,out)
   else:r=collisions(args.model,out)
   summary['checks'][category]={'status':'passed' if r['passed'] else 'failed','failed_checks':[x for x in r['checks'] if not x['passed']]}
  except Exception as e:
   traceback.print_exc();summary['checks'][category]={'status':'error','error':str(e),'traceback':traceback.format_exc()}
  write(out/'result.json',summary)
 summary['finished_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat();summary['passed']=len(summary['checks'])==4 and all(x['status']=='passed' for x in summary['checks'].values());write(out/'result.json',summary)
 print(json.dumps({k:v['status'] for k,v in summary['checks'].items()}),flush=True)
 return 0 if summary['passed'] else 1
if __name__=='__main__':sys.exit(main())
