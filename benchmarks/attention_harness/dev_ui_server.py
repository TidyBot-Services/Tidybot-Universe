"""Isolated Dev v2 operator UI/API; never restarts/deploys the daily UI."""
import argparse,json,secrets,re,subprocess,sys,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit,unquote
from .dev_ledger import DevV2Store,encoded
from .dev_loop import process_identity,sha,immutable
from .core.dev_store import DevConflict
from . import dev_experiment as experiments
from .dev_configuration import normalize_configuration,derive_cell_budget,configured_budget
from openarm365_dev_v2.dev_config import ROOT
UI=Path(__file__).with_name('dev_ui')

def agent_display(value):
    if value.get('dev_mode')=='agent' or value.get('selection',{}).get('dev_mode')=='agent':
        from .dev_agent_view import clean
        return clean(value)
    return value


def budget_preview(selection):
    """Show the same normalized worst-case budget that create validates; do not allocate anything."""
    if not isinstance(selection,dict):raise ValueError('selection object required')
    tasks,seeds,policies=(selection.get(key) for key in ('tasks','seeds','policies'))
    if any(not isinstance(values,list) or not values for values in (tasks,seeds,policies)):
        raise ValueError('tasks, seeds and policies required for preview')
    config=normalize_configuration(selection)
    from .free_models import eval_is_unmetered,is_unmetered_model
    from .dev_experiment import MODEL
    source=config.get('model_source','parcc')
    dev_model='gpt-6-astra' if source=='codex' else MODEL['model']
    required=derive_cell_budget(config)
    per_cell=configured_budget(config)
    cells=len(tasks)*len(seeds)*len(policies)
    wait_wall=config['max_versions']*config.get('planner_features',{}).get('human_wait_seconds',0)
    return dict(per_cell=per_cell,required_per_cell=required,cells=cells,
                eval_accounting='Recorded; no token cap' if source=='codex' else ('不计限额，仅记录' if config['eval_agent']['enabled'] and eval_is_unmetered(config['eval_agent']) else '计入限额'),
                model_token_accounting={role:('Recorded; no token cap' if source=='codex' else ('不计限额，仅记录' if is_unmetered_model(model) else '计入限额')) for role,model in {'dev':dev_model,'advisor':config['advisor_proxy']['model'],'eval':config['eval_agent']['model']}.items()},
                eval_safety_call_cap=max(2*config['max_versions'],10),
                total={key:value*cells for key,value in per_cell.items()},
                wall_seconds=config.get('wall_limit',cells*(per_cell['model_seconds']+per_cell['execution_seconds']+wait_wall)+3600),
                retry_k2_reachable=config['target_depth']>=3)

def live(store,exp_id,key):
    c=store.cell(key)
    if c['experiment_id']!=exp_id or c['state']!='running' or not c.get('active_episode'):raise FileNotFoundError('No in-flight live episode')
    ep=Path(c['active_episode']).resolve();root=Path(c['artifact_dir']).resolve()
    if not ep.is_relative_to(root):raise ValueError('episode outside registered cell')
    latest=ep/'live/latest.json'
    if not latest.exists():raise FileNotFoundError('Native live frame not available yet')
    row=json.loads(latest.read_bytes())
    if not re.fullmatch(r'frame-\d{6}\.jpg',row.get('file','')):raise ValueError('invalid frame identity')
    if not latest.resolve().is_relative_to(ep):raise ValueError('live metadata outside episode')
    frame=(ep/'live'/row['file']).resolve()
    if not frame.is_relative_to(ep):raise ValueError('live frame outside episode')
    if sha(frame)!=row['sha256']:raise ValueError('frame digest mismatch')
    return c,row,frame

def owner_of(request): return 'local'


def serve(store,artifact_root,port,budget_file=None,*,allow_custom=None,allow_private_hosts=(),vault=None):
    if budget_file:experiments.validate_budget_file(budget_file)
    from .dev_agent_provider import ProviderError,presets,resolve_provider,probe
    from .dev_agent_secret import Secret,scrub
    from .dev_agent_key_vault import ProviderKeyVault,launch_run_with_keys,keyless_env
    if allow_custom is None:allow_custom=__import__('os').getenv('ATTENTION_ALLOW_CUSTOM_PROVIDER_URL','').lower() in ('1','true','yes')
    vault=vault if vault is not None else ProviderKeyVault()
    def provider_spec(provider,base=None):
        return resolve_provider(provider,base,allow_custom=allow_custom,allow_private_hosts=allow_private_hosts)
    token=secrets.token_urlsafe(32);workers={};worker_lock=threading.Lock()
    from .dev_planner import PlannerControl
    def planner():
        with store.tx() as db:
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_planner_requests'").fetchone()
        if not exists:raise FileNotFoundError('no planner requests in this ledger')
        return PlannerControl(store)
    human_token=__import__('os').getenv('ATTENTION_HUMAN_APPROVAL_TOKEN','')
    if human_token and (len(human_token)<24 or human_token==token):raise ValueError('independent human capability must have at least 24 characters')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,data,content='application/json',status=200):
            raw=data.encode() if isinstance(data,str) else data
            self.send_response(status);self.send_header('Content-Type',content);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw)
        def obj(self,value,status=200):self.send(encoded(value),'application/json',status)
        def authorized(self):return secrets.compare_digest(self.headers.get('X-Attention-Token',''),token)
        def provider_error(self,exc,status=400,secret_values=()):
            message,redacted=scrub(str(exc),secret_values)
            self.obj(dict(error=dict(code=exc.code if isinstance(exc,ProviderError) else 'provider_request_invalid',
                                     message=message,redacted=redacted)),status)
        def provider_route(self):
            values=[]
            try:
                if not self.authorized():raise ProviderError('control_token_required','Control token required')
                path=urlsplit(self.path).path
                if urlsplit(self.path).query:raise ProviderError('provider_request_invalid','Query parameters are not accepted')
                size=int(self.headers.get('Content-Length',0))
                if not 0<=size<=8192:raise ProviderError('provider_body_invalid','Bounded JSON body required')
                body={}
                if self.command in ('PUT','POST'):
                    if self.headers.get('Content-Type','').split(';')[0]!='application/json' or not size:
                        raise ProviderError('provider_body_invalid','Bounded JSON body required')
                    try:body=json.loads(self.rfile.read(size))
                    except (ValueError,UnicodeError):raise ProviderError('provider_body_invalid','Invalid JSON body') from None
                    if not isinstance(body,dict):raise ProviderError('provider_body_invalid','JSON object required')
                    if isinstance(body.get('key'),str):values.append(Secret(body['key']))
                owner=owner_of(self)
                if path=='/api/provider/presets' and self.command=='GET':
                    self.obj([dict(id=p.id,label=p.label,needs_key=p.key_source=='user',base_url=p.chat_url.rsplit('/chat/completions',1)[0],
                                   suggested_models=p.suggested_models,capabilities=p.capabilities) for p in presets().values()]);return
                match=re.fullmatch(r'/api/provider/credentials(?:/([a-z0-9-]{1,32})(/test)?)?',path)
                if not match:raise ProviderError('provider_route_unknown','Unknown provider resource')
                name,test=match.groups()
                if self.command=='PUT' and not test:
                    if set(body)-{'provider','key','base_url'}:raise ProviderError('provider_body_invalid','Unexpected credential fields')
                    if not isinstance(body.get('provider'),str) or not isinstance(body.get('key'),str):
                        raise ProviderError('provider_body_invalid','Provider and key strings required')
                    name=name or body['provider'];spec=provider_spec(body['provider'],body.get('base_url'))
                    vault.put(owner,name,spec.id,values[0],base_url=body.get('base_url'))
                    row=next(r for r in vault.list(owner) if r['credential_id']==name)
                    self.obj(dict(ok=True,credential_id=name,provider=spec.id,key_set=True,expires_at=row['expires_at']));return
                if self.command=='GET' and not test:
                    rows=vault.list(owner)
                    if name is not None:rows=[r for r in rows if r['credential_id']==name]
                    self.obj(rows);return
                if self.command=='DELETE' and not test:
                    count=vault.clear(owner,name);self.obj(dict(ok=True,cleared=count));return
                if self.command=='POST' and test:
                    if set(body)-{'model','role'}:raise ProviderError('provider_body_invalid','Unexpected test fields')
                    model=body.get('model');role=body.get('role','dev')
                    if not isinstance(model,str) or not model.strip() or len(model)>128 or role not in ('dev','eval','advisor'):
                        raise ProviderError('provider_body_invalid','Valid model and role required')
                    row=next((r for r in vault.list(owner) if r['credential_id']==name),None)
                    if row is None:raise ProviderError('provider_key_missing','Missing or expired credential for role '+role)
                    keys=vault.take_for_run(owner,{'dev':dict(credential=name,provider=row['provider'],model=model)})
                    desc=keys.roles['dev'];secret=keys._keys[name];values.append(secret)
                    result=probe(provider_spec(desc['provider'],desc['base_url']),secret,model,role=role)
                    self.obj(dict(ok=result.ok,checks=result.checks,errors=result.errors));return
                raise ProviderError('provider_route_unknown','Unknown provider operation')
            except Exception as exc:
                self.provider_error(exc,403 if isinstance(exc,ProviderError) and exc.code=='control_token_required' else 400,values)
        def do_PUT(self):self.provider_route()
        def do_DELETE(self):self.provider_route()
        def do_GET(self):
            if urlsplit(self.path).path.startswith('/api/provider/'):return self.provider_route()
            path=unquote(urlsplit(self.path).path)
            try:
                if path in {'/','/ui/'}:
                    html=(UI/'index.html').read_text().replace('__CONTROL_TOKEN__',token);self.send(html,'text/html; charset=utf-8');return
                if path in {'/ui/app.js','/ui/view_model.js'}:self.send((UI/path.rsplit('/',1)[-1]).read_bytes(),'text/javascript');return
                if not self.authorized():self.obj({'error':'control token required'},403);return
                if path=='/api/dev/options':self.obj(experiments.options());return
                if path=='/api/dev/experiments':self.obj([agent_display(e) for e in experiments.list_experiments(store)]);return
                if path=='/api/dev/resources':
                    value=store.export_ledger()
                    if any(e.get('selection',{}).get('dev_mode')=='agent' for e in experiments.list_experiments(store)):
                        from .dev_agent_view import clean
                        value=clean(value)
                    self.obj(value);return
                match=re.fullmatch(r'/api/dev/experiments/(devexp-[a-f0-9]{24})(?:/(.*))?',path)
                if not match:raise FileNotFoundError('unknown resource')
                eid,suffix=match.groups()
                if not suffix:self.obj(agent_display(experiments.snapshot(store,eid)));return
                approval=re.fullmatch(r'approvals/(planner-[a-f0-9]{64})',suffix)
                if approval:
                    row=planner().get(approval[1])
                    if store.cell(row['cell'])['experiment_id']!=eid:raise FileNotFoundError('request outside experiment')
                    self.obj(row);return
                if suffix in {'report','export.json','export.csv'}:
                    from .dev_report import report,report_csv
                    exp=experiments.snapshot(store,eid)
                    if exp['selection'].get('dev_mode')=='agent':
                        from .dev_agent_view import projection
                        if suffix=='export.csv':raise FileNotFoundError('Use the agent JSON report')
                        self.obj(projection(store,exp['cells'][0],api='/api/dev'));return
                    value=report(store,eid)
                    if suffix=='export.csv':self.send(report_csv(value),'text/csv; charset=utf-8')
                    else:self.obj(value)
                    return
                cell=re.fullmatch(r'cells/([a-f0-9]{64})/(view|agent/view|agent/files/(.+)|live(?:/frame)?|files/(.+))',suffix)
                if not cell:raise FileNotFoundError('unknown cell resource')
                key,kind,agent_name,name=cell.groups();c=store.cell(key)
                if c['experiment_id']!=eid:raise FileNotFoundError('cell outside experiment')
                if c.get('dev_mode')=='agent':
                    from .dev_agent_view import projection,artifact
                    if kind in ('view','agent/view'):self.obj(projection(store,c,api='/api/dev'));return
                    if agent_name:
                        content,mime=artifact(c,agent_name)
                        if mime=='application/json':self.obj(content)
                        else:self.send(content,mime)
                        return
                    raise FileNotFoundError('Agent evidence requires the public agent view')
                if kind.startswith('agent/') or kind=='view':raise FileNotFoundError('Not an agent run')
                if kind.startswith('live'):
                    c,row,frame=live(store,eid,key)
                    if kind.endswith('/frame'):self.send(frame.read_bytes(),'image/jpeg')
                    else:self.obj({**row,'active_version':c['active_version']})
                    return
                directory=Path(c.get('artifact_dir','/missing')).resolve();file=(directory/name).resolve()
                if not file.is_relative_to(directory) or any(n.startswith('.') for n in file.relative_to(directory).parts) or not file.is_file():raise FileNotFoundError('cell file unavailable')
                allowed={'human-review-needed.json','human-decision-receipt.json','memory-input.json','memory-result.json','memory-candidate-proposal.json','advisor-independent-preparation.json','human-independent-preparation.json','candidate-public-feedback.json','session-lock.json','config.json','policy.py','diff.patch','public-feedback.json','bundle.json','version.json','decision.json','help.json','model-input.json','model-output.json','advisor-input.json','advisor-output.json','trace.json','native_result.json','sandbox_receipt.json','recording.json','simulation.mp4','advisor_replay.mp4'}
                if file.name not in allowed:raise FileNotFoundError('not public UI artifact')
                size=file.stat().st_size;start,end,status=0,size-1,200
                if self.headers.get('Range'):
                    rng=re.fullmatch(r'bytes=(\d+)-(\d*)',self.headers['Range'])
                    if not rng or int(rng[1])>=size:self.send(b'','application/octet-stream',416);return
                    start=int(rng[1]);end=min(int(rng[2]) if rng[2] else end,end)
                    if end<start:raise ValueError('invalid video range')
                    status=206
                content='video/mp4' if file.suffix=='.mp4' else 'application/json' if file.suffix=='.json' else 'text/plain; charset=utf-8'
                self.send_response(status);self.send_header('Content-Type',content);self.send_header('Content-Length',str(end-start+1));self.send_header('Cache-Control','no-store');self.send_header('Accept-Ranges','bytes')
                if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                self.end_headers()
                with file.open('rb') as f:
                    f.seek(start);left=end-start+1
                    while left:
                        chunk=f.read(min(left,262144));self.wfile.write(chunk);left-=len(chunk)
            except (FileNotFoundError,DevConflict) as exc:self.obj({'error':str(exc)},404)
            except (ValueError,KeyError,TypeError) as exc:self.obj({'error':str(exc)},400)
            except (BrokenPipeError,ConnectionResetError):pass
        def do_POST(self):
            if urlsplit(self.path).path.startswith('/api/provider/'):return self.provider_route()
            if not self.authorized():self.obj({'error':'control token required'},403);return
            try:
                size=int(self.headers.get('Content-Length',0))
                if self.headers.get('Content-Type','').split(';')[0]!='application/json' or not 0<size<=32768:raise ValueError('bounded JSON body required')
                body=json.loads(self.rfile.read(size));path=urlsplit(self.path).path
                if not isinstance(body,dict):raise ValueError('JSON object required')
                if path=='/api/dev/preview':self.obj(budget_preview(body));return
                if path=='/api/dev/experiments':
                    if 'agent_provider' in body:
                        from .dev_agent_config import normalize
                        body={**body,**normalize(body)}
                        known={r['credential_id']:r for r in vault.list(owner_of(self))}
                        for desc in body['agent_provider']['roles'].values():
                            if desc['provider']=='openai-compatible' and desc['credential'] in known:
                                desc['base_url_host']=known[desc['credential']]['base_url_host']
                    self.obj(agent_display(experiments.create(store,artifact_root,body)),201);return
                approval=re.fullmatch(r'/api/dev/experiments/(devexp-[a-f0-9]{24})/approvals/(planner-[a-f0-9]{64})/decision',path)
                if approval:
                    supplied=self.headers.get('X-Attention-Human-Token','')
                    if not human_token or not secrets.compare_digest(supplied,human_token):
                        self.obj({'error':'separate human approval capability required'},403);return
                    eid,rid=approval.groups();row=planner().get(rid)
                    if store.cell(row['cell'])['experiment_id']!=eid:raise ValueError('request outside experiment')
                    if set(body)!={'binding','approved','response_id'}:raise ValueError('exact binding and decision required')
                    self.obj(planner().reply(rid,scope=body['binding'],approved=body['approved'],actor='authenticated_human',response_id=body['response_id']));return
                match=re.fullmatch(r'/api/dev/experiments/(devexp-[a-f0-9]{24})/(run|cancel|switch)',path)
                if not match:raise FileNotFoundError('unknown command')
                eid,command=match.groups()
                if command=='cancel':
                    if body:raise ValueError('cancel body must be empty')
                    self.obj(experiments.cancel(store,eid));return
                if command=='switch':
                    if set(body)!={'cell','policy'}:raise ValueError('cell/policy required')
                    c=store.cell(body['cell'])
                    if c['experiment_id']!=eid:raise ValueError('cell outside experiment')
                    self.obj(experiments.switch(store,body['cell'],body['policy']));return
                e=experiments.snapshot(store,eid)
                if set(body)!={'max_cells'} or type(body['max_cells']) is not int or not 1<=body['max_cells']<=len(e['cells']):raise ValueError('explicit max_cells within manifest denominator required')
                e=experiments.snapshot(store,eid)
                if e['cancelled']:raise DevConflict('experiment cancelled')
                from .dev_disk import require_start
                require_start(artifact_root)
                manifest=experiments.verify_manifest(e)
                run_keys=None
                if 'agent_provider' in manifest:
                    roles=manifest['agent_provider']['roles']
                    run_keys=vault.take_for_run(owner_of(self),roles)
                    desc=run_keys.roles['dev'];secret=run_keys._keys[desc['credential']]
                    spec=provider_spec(desc['provider'],desc['base_url'])
                    if roles['dev'].get('base_url_host')!=urlsplit(spec.chat_url).hostname:
                        raise ProviderError('provider_mismatch','Credential host differs for role dev')
                    result=probe(spec,secret,desc['model'],role='dev')
                    if not result.ok:
                        self.obj(dict(error=dict(code='provider_preflight_failed',message='\n'.join(e['message'] for e in result.errors),
                            redacted=any(e['redacted'] for e in result.errors),role='dev'),checks=result.checks,errors=result.errors),409);return
                with worker_lock:
                    if any(p.poll() is None for p in workers.values()):raise DevConflict('serial UI worker already active')
                    if e['owner'] and experiments.owner_alive(e['owner']):raise DevConflict('experiment owner already active')
                    directory=Path(e['artifact_dir']);log=(directory/'scheduler.log').open('ab')
                    env=__import__('benchmarks.attention_harness.dev_capabilities',fromlist=['execution_environment']).execution_environment(ROOT,{'ATTENTION_PLANNER_UI_URL':f'http://127.0.0.1:{port}/ui/'})
                    try:
                        if run_keys is not None:
                            process=launch_run_with_keys([sys.executable,'-B','-u','-m','benchmarks.attention_harness.dev_experiment','run','--store',str(store.path),'--artifacts',str(artifact_root),'--experiment',eid,'--max-cells',str(body['max_cells']),*(['--budget-file',str(budget_file)] if 'goal_id' not in e else [])],run_keys,cwd=ROOT,env=keyless_env(env),stdout=log,stderr=log,start_new_session=True)
                        else:
                            process=subprocess.Popen([sys.executable,'-B','-u','-m','benchmarks.attention_harness.dev_experiment','run','--store',str(store.path),'--artifacts',str(artifact_root),'--experiment',eid,'--max-cells',str(body['max_cells']),*(['--budget-file',str(budget_file)] if 'goal_id' not in e else [])],cwd=ROOT,env=env,stdout=log,stderr=log,start_new_session=True)
                    finally:log.close()
                    workers[eid]=process;immutable(directory/f'scheduler-process-{process.pid}.json',process_identity(process.pid))
                self.obj({'experiment_id':eid,'worker_started':True},202)
            except ProviderError as exc:self.provider_error(exc,409)
            except DevConflict as exc:self.obj({'error':str(exc)},409)
            except FileNotFoundError as exc:self.obj({'error':str(exc)},404)
            except (ValueError,KeyError,TypeError) as exc:
                if str(exc)=='role_not_supported_yet':self.provider_error(ProviderError('role_not_supported_yet',str(exc)),409)
                else:self.obj({'error':str(exc)},400)
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)

def main():
    p=argparse.ArgumentParser();p.add_argument('--store',required=True);p.add_argument('--artifacts',required=True);p.add_argument('--port',type=int,required=True);p.add_argument('--budget-file');p.add_argument('--allow-custom-provider-url',action='store_true',default=None);a=p.parse_args()
    server=serve(DevV2Store(a.store),Path(a.artifacts),a.port,Path(a.budget_file) if a.budget_file else None,allow_custom=a.allow_custom_provider_url)
    import benchmarks,openarm365_dev_v2
    receipt={'process':process_identity(),'port':a.port,'cwd':str(ROOT),'benchmarks':benchmarks.__file__,'openarm365_dev_v2':openarm365_dev_v2.__file__,'argv':sys.argv}
    artifact=Path(a.artifacts);artifact.mkdir(parents=True,exist_ok=True);immutable(artifact/f'ui-instance-{__import__("os").getpid()}.json',receipt)
    server.serve_forever()
if __name__=='__main__':main()
