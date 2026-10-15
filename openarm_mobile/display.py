"""Independent display process. Reads snapshots; never steps control physics.

Only this render replica applies captured actor poses/qpos. The real service
publishes through a bounded nonblocking queue; overload drops old snapshots.
"""
from pathlib import Path
import json
import math
import os
import queue
import time

POLICY_CAMERA={'name':'base_camera','width':640,'height':480}


def camera_specs(arm):
    if arm not in {'left','right'}: raise ValueError('Invalid executing arm')
    return {
        'chest':{'name':'Chest first person','parent':'openarm_body_link0','width':1920,'height':1080,'fovy':math.radians(75)},
        'wrist':{'name':arm.capitalize()+' wrist','parent':f'openarm_{arm}_link7','width':1920,'height':1080,'fovy':math.radians(80)},
        'external':{'name':'External overview','parent':None,'width':1920,'height':1080,'fovy':math.radians(65)}}


def atomic(path, data):
    path=Path(path);temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_bytes(data);os.replace(temp,path)


def display_worker(inbox, done, output, arm, blueprint):
    # Renderer resource ownership is independent of the control process.
    nvidia=Path('/usr/share/vulkan/icd.d/nvidia_icd.json')
    if nvidia.exists():os.environ['VK_ICD_FILENAMES']=str(nvidia)
    import io
    import signal
    signal.signal(signal.SIGTERM,lambda *_: done.set())
    signal.signal(signal.SIGINT,lambda *_: done.set())
    import resource
    import numpy as np
    import sapien
    from PIL import Image
    from .kitchen import accepted_module, MODEL
    from .render_scene import import_scene
    from mani_skill.utils.sapien_utils import look_at
    from mani_skill.render.shaders import set_shader_pack, PREBUILT_SHADER_CONFIGS
    set_shader_pack(PREBUILT_SHADER_CONFIGS['default'])
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    kitchen=None
    try:
        kitchen=accepted_module().sim.Rig(MODEL,render=True)
        scene=kitchen.scene
        kitchen.robot.set_root_pose(sapien.Pose(blueprint['root_pose']))
        actors=import_scene(scene,blueprint)
        scene.set_ambient_light([.3,.3,.3])
        scene.add_directional_light([1,1,-2],[1,1,1],shadow=True)
        cameras={}
        specs=camera_specs(arm)
        # SAPIEN uses X forward, Y left, Z up; optical conventions are saved.
        for key,spec in specs.items():
            if key=='chest':
                local=look_at([.05,-.08,.55],[.42,-.08,.30])
            elif key=='wrist':
                local=look_at([.07,.04,.04],[.01,0,.22])
            else:
                destination=np.array(blueprint['destination'])
                local=look_at(destination+[-1.4,-1.2,1.375],destination+[-.35,-.30,-.075])
            pose=sapien.Pose(local.p[0].numpy(),local.q[0].numpy())
            if spec['parent']:
                entity=kitchen.robot.find_link_by_name(spec['parent']).entity
                camera=scene.add_mounted_camera('display_'+key,entity,pose,1920,1080,spec['fovy'],.015,12.)
            else:
                camera=scene.add_camera('display_'+key,1920,1080,spec['fovy'],.015,12.)
                camera.entity.pose=pose
            cameras[key]=camera
            (output/key).mkdir(exist_ok=True)
        sequence=0;bytes_saved=0;last_source=None;origin=time.monotonic();last_epoch=None
        while not done.is_set():
            try:snapshot=inbox.get(timeout=.2)
            except queue.Empty:continue
            # Drain to the latest actual physical sample without blocking owner.
            while True:
                try:snapshot=inbox.get_nowait()
                except queue.Empty:break
            if snapshot['monotonic']==last_source:continue
            last_source=snapshot['monotonic']
            if last_epoch is not None and last_epoch!=snapshot['epoch']:
                atomic(output/'latest.json',json.dumps({'state':'resetting','epoch':snapshot['epoch']}).encode())
            last_epoch=snapshot['epoch']
            kitchen.robot.set_qpos(snapshot['qpos'])
            for actor,pose in zip(actors,snapshot['actors']):actor.pose=sapien.Pose(pose)
            # No scene.step: render replica cannot affect actual control/evaluator.
            scene.update_render()
            sequence+=1;batch={}
            begin=time.monotonic()
            for key,camera in cameras.items():
                started=time.monotonic();camera.take_picture()
                raw=np.array(camera.get_picture('Color'))[...,:3]
                pixels=raw if raw.dtype==np.uint8 else np.uint8(np.clip(raw,0,1)*255)
                if pixels.shape!=(1080,1920,3):raise RuntimeError('Display camera source is not native 1080p')
                encoded=io.BytesIO();Image.fromarray(pixels).save(encoded,format='JPEG',quality=95,subsampling=0)
                data=encoded.getvalue();bytes_saved+=len(data)
                if sequence==1:
                    Image.fromarray(pixels).save(output/key/'native_initial.png')
                    decoded=np.asarray(Image.open(io.BytesIO(data)),dtype=np.float32)
                    mse=float(np.mean((decoded-pixels.astype(np.float32))**2))
                    atomic(output/key/'encoding_quality.json',json.dumps({'jpeg_quality':95,'subsampling':0,
                        'mse':mse,'psnr_db':10*math.log10(255**2/mse) if mse>0 else None,
                        'native_width':1920,'native_height':1080,'resized':False}).encode())
                if bytes_saved>2_000_000_000:raise RuntimeError('Display recording storage limit reached')
                relative=f'{key}/{sequence:06d}.jpg';atomic(output/relative,data)
                ended=time.monotonic()
                batch[key]={**specs[key],'file':relative,'sequence':sequence,'width':1920,'height':1080,
                    'captured_at':snapshot['unix'],'source_monotonic':snapshot['monotonic'],
                    'render_started_monotonic':started,'render_completed_monotonic':ended,
                    'source_age_seconds':ended-snapshot['monotonic'],'render_seconds':ended-started,
                    'color_dtype':str(raw.dtype),'color_range':[float(raw.min()),float(raw.max())],
                    'world_opengl':camera.get_model_matrix().tolist(),'K':camera.get_intrinsic_matrix().tolist()}
            row={'state':'connected','sequence':sequence,'epoch':snapshot['epoch'],
                'physical_step':snapshot['step'],'source_monotonic':snapshot['monotonic'],
                'captured_at':snapshot['unix'],'views':batch,'render_batch_seconds':time.monotonic()-begin,
                'max_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                'cpu_seconds':sum(resource.getrusage(resource.RUSAGE_SELF)[i] for i in [0,1]),
                'bytes_saved':bytes_saved,'pid':os.getpid()}
            with (output/'samples.jsonl').open('a') as log:log.write(json.dumps(row)+'\n')
            atomic(output/'latest.json',json.dumps(row).encode())
            done.wait(max(0,.1-(time.monotonic()-begin)))
        atomic(output/'state.json',json.dumps({'state':'ended','frames':sequence,'bytes_saved':bytes_saved,'seconds':time.monotonic()-origin}).encode())
    except BaseException as exc:
        import traceback
        failure={'state':'disconnected','error':str(exc),'traceback':traceback.format_exc()}
        atomic(output/'state.json',json.dumps(failure).encode())
        atomic(output/'latest.json',json.dumps(failure).encode())
    finally:
        if kitchen is not None:kitchen.close()
