#!/usr/bin/env python3
"""Local OpenArm HTTP service. Single SAPIEN owner, stdlib HTTP, no benchmark imports."""
from pathlib import Path
import argparse,collections,datetime,hashlib,importlib.util,io,json,math,os,queue,signal,sys,threading,time,traceback,urllib.parse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import numpy as np
import sapien
ROOT=Path(__file__).resolve().parent
CFG=json.loads((ROOT/'acceptance_config.json').read_text())
P0=ROOT.parent/'openarm_phase0_recheck'
spec=importlib.util.spec_from_file_location('openarm_phase0_validated',P0/'validate.py');sim=importlib.util.module_from_spec(spec);spec.loader.exec_module(sim)

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(x):return json.dumps(x,default=sim.convert,allow_nan=False,separators=(',',':')).encode()
class APIError(Exception):
 def __init__(self,status,code,message):super().__init__(message);self.status=status;self.code=code

def finite_number(v):return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
class Request:
 def __init__(self,method,path,body,epoch):self.method=method;self.path=path;self.body=body;self.epoch=epoch;self.event=threading.Event();self.response=None;self.canceled=False

class Runtime:
 def __init__(self,evidence,fault_after_steps=None):
  self.evidence=evidence;self.fault_after_steps=fault_after_steps;self.lock=threading.RLock();self.queue=queue.PriorityQueue();self.sequence=0;self.epoch=0;self.total_steps=0;self.reset_steps=0;self.ready=False;self.error=None;self.closing=threading.Event();self.actions={};self.active=None;self.rig=None;self.qlog=[];self.vlog=[];self.clog=[];self.glog=[];self.elog=[];self.action_log=[];self.times=[]
  self.thread=threading.Thread(target=self.run,name='openarm-simulation',daemon=False)
 def log(self,event,**details):
  with (self.evidence/'events.jsonl').open('a') as f:f.write(dump({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'event':event,'pid':os.getpid(),'epoch':self.epoch,'total_steps':self.total_steps,**details}).decode()+'\n')
 def health(self):
  with self.lock:return {'ready':self.ready,'error':self.error,'api_version':CFG['api_version'],'model_version':CFG['model_version'],'model_sha256':CFG['model_urdf_sha256'],'loaded_dof':getattr(self.rig.robot,'dof',None) if self.rig else None,'independent_controls':16 if self.ready else None,'pid':os.getpid(),'epoch':self.epoch,'total_steps':self.total_steps,'reset_steps':self.reset_steps}
 def submit(self,method,path,body):
  with self.lock:
   if not self.ready:raise APIError(503,'not_ready',self.error or 'Simulation starting')
   r=Request(method,path,body,self.epoch);self.sequence+=1;self.queue.put((0 if path in ['/stop','/reset'] else 1,self.sequence,r))
  if not r.event.wait(CFG['request_policy']['handler_timeout_seconds']):
   with self.lock:r.canceled=True
   raise APIError(503,'request_timeout','Simulation request timed out and was canceled before execution')
  status,ctype,result=r.response
  if isinstance(result,Exception):raise result
  return status,ctype,result
 def setup_camera(self):
  scene=self.rig.scene;c=CFG['camera'];scene.set_ambient_light([.6,.6,.6]);scene.add_directional_light([1,1,-2],[1,1,1],shadow=True);scene.add_ground(0)
  b=scene.create_actor_builder();b.add_box_visual(half_size=c['calibration_box_half_size'],material=[.9,.15,.05]);b.add_box_collision(half_size=c['calibration_box_half_size']);box=b.build_static('calibration');box.pose=sapien.Pose(c['calibration_box_center'])
  self.camera=scene.add_camera('openarm_http_camera',c['width'],c['height'],c['fovy_rad'],c['near_m'],c['far_m'])
  pos=np.array(c['position']);forward=np.array(c['look_at'])-pos;forward/=np.linalg.norm(forward);left=np.cross([0,0,1],forward);left/=np.linalg.norm(left);up=np.cross(forward,left);self.camera_pose=np.eye(4);self.camera_pose[:3,:3]=np.stack([forward,left,up],axis=1);self.camera_pose[:3,3]=pos;self.camera.entity.pose=sapien.Pose(self.camera_pose)
 def init_rig(self):
  if self.rig:self.rig.close()
  self.rig=sim.Rig(P0/'model/compat_v6/robot.urdf',render=True);self.setup_camera();self.reset_steps=0
 def collision_probe(self):
  scene=sapien.Scene([sapien.physx.PhysxCpuSystem()]);scene.set_timestep(CFG['physics']['dt'])
  def contacts():
   return [{'bodies':[x.entity.name for x in c.bodies],'points':[{'position':p.position.tolist(),'normal':p.normal.tolist(),'separation_m':float(p.separation),'impulse_Ns':p.impulse.tolist()} for p in c.points]} for c in scene.physx_system.get_contacts()]
  try:
   b=scene.create_actor_builder();b.add_box_collision(half_size=[.1]*3);one=b.build_static('probe_static');b=scene.create_actor_builder();b.add_box_collision(half_size=[.1]*3);two=b.build('probe_dynamic');two.pose=sapien.Pose([.5,0,0]);scene.step();separated=contacts();two.pose=sapien.Pose([.18,0,0]);two.find_component_by_type(sapien.physx.PhysxRigidDynamicComponent).set_linear_velocity([0,0,0]);scene.step();overlap=contacts()
   if separated or not sim.colliding(overlap,lambda a,b:True):raise RuntimeError('Real collision startup probe failed')
   return {'source':'real PhysX isolated startup scene','physical_steps':2,'separated_contacts':separated,'known_overlap_m':.02,'overlap_contacts':overlap}
  finally:scene.clear()
 def model(self):
  rig=self.rig;rows=[]
  for j in rig.joints:
   x=rig.jxml[j.name];limit=x.find('limit');m=x.find('mimic');rows.append({'name':j.name,'type':x.get('type'),'dof':j.dof,'axis':np.fromstring(x.find('axis').get('xyz'),sep=' ').tolist(),'limits':rig.qlimits[rig.idx[j.name]].tolist(),'effort_limit':float(limit.get('effort')),'velocity_limit':float(limit.get('velocity')),'mimic':dict(m.attrib) if m is not None else None})
  return {'api_version':CFG['api_version'],'model_version':CFG['model_version'],'urdf_sha256':CFG['model_urdf_sha256'],'loaded_dof':rig.robot.dof,'independent_controls':rig.robot.dof-len(rig.mimics),'joints':rows,'all_joints':[{'name':j.name,'dof':j.dof,'type':j.type} for j in rig.robot.joints],'extra_disabled_pairs':rig.extra_pairs,'action_units':{'arm':'radians','gripper':'meters per-finger displacement, NOT total jaw width'}}
 def state(self):
  rig=self.rig;q=np.array(rig.robot.qpos);v=np.array(rig.robot.qvel);result={'epoch':self.epoch,'total_steps':self.total_steps,'reset_steps':self.reset_steps,'active_action':self.active,'joint_order':rig.names,'qpos':q,'qvel':v,'goal':rig.goal.copy(),'drive_target':rig.command.copy(),'model_version':CFG['model_version']}
  for side in ['left','right']:
   ix=[rig.idx[f'openarm_{side}_joint{i}'] for i in range(1,8)];result[side]={'qpos':q[ix],'qvel':v[ix],'goal':rig.goal[ix].copy(),'drive_target':rig.command[ix].copy()}
   a,b=[rig.idx[f'openarm_{side}_finger_joint{i}'] for i in [1,2]];result[side+'_gripper']={'driver_position_m':float(q[a]),'follower_position_m':float(q[b]),'driver_velocity_m_s':float(v[a]),'follower_velocity_m_s':float(v[b]),'goal_m':float(rig.goal[a]),'drive_target_m':float(rig.command[a]),'mimic_error_m':float(q[b]-q[a]),'limits_m':rig.qlimits[a].tolist()}
  return result
 def validate_action(self,body,ingress_epoch):
  keys={'left','right','left_gripper','right_gripper'}
  if not isinstance(body,dict) or set(body)-keys-{'epoch'} or not set(body)&keys:raise APIError(400,'invalid_action','Expected nonempty subset of left/right[7] and left_gripper/right_gripper scalars')
  epoch=body.get('epoch',ingress_epoch)
  if not isinstance(epoch,int) or isinstance(epoch,bool):raise APIError(400,'invalid_epoch','epoch must be an integer')
  if epoch!=self.epoch or ingress_epoch!=self.epoch:raise APIError(409,'stale_epoch','Target belongs to an earlier reset/stop epoch')
  for side in ['left','right']:
   if side in body:
    a=body[side]
    if not isinstance(a,list) or len(a)!=7 or not all(finite_number(x) for x in a):raise APIError(400,'invalid_dimension_or_number',side+' requires exactly 7 finite numbers')
    for i,x in enumerate(a,1):
     lower,upper=self.rig.qlimits[self.rig.idx[f'openarm_{side}_joint{i}']]
     if not lower<=x<=upper:raise APIError(400,'out_of_bounds',f'{side} joint{i} outside [{lower},{upper}]')
   name=side+'_gripper'
   if name in body:
    x=body[name]
    if not finite_number(x):raise APIError(400,'invalid_number',name+' requires a finite scalar')
    lower,upper=self.rig.qlimits[self.rig.idx[f'openarm_{side}_finger_joint1']]
    if not lower<=x<=upper:raise APIError(400,'out_of_bounds',name+' outside official limits')
  return self.rig.make_q(body,self.rig.goal)
 def cancel(self,status):
  if self.active:
   a=self.actions[self.active];a['status']=status
   if status=='failed':a['error']={'code':'simulation_error','message':self.error or 'Simulation stopped after an exception'}
   a['finished_step']=self.total_steps;self.log('action_'+status,action=a.copy());self.active=None
 def observation(self):
  self.rig.scene.update_render();self.camera.take_picture();rgb=np.array(self.camera.get_picture('Color'))[...,:3];position=np.array(self.camera.get_picture('Position'));seg=np.array(self.camera.get_picture('Segmentation'));valid=(position[...,3]<1)&np.isfinite(position).all(-1);depth=np.where(valid,-position[...,2],0).astype(np.float32);M=self.camera.get_model_matrix();E=self.camera.get_extrinsic_matrix();buf=io.BytesIO();np.savez_compressed(buf,rgb=rgb.astype(np.float32),depth_m=depth,valid_depth=valid,position_opengl_m=position,segmentation=seg,K=self.camera.get_intrinsic_matrix(),T_world_opengl=M,T_world_optical=M@np.diag([1,-1,-1,1]),T_world_sapien=self.camera_pose,extrinsic_world_optical=E,epoch=self.epoch,total_steps=self.total_steps);return buf.getvalue()
 def execute(self,r):
  try:
   if r.canceled:raise APIError(503,'request_timeout','Canceled request was not executed')
   method,path,body=r.method,r.path,r.body
   if path in ['/reset','/stop']:
    if method!='POST' or body!={}:raise APIError(400,'invalid_request','Expected POST with empty JSON object')
    self.cancel('canceled_reset' if path=='/reset' else 'stopped');self.epoch+=1
    if path=='/reset':self.init_rig()
    else:
     q=np.clip(np.array(self.rig.robot.qpos),self.rig.qlimits[:,0],self.rig.qlimits[:,1])
     for m in self.rig.mimics:q[self.rig.idx[m.joint]]=m.multiplier*q[self.rig.idx[m.mimic]]+m.offset
     self.rig.goal=q.copy();self.rig.command=q.copy();self.rig.apply_drive()
    self.log(path[1:],state=self.state());r.response=(200,'application/json',self.state())
   elif method=='POST' and path=='/actions':
    goal=self.validate_action(body,r.epoch);self.cancel('superseded');self.rig.goal=goal.copy();aid=f'{self.epoch}:{len(self.actions)+1}';a={'id':aid,'epoch':self.epoch,'status':'running','start_step':self.total_steps,'physical_steps':0,'step_budget':CFG['execution']['action_steps'],'requested':body.copy(),'goal':goal.tolist(),'error':None};self.actions[aid]=a;self.active=aid;self.log('action_started',action=a);r.response=(202,'application/json',a.copy())
   elif method=='GET' and path=='/state':r.response=(200,'application/json',self.state())
   elif method=='GET' and path=='/model':r.response=(200,'application/json',self.model())
   elif method=='GET' and path=='/collisions':r.response=(200,'application/json',{'epoch':self.epoch,'total_steps':self.total_steps,'contacts':self.rig.contacts(),'startup_validation':self.startup_collision,'self_collision_enabled':True})
   elif method=='GET' and path=='/observation':r.response=(200,'application/x-npz',self.observation())
   elif method=='GET' and path.startswith('/actions/'):
    aid=urllib.parse.unquote(path[len('/actions/'):])
    if aid not in self.actions:raise APIError(404,'unknown_action','Action ID not found')
    r.response=(200,'application/json',self.actions[aid].copy())
   else:raise APIError(404,'not_found','Unknown endpoint or method')
  except APIError as e:r.response=(e.status,'application/json',e)
  except Exception as e:
   self.log('request_exception',traceback=traceback.format_exc());r.response=(503,'application/json',APIError(503,'simulation_error',str(e)));self.error=str(e);self.closing.set()
  finally:r.event.set()
 def physics_step(self):
  self.rig.step();self.total_steps+=1;self.reset_steps+=1;q=np.array(self.rig.robot.qpos);v=np.array(self.rig.robot.qvel)
  if not np.isfinite(q).all() or not np.isfinite(v).all():raise RuntimeError('Nonfinite physics state')
  self.qlog.append(q);self.vlog.append(v);self.clog.append(self.rig.command.copy());self.glog.append(self.rig.goal.copy());self.elog.append(self.epoch);self.action_log.append(self.active or '');self.times.append(self.total_steps)
  if self.active:
   a=self.actions[self.active];a['physical_steps']+=1
   if a['physical_steps']>=a['step_budget']:
    tol=CFG['tolerances'];err=abs(q-self.rig.goal);ok=np.max(err[self.rig.arm])<=tol['arm_position_rad'] and np.max(err[self.rig.finger])<=tol['finger_position_m'] and np.max(abs(v[self.rig.arm]))<=tol['arm_final_velocity_rad_s'] and np.max(abs(v[self.rig.finger]))<=tol['finger_final_velocity_m_s'];a.update(status='completed' if ok else 'failed',finished_step=self.total_steps,final_arm_error_rad=float(np.max(err[self.rig.arm])),final_finger_error_m=float(np.max(err[self.rig.finger])),final_arm_velocity_rad_s=float(np.max(abs(v[self.rig.arm]))),final_finger_velocity_m_s=float(np.max(abs(v[self.rig.finger]))),error=None if ok else {'code':'convergence_timeout','message':'Fixed 720 physical steps ended outside frozen tolerances'});self.log('action_finished',action=a.copy());self.active=None
  if self.fault_after_steps and self.total_steps>=self.fault_after_steps:raise RuntimeError('Acceptance fault injection after real physical steps')
 def run(self):
  try:
   self.init_rig();self.startup_collision=self.collision_probe()
   with self.lock:self.ready=True
   self.log('ready',health=self.health(),model=self.model(),startup_collision=self.startup_collision)
   while not self.closing.is_set():
    started=time.monotonic()
    with self.lock:
     for _ in range(100):
      try:_,_,r=self.queue.get_nowait()
      except queue.Empty:break
      self.execute(r)
     if not self.closing.is_set():self.physics_step()
    delay=CFG['physics']['dt']-(time.monotonic()-started)
    if delay>0:self.closing.wait(delay)
  except BaseException as e:
   with self.lock:self.error=str(e);self.ready=False;self.cancel('failed');self.closing.set()
   self.log('fatal',error=str(e),traceback=traceback.format_exc())
  finally:
   with self.lock:
    self.ready=False
    while not self.queue.empty():
     _,_,r=self.queue.get_nowait();r.response=(503,'application/json',APIError(503,'shutting_down','Simulation exited'));r.event.set()
   if self.rig:
    np.savez_compressed(self.evidence/'trajectory.npz',qpos=self.qlog,qvel=self.vlog,drive_target=self.clog,goal=self.glog,epoch=self.elog,action_id=self.action_log,total_steps=self.times,joint_order=self.rig.names,dt=CFG['physics']['dt']);self.rig.close()
   self.log('simulation_closed',error=self.error)

class Server(ThreadingHTTPServer):daemon_threads=True;allow_reuse_address=True
class Handler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,fmt,*args):
  with (self.server.runtime.evidence/'http.jsonl').open('a') as f:f.write(dump({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'message':fmt%args}).decode()+'\n')
 def respond(self,status,ctype,data):
  content=data if isinstance(data,bytes) else dump(data);self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(content)));self.send_header('Connection','close');self.end_headers();self.wfile.write(content)
 def dispatch(self):
  rt=self.server.runtime
  try:
   parsed=urllib.parse.urlsplit(self.path)
   if parsed.query:raise APIError(400,'invalid_query','Query parameters are unsupported')
   if self.command=='GET' and parsed.path=='/health':self.respond(200,'application/json',rt.health());return
   body=None
   if self.command=='POST':
    if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise APIError(400,'invalid_content_type','Use application/json')
    try:n=int(self.headers.get('Content-Length','0'))
    except ValueError:raise APIError(400,'invalid_length','Invalid Content-Length')
    if n<=0 or n>CFG['request_policy']['max_body_bytes']:raise APIError(400,'invalid_length','Body size outside allowed range')
    def invalid_constant(x):raise ValueError('Nonfinite JSON number '+x)
    def no_duplicates(pairs):
     d={}
     for k,v in pairs:
      if k in d:raise ValueError('Duplicate JSON key '+k)
      d[k]=v
     return d
    try:body=json.loads(self.rfile.read(n),parse_constant=invalid_constant,object_pairs_hook=no_duplicates)
    except (ValueError,UnicodeDecodeError):raise APIError(400,'invalid_json','Strict finite JSON required')
   status,ctype,result=rt.submit(self.command,parsed.path,body);self.respond(status,ctype,result)
  except APIError as e:self.respond(e.status,'application/json',{'error':{'code':e.code,'message':str(e)},'epoch':rt.epoch})
  except (BrokenPipeError,ConnectionResetError):pass
 def do_GET(self):self.dispatch()
 def do_POST(self):self.dispatch()

def verify_freeze():
 if '.'.join(map(str,sys.version_info[:3]))!=CFG['python']:raise RuntimeError('Python version mismatch')
 if sha(ROOT/'acceptance_config.json')!=(ROOT/'acceptance_config.sha256').read_text().split()[0]:raise RuntimeError('Phase 1 config changed')
 for p,h in [(P0/'model/compat_v6/robot.urdf',CFG['model_urdf_sha256']),(P0/'model/compat_v6/implementation_lock.json',CFG['implementation_lock_sha256']),(P0/'validate.py',CFG['rig_source_sha256']),(P0/'gate_result.json',CFG['phase0_gate_sha256']),(P0/'acceptance_config.json',CFG['phase0_config_sha256'])]:
  if sha(p)!=h:raise RuntimeError('Frozen Phase 0 dependency changed: '+str(p))
 for dep,v in CFG['dependencies'].items():
  if sim.md.version(dep)!=v:raise RuntimeError('Dependency mismatch: '+dep)
 ml=json.loads((P0/'model/compat_v6/implementation_lock.json').read_text())
 for f,h in ml['mesh_sha256'].items():
  if sha(P0/'model/compat_v6'/f)!=h:raise RuntimeError('Model mesh changed: '+f)

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8870);parser.add_argument('--evidence',required=True);parser.add_argument('--fault-after-steps',type=int);args=parser.parse_args();verify_freeze();ev=Path(args.evidence).resolve();ev.mkdir(parents=True,exist_ok=False);rt=Runtime(ev,args.fault_after_steps);server=Server(('127.0.0.1',args.port),Handler);server.runtime=rt;server.timeout=.1
 def shutdown(signum,frame):rt.closing.set()
 signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown);rt.thread.start()
 print(dump({'pid':os.getpid(),'url':f'http://127.0.0.1:{server.server_port}','config_sha256':sha(ROOT/'acceptance_config.json')}).decode(),flush=True)
 try:
  while not rt.closing.is_set():server.handle_request()
 finally:
  rt.closing.set();server.server_close();rt.thread.join(10)
  if rt.thread.is_alive():print('Simulation thread did not close',flush=True);os._exit(2)
 print(dump({'closed':True,'error':rt.error,'physical_steps':rt.total_steps}).decode(),flush=True);return 1 if rt.error else 0
if __name__=='__main__':sys.exit(main())
