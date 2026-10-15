"""Deterministic MJCF conversion of the accepted OpenArm compat_v6 URDF.

Preserves link frames, masses, inertia tensors, collision meshes and joint/effort
limits. MuJoCo uses convex mesh contacts. Display uses the same pinned collision
meshes (DAE visual materials are not used). No object or task code is converted.
"""
from pathlib import Path
import xml.etree.ElementTree as E
import hashlib, json
import numpy as np
from scipy.spatial.transform import Rotation
ROOT=Path(__file__).resolve().parent
REPO=ROOT.parent
URDF=REPO/'openarm_phase0_recheck/model/compat_v6/robot.urdf'
BASE=REPO/'openarm_mobile/model/source/tidyverse.urdf'

def s(v): return ' '.join(str(float(x)) for x in v)
def transform(node):
    if node is None: return {}
    return {'pos':node.get('xyz','0 0 0'), 'quat':s(Rotation.from_euler('xyz',np.fromstring(node.get('rpy','0 0 0'),sep=' ')).as_quat()[[3,0,1,2]])}

def build_model():
    src=E.parse(URDF).getroot(); links={n.get('name'):n for n in src.findall('link')}
    children={}
    for j in src.findall('joint'): children.setdefault(j.find('parent').get('link'),[]).append(j)
    used={str(URDF):hashlib.sha256(URDF.read_bytes()).hexdigest(),str(BASE):hashlib.sha256(BASE.read_bytes()).hexdigest()}
    def document(name):
        r=E.Element('mujoco',model=name); E.SubElement(r,'compiler',angle='radian');
        for tag in ['asset','worldbody','actuator','sensor','contact','equality']: E.SubElement(r,tag)
        return r
    def add_link(doc,parent,name,attrs=None,grip=False):
        b=E.SubElement(parent,'body',name=name,**(attrs or {})); link=links[name]
        inertia=link.find('inertial')
        if inertia is not None:
            tensor=inertia.find('inertia'); mat=np.array([[float(tensor.get('ixx')),float(tensor.get('ixy')),float(tensor.get('ixz'))],[float(tensor.get('ixy')),float(tensor.get('iyy')),float(tensor.get('iyz'))],[float(tensor.get('ixz')),float(tensor.get('iyz')),float(tensor.get('izz'))]])
            rot=Rotation.from_euler('xyz',np.fromstring(inertia.find('origin').get('rpy','0 0 0'),sep=' ')).as_matrix(); mat=rot@mat@rot.T
            E.SubElement(b,'inertial',pos=inertia.find('origin').get('xyz','0 0 0'),mass=inertia.find('mass').get('value'),fullinertia=s([mat[0,0],mat[1,1],mat[2,2],mat[0,1],mat[0,2],mat[1,2]]))
        for i,c in enumerate(link.findall('collision')):
            mesh=c.find('geometry/mesh'); path=(URDF.parent/mesh.get('filename')).resolve(); used[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest(); meshname=name+'_mesh'+str(i)
            E.SubElement(doc.find('asset'),'mesh',name=meshname,file=str(path),scale=mesh.get('scale','1 1 1'))
            geo=dict(name=name+'_collision'+str(i),type='mesh',mesh=meshname,**transform(c.find('origin')))
            E.SubElement(b,'geom',**geo,group='0',friction='1 0.005 0.0001',condim='4')
            geo['name']=name+'_visual'+str(i);E.SubElement(b,'geom',**geo,group='1',contype='0',conaffinity='0',rgba='.65 .67 .69 1')
        for j in children.get(name,[]):
            child=j.find('child').get('link')
            if not grip and ('hand' in child or 'finger' in child): continue
            cb=add_link(doc,b,child,transform(j.find('origin')),grip)
            if j.get('type')!='fixed':
                lim=j.find('limit'); E.SubElement(cb,'joint',name=j.get('name'),type='slide' if j.get('type')=='prismatic' else 'hinge',axis=j.find('axis').get('xyz'),limited='true',range=lim.get('lower')+' '+lim.get('upper'),damping='2' if grip else '.2',armature='.01',frictionloss='.1')
                if grip:
                    E.SubElement(doc.find('actuator'),'position',name=j.get('name')+'_act',joint=j.get('name'),kp='500',kv='10',ctrllimited='true',ctrlrange=lim.get('lower')+' '+lim.get('upper'),forcelimited='true',forcerange=s([-float(lim.get('effort')),float(lim.get('effort'))]))
                else:
                    E.SubElement(doc.find('actuator'),'motor',name=j.get('name')+'_act',joint=j.get('name'),ctrllimited='true',ctrlrange=s([-float(lim.get('effort')),float(lim.get('effort'))]))
        return b
    robot=document('OpenArm365'); root=E.SubElement(robot.find('worldbody'),'body',name='base')
    # Physical left is first; the logical right task arm maps to this chain.
    body=add_link(robot,root,'openarm_body_link0',{'pos':'0 0 .6'})
    for c in list(body):
        if c.tag=='body':body.remove(c)
    for side in ['left','right']:
        j=next(j for j in children['openarm_body_link0'] if side in j.get('name'))
        add_link(robot,body,j.find('child').get('link'),transform(j.find('origin')))
    # Remove orphan assets/actuators from first recursive copy.
    for tag in ['asset','actuator']:
        seen=set()
        for e in list(robot.find(tag)):
            if e.get('name') in seen: robot.find(tag).remove(e)
            else: seen.add(e.get('name'))
    act=robot.find('actuator'); ordered=sorted(list(act),key=lambda a:(0 if 'left' in a.get('name') else 1,a.get('name'))); act[:]=ordered
    E.SubElement(root,'site',name='left_center',size='.003',rgba='0 0 0 0'); E.SubElement(root,'site',name='right_center',size='.003',rgba='0 0 0 0')
    grip=document('OpenArmGripper365'); hand=add_link(grip,grip.find('worldbody'),'openarm_left_link7',{},True)
    # Keep only the hand and finger subtree; link7 belongs to the arm.
    for node in list(hand):
        if node.tag in ('inertial','geom'): hand.remove(node)
    for node in list(grip.find('asset')):
        if node.get('name','').startswith('openarm_left_link7'): grip.find('asset').remove(node)
    # Gripper mount already includes original link7 -> hand offset.
    hand.set('name','hand')
    tcp=hand.find(".//body[@name='openarm_left_hand_tcp']"); tcp.set('name','eef')
    for name in ['grip_site','grip_site_cylinder','ee','ee_x','ee_y','ee_z','ft_frame']:
        E.SubElement(tcp,'site',name=name,size='.003',rgba='0 0 0 0')
    E.SubElement(hand,'camera',name='eye_in_hand',pos='.05 0 .03',quat='0 1 0 0',fovy='75')
    E.SubElement(grip.find('sensor'),'force',name='force_ee',site='ft_frame');E.SubElement(grip.find('sensor'),'torque',name='torque_ee',site='ft_frame')
    E.SubElement(grip.find('equality'),'joint',joint1='openarm_left_finger_joint1',joint2='openarm_left_finger_joint2',polycoef='0 1 0 0 0')
    base=document('TidyverseBase365'); b=E.SubElement(base.find('worldbody'),'body',name='base')
    for name,typ,axis,eff in [('forward','slide','1 0 0',600),('side','slide','0 1 0',600),('yaw','hinge','0 0 1',600)]:
        E.SubElement(b,'joint',name='joint_mobile_'+name,type=typ,axis=axis,limited='false',damping='10')
        speed=.3 if name=='yaw' else .15
        E.SubElement(base.find('actuator'),'velocity',name='actuator_mobile_'+name,joint='joint_mobile_'+name,kv='1000',ctrllimited='true',ctrlrange=s([-speed,speed]),forcelimited='true',forcerange=s([-eff,eff]))
    E.SubElement(b,'inertial',pos='-.198 0 .2',mass='50',diaginertia='1 1 .5')
    E.SubElement(b,'geom',name='chassis_collision',type='box',pos='-.198 0 .309',size=s(np.array([.733,.523,.606])/2),group='0')
    mesh=REPO/'openarm_mobile/model/source/tidyverse_base/base_vis.stl';used[str(mesh)]=hashlib.sha256(mesh.read_bytes()).hexdigest()
    E.SubElement(base.find('asset'),'mesh',name='base_visual',file=str(mesh))
    E.SubElement(b,'geom',type='mesh',mesh='base_visual',pos='0 0 .47225',contype='0',conaffinity='0',group='1',rgba='.5 .5 .5 1')
    support=E.SubElement(b,'body',name='support');E.SubElement(support,'site',name='center',size='.005',rgba='0 0 0 0')
    # Preserve the existing SRDF exceptions; never disable global collision.
    for side in ['left','right']:
        E.SubElement(robot.find('contact'),'exclude',body1=f'openarm_{side}_link5',body2=f'openarm_{side}_link7')
    for finger in ['left','right']:
        E.SubElement(grip.find('contact'),'exclude',body1='openarm_left_hand',body2=f'openarm_left_{finger}_finger')
    # Robosuite collects XML joints in traversal order; URDF joints belong before child bodies.
    for doc in [robot,grip,base]:
        for body_node in doc.findall('.//body'):
            body_node[:]=sorted(list(body_node),key=lambda n:0 if n.tag=='joint' else 1)
    ROOT.joinpath('model').mkdir(exist_ok=True)
    for name,doc in [('robot',robot),('gripper',grip),('base',base)]:
        E.indent(doc);path=ROOT/'model'/f'{name}.xml';path.write_bytes(E.tostring(doc,encoding='utf-8',xml_declaration=True)); used[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
    import copy
    rightgrip=copy.deepcopy(grip)
    for node in rightgrip.iter():
        for key,value in list(node.attrib.items()):
            if key=='file':
                # Original right-hand URDF geometry may point to a shared source.
                continue
            node.set(key,value.replace('openarm_left','openarm_right'))
    # Restore right URDF mesh references, including its mirrored right finger.
    for meshnode in rightgrip.find('asset'):
        linkname=meshnode.get('name').rsplit('_mesh',1)[0]
        meshsrc=links[linkname].find('collision/geometry/mesh')
        meshpath=(URDF.parent/meshsrc.get('filename')).resolve(); used[str(meshpath)]=hashlib.sha256(meshpath.read_bytes()).hexdigest()
        meshnode.set('file',str(meshpath))
    rightpath=ROOT/'model/gripper_right.xml';rightpath.write_bytes(E.tostring(rightgrip,encoding='utf-8',xml_declaration=True));used[str(rightpath)]=hashlib.sha256(rightpath.read_bytes()).hexdigest()
    (ROOT/'model/identity.json').write_text(json.dumps({'sources':used,'collision':'MuJoCo convex hull of original collision meshes; same dimensions','mount_height_m':.6,'gripper_tcp_hand_m':[0,0,.0835]},indent=2)+'\n')
    return used
if __name__=='__main__': build_model()
