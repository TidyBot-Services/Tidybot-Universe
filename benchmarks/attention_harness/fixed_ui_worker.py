"""Private-prefix, single-use lifecycle and SDK runner for main UI fixed scenes."""
from benchmarks.attention_harness.workspace_paths import get, lazy
raise RuntimeError('V2-only: historical modified solver/executor entry retired; use authenticated main deployment. No fallback.')
from pathlib import Path
import hashlib,json,os,signal,socket,subprocess,sys,threading,time,traceback,urllib.request,urllib.error,uuid
ROOT=lazy('ui.fixed_root')
SOURCE=Path(__file__).parent if '__file__' in globals() else get('repo.native_tree') / 'benchmarks/attention_harness'
# -c loader deliberately keeps the UI checkout out of sys.path. All physical
# dependencies and accepted source resolve from the immutable isolated baseline.
sys.path[:]=[p for p in sys.path if not p or Path(p).resolve().is_relative_to(ROOT)]
for name in ['scripts','executor','service']:sys.path.insert(0,str(ROOT/name))
out=Path(sys.argv[1]);lock=threading.RLock();interrupted=threading.Event();closed=threading.Event();children=[];backend=None;physics=None
value=json.loads((out/'status.json').read_text());value['worker_pid']=os.getpid()
http_log=(out/'http.jsonl').open('a');started=time.monotonic()

def save(name,data):
    path=out/name;temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');temp.write_text(json.dumps(data,indent=2,default=str));os.replace(temp,path)
def update(**changes):
    with lock:
        value.update(changes);value['updated_at']=time.time();save('status.json',value)
def event(phase,**detail):
    with lock:
        item={'time':time.time(),'elapsed':time.monotonic()-started,'phase':phase,**detail}
        value['events'].append(item);update(phase=phase)
        with (out/'events.jsonl').open('a') as f:f.write(json.dumps(item)+'\n')
def check():
    if interrupted.is_set():raise InterruptedError('Operator '+json.loads((out/'interrupt.json').read_text())['action'])
    if time.monotonic()-started>600:raise TimeoutError('Fixed UI whole-run deadline600s; stopping owned services')
def http(method,path,body=None,timeout=90,origin='http://127.0.0.1:17554',priority=False):
    if not priority:check()
    request=urllib.request.Request(origin+path,data=None if body is None else json.dumps(body).encode(),method=method,headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=timeout) as r:status=r.status;response=json.load(r)
    except urllib.error.HTTPError as exc:
        status=exc.code;response=json.load(exc)
    with lock:
        http_log.write(json.dumps({'time':time.time(),'method':method,'path':path,'request':body,'status':status,'response':response})+'\n');http_log.flush()
    if status>=400:raise RuntimeError(f'HTTP{status} {path}: '+json.dumps(response))
    return response

def launch(label,args):
    stream=(out/(label+'.log')).open('w')
    p=subprocess.Popen([str(ROOT/'.venv/bin/python'),'-s','-u',*args],cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT)
    children.append((label,p,stream));save('processes.json',[{'name':n,'pid':proc.pid,'args':proc.args} for n,proc,_ in children]);return p

def wait_ready(proc,origin,seconds):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        check()
        if proc.poll() is not None:raise RuntimeError(f'Service exited({proc.returncode}); inspect '+next(n for n,p,_ in children if p is proc)+'.log')
        try:
            result=http('GET','/health',timeout=1,origin=origin)
            if result.get('ready',True):return result
        except (OSError,urllib.error.URLError):pass
        time.sleep(.15)
    raise TimeoutError(f'Service readiness deadline{seconds}s: {origin}')

def control_watch():
    while not closed.wait(.05):
        if (out/'interrupt.json').exists() and not interrupted.is_set():
            interrupted.set();event('Stop requested; awaiting both arms')
            if backend is not None:
                try:
                    command=json.loads((out/'interrupt.json').read_text());state=http('GET','/state',timeout=5,priority=True)
                    if command['action']=='cancel' and state.get('active_action'):
                        receipt=http('POST','/cancel',{'epoch':state['epoch'],'action_id':state['active_action']},timeout=10,priority=True)
                        receipt=backend._confirm_rest(receipt)
                    else:receipt=backend.stop()
                    save('operator-stop.json',receipt);update(stop=receipt)
                    if state.get('active_action'):save('operator-action.json',http('GET','/actions/'+state['active_action'],timeout=5,priority=True))
                except Exception as exc:save('operator-stop-error.json',{'error':str(exc)})
            elif physics is None:
                # Cancel a cold planner warmup without waiting180s for its socket.
                for _,proc,_ in children:
                    if proc.poll() is None:proc.terminate()
            return

signal.signal(signal.SIGTERM,lambda *_:save('interrupt.json',{'action':'stop','request_id':'worker-signal'}))
signal.signal(signal.SIGINT,lambda *_:save('interrupt.json',{'action':'stop','request_id':'worker-signal'}))
watch=threading.Thread(target=control_watch,daemon=True);watch.start()
terminal='failed';error=None;summary={}
try:
    event('Verifying accepted baseline and isolated environment')
    from verify_version import verify
    verification=verify();save('baseline-verification.json',verification)
    baseline_version=hashlib.sha256((ROOT/'VERSION.json').read_bytes()).hexdigest()
    files={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [SOURCE/'fixed_ui.py',SOURCE/'fixed_ui_worker.py',SOURCE/'fixed_ui_physics.py',SOURCE/'ui/fixed.html',SOURCE/'ui/fixed.js',SOURCE/'ui_server.py']}
    (out/'source').mkdir()
    for file in files:
        path=Path(file);target=out/'source'/str(path.relative_to(SOURCE));target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(path.read_bytes())
    save('version.json',{'baseline_version_sha256':baseline_version,'baseline':verification,'ui_files':files,'python':sys.executable,'prefix':sys.prefix,'scope':'fixed simulation only'})
    for port in [17553,17554]:
        with socket.socket() as s:
            s.settimeout(.2)
            if s.connect_ex(('127.0.0.1',port))==0:raise RuntimeError(f'Port{port} occupied; existing service left running. Stop the other fixed session first.')
    env={**os.environ,'PYTHONNOUSERSITE':'1','PYTHONPATH':str(ROOT/'executor')+':'+str(ROOT/'service')+':'+str(ROOT/'scripts'),
         'CUROBO_PORT':'17553','CUROBO_ROBOT_CFG':str(ROOT/'config/openarm_fixed.yml'),
         'VK_ICD_FILENAMES':os.environ.get('VK_ICD_FILENAMES','/usr/share/vulkan/icd.d/lvp_icd.x86_64.json')}
    event('Starting cuRoboV2 planner')
    planner=launch('planner',['-m','curobo_service']);summary['planner_health']=wait_ready(planner,'http://127.0.0.1:17553',45)
    event('Warming up cuRoboV2 on GPU')
    save('warmup.json',http('POST','/warmup',{},timeout=180,origin='http://127.0.0.1:17553'));check();update(planner_ready=True)
    scene=value['scene'];fixture={'obstacles':'fixed_obstacles_v1.json','shared':'fixed_shared_bar_v3.json','contact_loss':'fixed_contact_loss_v1.json','timeout':'fixed_timeout_fault_v1.json','planning_rejected':'fixed_obstacles_v1.json'}[scene]
    # The generic warmup builds14DOF only. A one-arm model adds the inactive
    # arm locks and requires its own cold compilation, outside the unchanged
    # eight-second physical planning budget. This query never dispatches.
    if value['arms']!='both':
        fixture_data=json.loads((ROOT/'config'/fixture).read_text());initial=fixture_data['initial']
        names=[f'openarm_{side}_joint{i}' for side in ['left','right'] for i in range(1,8)]
        q=initial['left']+initial['right']
        event('Warming selected arm model; read-only, no physical dispatch')
        obj=fixture_data.get('object',{'center':[5,5,5],'half_size':[.02]*3})
        world=fixture_data['cuboids']+[{'name':'floor','center':[0,0,-.03],'half_size':[3,3,.01]},
            {'name':'acceptance_object','center':obj['center'],'half_size':obj['half_size'],'quat_wxyz':obj.get('quat_wxyz',[1,0,0,0])}]
        http('POST','/world/cuboids',{'env_id':'fixed-acceptance','cuboids':world},timeout=30,origin='http://127.0.0.1:17553')
        warm=http('POST','/plan/joint',{'env_id':'fixed-acceptance','joint_names':names,'current_q':q,'target_qpos':q,
            'active_joints':[n for n in names if n.startswith('openarm_'+value['arms']+'_')],
            'locked_joints':{f'openarm_{side}_finger_joint1':initial[side+'_gripper'] for side in ['left','right']}},
            timeout=180,origin='http://127.0.0.1:17553')
        save('selected-arm-warmup.json',warm)
        if warm.get('status')!='success':raise RuntimeError('Selected arm warmup rejected: '+str(warm))
    event('Starting fixed-base physics executor')
    wrapper=SOURCE/'fixed_ui_physics.py'
    physics=launch('physics',['-c','import sys; p=sys.argv.pop(1); exec(compile(open(p).read(),p,"exec"))',str(wrapper),str(out),str(ROOT/'config'/fixture)])
    summary['executor_health']=wait_ready(physics,'http://127.0.0.1:17554',60);update(executor_ready=True)
    from openarm_bimanual.fixed_backend import FixedBimanualActionBackend
    backend=FixedBimanualActionBackend('http://127.0.0.1:17554',timeout_seconds=90,transport=lambda method,path,body,timeout:http(method,path,body,timeout,priority=path in {'/stop','/cancel','/state'}),interrupt_check=interrupted.is_set)
    summary['sdk_attestation']={'class':'openarm_bimanual.fixed_backend.FixedBimanualActionBackend','health':backend._attested_health,'selection':value['arms'],'actual_fixed_base':backend._state()['base']['fixed']}
    save('sdk-attestation.json',summary['sdk_attestation']);update(status='ready',sdk_ready=True);event('Ready · planner / executor / SDK connected')
    ready_until=time.monotonic()+300
    while not (out/'execute.json').exists():
        check()
        if time.monotonic()>ready_until:raise TimeoutError('Ready session idle timeout300s; no motion dispatched')
        if any(p.poll() is not None for _,p,_ in children):raise RuntimeError('An owned service exited while ready')
        time.sleep(.1)
    check();summary['before']=backend._state();save('before.json',summary['before'])
    if scene in {'shared','contact_loss'}:
        update(status='executing');event('Closing left gripper; requiring real two-finger contact')
        backend.set_limb_gripper('left',1.,settle_steps=80);check();event('Closing right gripper; requiring real two-finger contact')
        backend.set_limb_gripper('right',1.,settle_steps=80);check()
        event('Acquiring common grasp from both physical contacts');summary['group']=backend.begin_cooperation()
        update(status='planning');event('Planning common-object +8mm lift with joint14DOF cuRoboV2')
        # Existing SDK performs planning, epoch validation, ordered atomic
        # segments, physical waiting and4mm/0.03rad convergence checks.
        update(status='executing');summary['transport']=backend.move_object_delta(0.,0.,.008,tolerance=.004,max_steps=100)
        summary['after']=backend._state()
        if not summary['after']['cooperation']['shared_motion_verified']:raise RuntimeError('Physical common motion was not verified')
        summary['lift_m']=summary['after']['objects']['acceptance_object']['position'][2]-summary['before']['objects']['acceptance_object']['position'][2]
        if not .006<=summary['lift_m']<=.010:raise RuntimeError('Measured bar lift outside approximately8mm range')
    else:
        state=summary['before'];targets={s:state[s]['qpos'].copy() for s in ['left','right']}
        arms=['left','right'] if value['arms']=='both' else [value['arms']]
        fixture_data=json.loads((ROOT/'config'/fixture).read_text())
        for side in arms:
            if scene=='timeout':targets[side][0]+=-.1 if side=='left' else .1
            else:targets[side]=fixture_data['targets'][side]
        if scene=='planning_rejected':targets[arms[0]][0]=100. # Original joint-limit refusal; no dispatch.
        update(status='planning');event('Planning frozen obstacle trajectory',arms=arms)
        plan=backend._call('POST','/plan/joint',{'epoch':state['epoch'],**targets,'participating_arms':arms});save('trajectory.json',plan)
        check();update(status='executing');event('Executing epoch-bound complete trajectory',plan_id=plan['plan_id'])
        receipt=backend._call('POST','/trajectory/actions',{'epoch':state['epoch'],'plan_id':plan['plan_id']})
        summary['action']=backend._wait_action(receipt);save('action.json',summary['action']);summary['after']=backend._state()
        summary['target_error_rad']=max(abs(summary['after'][s]['qpos'][i]-targets[s][i]) for s in arms for i in range(7))
        if summary['target_error_rad']>.03:raise RuntimeError('Physical target convergence failed')
    check();terminal='completed';event('Physical execution completed')
except BaseException as exc:
    error=type(exc).__name__+': '+str(exc);traceback.print_exc();save('failure.json',{'error':error,'traceback':traceback.format_exc()})
    terminal='cancelled' if interrupted.is_set() else 'failed'
    event('Operator interruption' if interrupted.is_set() else 'Execution or startup failed',error=error[:1500])
finally:
    update(status='finalizing',error=error[:1500] if error else None);event('Confirming both arms stopped and finalizing recording')
    if backend is not None:
        try:
            # Priority transport must bypass the cancellation/deadline check.
            backend._transport=lambda method,path,body,timeout:http(method,path,body,timeout,priority=True)
            summary['final_state']=backend._transport('GET','/state',None,5)
            summary['stop']=backend.stop();update(stop=summary['stop']);save('stop.json',summary['stop'])
            if not summary['stop'].get('physical_rest_confirmed'):
                terminal='failed';error=(error or '')+' Physical rest of both arms was not confirmed'
            # Let the existing no-action holding loop reach the next native
            #10Hz record sample, so saved media includes the terminal state.
            required_step=summary['stop'].get('stopped_state',summary['final_state'])['total_steps']
            coverage_deadline=time.monotonic()+3
            while time.monotonic()<coverage_deadline:
                if (out/'measured.json').exists() and json.loads((out/'measured.json').read_text())['total_steps']>=required_step:break
                time.sleep(.03)
            summary['recording_terminal_step']=required_step
            summary['recording_covers_terminal']=((out/'measured.json').exists() and json.loads((out/'measured.json').read_text())['total_steps']>=required_step)
            if not summary['recording_covers_terminal']:
                terminal='failed';error=(error or '')+' Recording did not capture terminal physical state'
            summary['actions']=backend.action_receipts;summary['plans']=backend.plan_query_receipts
            save('sdk-actions.json',summary['actions']);save('sdk-plans.json',summary['plans'])
            # Recover all terminal receipts, including interrupted/failed actions.
            if summary['final_state'].get('actions'):save('actions-final.json',summary['final_state']['actions'])
        except Exception as exc:
            terminal='failed';error=(error or '')+' Stop barrier failed: '+str(exc)
    reaped=[]
    for name,proc,stream in reversed(children):
        if proc.poll() is None:
            proc.terminate()
            try:proc.wait(18)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
        reaped.append({'name':name,'pid':proc.pid,'returncode':proc.returncode,'reaped':proc.poll() is not None});stream.close()
    save('reap.json',reaped)
    video=out/'physics/simulation.mp4';recording='missing'
    if video.is_file() and video.stat().st_size>0:
        probe=subprocess.run(['ffprobe','-v','error','-count_frames','-show_streams','-of','json',str(video)],capture_output=True,text=True)
        save('video-probe.json',json.loads(probe.stdout) if probe.returncode==0 else {'error':probe.stderr})
        recording='saved' if probe.returncode==0 else 'failed'
    closed.set();watch.join(12)
    if interrupted.is_set() and terminal=='completed':terminal='cancelled'
    summary.update(status=terminal,error=error,recording=recording,reaped=reaped);save('summary.json',summary)
    update(status=terminal,error=error[:1500] if error else None,phase={'completed':'Completed','failed':'Failed','cancelled':'Cancelled'}[terminal],recording=recording,
           result={k:summary[k] for k in ['lift_m','target_error_rad'] if k in summary},finished_at=time.time())
    http_log.close()
