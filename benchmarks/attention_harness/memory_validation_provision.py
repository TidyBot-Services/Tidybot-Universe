"""Default offline provisioning: sealed originals in, ten bounded Dev arms out.

The ten cells are immutable logical arm slots in the service plan. After a
proven invalid generation, memory_validation_replacements authorizes at most
two additional paired attempts in those slots; initial manifests stay frozen.

Catalog references are the output of dev_v2_memory_import.prepare, sealed by the
worker origin phase or supplied as an explicit runtime override. Originals and
their ledgers are read-only.
"""
import hashlib,json,math,re,sqlite3,time
from datetime import datetime
from pathlib import Path
from .core.dev_store import DevConflict
from .dev_v2_loop import checked,job_for
from .dev_ledger import DevV2Store
from .memory_provisional import derive_signature


def read(ref):return json.loads(checked(ref).read_bytes())

def target_config(origin):
    config=read(origin['config'])
    target=origin.get('cross_seed')
    if target:
        config=dict(config,seed=target['seed'],scene_contract=target['contract'])
        # Seed aliases checked by validate_dev_config, not new settings.
        for field in ('scene_reset_seed','global_random_seed'):
            if field in config:config[field]=target['seed']
    return config


def arm_count(origin):
    return origin.get('cross_seed',{}).get('runs',10)


def target_config_text(origin):
    """Replace only top-level seed/contract values, preserving all other bytes."""
    raw=checked(origin['config']).read_text()
    if 'cross_seed' not in origin:return raw
    value=target_config(origin);decoder=json.JSONDecoder();pos=raw.index('{')+1;changes=[]
    while True:
        while raw[pos].isspace() or raw[pos]==',':pos+=1
        if raw[pos]=='}':break
        key,pos=decoder.raw_decode(raw,pos)
        while raw[pos].isspace() or raw[pos]==':':pos+=1
        start=pos;old,pos=decoder.raw_decode(raw,pos)
        if key in {'seed','scene_reset_seed','global_random_seed','scene_contract'} and old!=value[key]:
            changes.append((start,pos,json.dumps(value[key],sort_keys=True,ensure_ascii=False,allow_nan=False)))
    for start,end,text in reversed(changes):raw=raw[:start]+text+raw[end:]
    return raw


def validate_creation(selection,origin):
    """Additional checks for the private validation branch of experiment.create."""
    if set(origin)-{'manifest','config','source','cell','version','cross_seed'} or not {'manifest','config','source','cell','version'}<=set(origin):raise DevConflict('complete sealed validation origin required')
    manifest=read(origin['manifest']);config=read(origin['config']);checked(origin['source'])
    cell=next((c for c in manifest['cells'] if c['key']==origin['cell']),None)
    if not cell or manifest['configs'].get(f"{cell['task_id']}:{cell['seed']}")!=config:raise DevConflict('origin frozen configuration differs')
    count=arm_count(origin)
    if 'cross_seed' in origin:
        target=origin['cross_seed']
        if set(target)!={'seed','contract','runs'} or type(target['seed']) is not int or target['seed']==config['seed'] or type(count) is not int or count not in (1,6,10):raise DevConflict('exact cross-seed probe or paired batch required')
        from openarm365_dev_v2.scene_contract import load_contract,seed_entry,SCHEMA_V2
        contract=load_contract(target['contract'])
        if contract['schema']!=SCHEMA_V2:raise DevConflict('seed-keyed target contract required')
        seed_entry(contract,config['task_id'],target['seed'])
        config=target_config(origin);cell=dict(cell,seed=target['seed'])
    expected=dict(tasks=[cell['task_id']],seeds=[cell['seed']],policies=[cell['policy_id']],budget_tier='dev',model_source='codex',run_notes=False,memory_family=False,help_budget=0,mode='diagnostic',target_depth=1,target_valid_executions=1,max_versions=1,reproducible_reset=True,scene_contract=config.get('scene_contract'),attempt_wall_seconds=config['attempt_wall_seconds'])
    if any(selection.get(k)!=v for k,v in expected.items()) or not config.get('scene_contract') or config.get('budget_tier')!='dev':raise DevConflict('validation requires ten clean dev arms on the frozen scene')
    if any(selection.get(k) for k in ('memory_authority','planner_features','human_proxy')) or any(selection.get(k,{}).get('enabled') for k in ('advisor_proxy','eval_agent')):raise DevConflict('validation cannot acquire Memory or extra assistance')
    from .dev_configuration import normalize_configuration,derive_cell_budget
    budget=derive_cell_budget(normalize_configuration(selection))
    if selection.get('cell_budget',budget)!=budget or selection.get('goal_limits',{k:count*v for k,v in budget.items()})!={k:count*v for k,v in budget.items()}:raise DevConflict('exact validation resource allocation required')
    # The original native config is retained even if its original Dev provider was
    # PARCC. The new experiment's generation provider is independently Codex.
    from openarm365_dev_v2.dev_config import validate_dev_config,verify_lock
    epoch=json.loads(Path(manifest['identity_lock']['path']).read_bytes())['source_root']
    verify_lock(manifest['identity_lock'],expected_source_root=epoch)
    validate_dev_config(config,expected_source_root=epoch)


class Provisioner:
    def __init__(self,config,client=None,*,clock=time.time):
        self.config=config;self.client=client;self.clock=clock
        self.root=Path(config['experiments_root']).resolve()
        self.state_root=Path(config['state_root']).resolve()

    def catalogs(self,note=None):
        refs=self.config.get('evidence_catalogs')
        if not refs and note is not None:
            from .memory_origin_evidence import receipt_catalogs
            refs=receipt_catalogs(self,note)
        if not refs:raise DevConflict('Origin import catalogs are not configured')
        catalogs={cid:read(ref) for cid,ref in refs.items()}
        from .memory_source_catalogs import verify_source_link,registration_order
        for cid,catalog in catalogs.items():
            root=Path(catalog['root']).resolve()
            for name,digest in catalog['files'].items():
                if name in catalog.get('links',{}):
                    verify_source_link(catalogs,cid,name,digest);continue
                path=root/name
                if not path.resolve().is_relative_to(root):raise DevConflict('Origin catalog path escapes its root')
                checked(dict(uri=str(path),sha256=digest))
        return registration_order(catalogs)

    def resolve(self,note):
        if getattr(self.client,'spec',None) is not None:
            from .memory_positive_control import resolve
            return resolve(self,note)
        if self.client is None:raise DevConflict('Memory provenance reader is unavailable')
        provenance=self.client.provenance(note['memory_id']);origin=provenance.get('origin',{})
        summary=note['source_summary'];catalogs=self.catalogs(note)
        if any(origin.get(k)!=summary[k] for k in ('experiment_id','cell')) or origin.get('origin_version')!=summary['version']:raise DevConflict('Note source summary differs from its sealed provenance')
        candidates=[]
        for cid,catalog in catalogs.items():
            if catalog['role']!='executor':continue
            for name,digest in catalog['files'].items():
                if not name.endswith('.json'):continue
                data=read(dict(uri=str(Path(catalog['root'])/name),sha256=digest))
                if isinstance(data,dict) and data.get('schema_version')=='attentionbench.dev-v2-import.v1' and data.get('cell')==summary['cell']:
                    if read(data['manifest'])['experiment_id']==summary['experiment_id']:candidates.append((cid,name,data))
        failed=[v for v in candidates if v[2]['version']==summary['version'] and 'execute_job_id' in v[2] and 'failed_execution_import_id' not in v[2]]
        sources=[v for v in candidates if v[2]['version']==origin.get('help_version') and 'failed_execution_import_id' in v[2]]
        if len(failed)!=1 or len(sources)!=1:raise DevConflict('Original failed-execution and source import descriptors are missing or ambiguous')
        fc,fn,fd=failed[0];sc,sn,sd=sources[0];manifest=read(fd['manifest']);config=read(fd['config'])
        ledger=Path(catalogs[fc].get('ledger_origin','')).resolve()
        if not ledger.is_file() or not ledger.is_relative_to(self.root) or Path(manifest['ledger_path']).resolve()!=ledger:raise DevConflict('Original ledger is outside the configured experiments root or missing')
        def row(db,table,where,args):
            rows=db.execute('SELECT payload FROM '+table+' WHERE '+where,args).fetchall()
            if len(rows)!=1:raise DevConflict('Original ledger record missing or ambiguous: '+table)
            return json.loads(rows[0][0])
        with sqlite3.connect(ledger.as_uri()+'?mode=ro',uri=True) as db:
            cell=row(db,'v2_cells','id=?',(summary['cell'],))
            experiment=row(db,'v2_experiments','id=?',(summary['experiment_id'],))
            version=row(db,'v2_versions','cell=? AND version=?',(summary['cell'],summary['version']))
            job=row(db,'v2_jobs',"cell=? AND version=? AND kind='execute' AND state='settled'",(summary['cell'],summary['version']))
            source_version=row(db,'v2_versions','cell=? AND version=?',(summary['cell'],origin['help_version']))
        if cell['experiment_id']!=summary['experiment_id'] or experiment['manifest']!=fd['manifest'] or version!=read(fd['version_record']) or source_version!=read(sd['version_record']):raise DevConflict('Original ledger differs from sealed import evidence')
        if sd['manifest']!=fd['manifest'] or sd['config']!=fd['config'] or job['job_id']!=fd['execute_job_id']:raise DevConflict('Origin import bindings differ')
        # Compare the settled receipt itself against the exported read-only snapshot.
        snapshot=checked(fd['ledger'])
        with sqlite3.connect(snapshot.as_uri()+'?mode=ro',uri=True) as db:
            if row(db,'v2_jobs','id=?',(job['job_id'],))!=job:raise DevConflict('Original settled execution differs from import snapshot')
        feedback=job['receipt']['public_feedback'];bundle=read(job['receipt']['bundle']);source=version['source']
        checked(source);public=read(feedback);signature=provenance['failure_signature'];digests=origin['digests']
        if source['sha256']!=digests['origin_source'] or feedback['sha256']!=digests['origin_public_feedback'] or derive_signature(public)!=signature:raise DevConflict('Origin source, feedback or failure signature differs from the note')
        if origin['policy_id']!=cell['policy_id']:raise DevConflict('Origin policy differs from the note')
        for name,field in (('help','help'),('help_model_input','model_input'),('help_source','source')):
            checked(source_version[field])
            if source_version[field]['sha256']!=digests[name]:raise DevConflict('Source help evidence differs from the note: '+name)
        if version['config_sha256']!=fd['config']['sha256'] or bundle['config_sha256']!=fd['config']['sha256'] or bundle['policy_sha256']!=source['sha256']:raise DevConflict('Origin execution binding differs')
        if sd['validator_id'] not in catalogs:raise DevConflict('Independent source validator catalog is missing')
        from .memory_source_catalogs import manifest_sources,registration_order
        catalogs.update(manifest_sources(fd['manifest'],fd['context'],self.root,contract=config.get('scene_contract')))
        sealed=dict(manifest=fd['manifest'],config=fd['config'],source=source,cell=summary['cell'],version=summary['version'])
        return dict(sealed=sealed,config=config,feedback=feedback,signature=signature,context=fd['context'],policy_id=cell['policy_id'],catalogs=registration_order(catalogs),
                    failed_execution=dict(import_id=sd['failed_execution_import_id'],catalog_id=fc,descriptor=fn),source_import=dict(import_id='source-'+hashlib.sha256((sc+sn).encode()).hexdigest()[:24],catalog_id=sc,descriptor=sn))

    def __call__(self,note,operation):
        if not re.fullmatch('[A-Za-z0-9_.-]{8,100}',operation):raise DevConflict('Stable validation operation id required')
        resolved=self.resolve(note)  # Also recheck originals on every retry.
        config=resolved['config'];origin=resolved['sealed']
        selection=dict(request_key='validation-'+operation,tasks=[config['task_id']],seeds=[config['seed']],policies=[resolved['policy_id']],mode='diagnostic',budget_tier='dev',model_source='codex',help_budget=0,run_notes=False,memory_family=False,target_depth=1,target_valid_executions=1,max_versions=1,reproducible_reset=True,scene_contract=config['scene_contract'],attempt_wall_seconds=config['attempt_wall_seconds'])
        from .dev_configuration import normalize_configuration,derive_cell_budget
        budget=derive_cell_budget(normalize_configuration(selection))
        selection['wall_limit']=10*(budget['model_seconds']+budget['execution_seconds'])
        validate_creation(selection,origin)
        exp_id='devexp-'+hashlib.sha256(selection['request_key'].encode()).hexdigest()[:24]
        ledger=self.root/exp_id/'ledger.sqlite3'
        if not ledger.exists():
            state_path=self.state_root/'state.json'
            state=json.loads(state_path.read_bytes()) if state_path.exists() else {}
            today=datetime.fromtimestamp(self.clock()).astimezone().date().isoformat()
            used=state.get('runs_today',0) if state.get('day')==today else 0
            from .memory_validation_worker import daily_run_stop
            if type(used) is not int or used<0 or daily_run_stop({'runs_today':used},self.state_root,needed=10):raise DevConflict('Fewer than 10 automatic checks remain today')
        from .dev_experiment import create
        from .memory_validation_ownership import record_experiment
        store=DevV2Store(ledger);experiment=create(store,self.root,selection,validation_origin=origin)
        record_experiment(self.state_root,operation,exp_id)
        pairs=[dict(pair_id=str(i),**{a+'_cell':next(c['key'] for c in experiment['cells'] if c['validation_pair']==i and c['validation_arm']==a) for a in ('control','treatment')}) for i in range(5)]
        return dict(experiment_id=exp_id,memory_id=note['memory_id'] if resolved.get('positive_control') else 'validation-'+operation,origin=origin['source'],origin_feedback=resolved['feedback'],config=origin['config'],context=resolved['context'],failure_signature=resolved['signature'],policy_id=resolved['policy_id'],dev_model=dict(provider='codex',model='gpt-6-astra'),budget=dict(assistance_credits=0),source_catalogs=resolved['catalogs'],failed_execution=resolved['failed_execution'],source_import=resolved['source_import'],**({'positive_control':resolved['positive_control']} if resolved.get('positive_control') else {}),pairs=pairs,cells={c['key']:dict(ledger=str(ledger)) for c in experiment['cells']})

    @staticmethod
    def gpu_seconds(store,cell,version=1):
        """Only accept independently measured, sealed, per-execution evidence."""
        job=job_for(store.cell(cell),version,'execute')
        if not job or job.get('state')!='settled':raise DevConflict('GPU measurement requires a settled execution')
        ref=job['receipt'].get('gpu_measurement')
        if not ref:raise DevConflict('Independent GPU time measurement is unavailable for this arm')
        measured=read(ref);bundle=read(job['receipt']['bundle'])
        if measured.get('source')!='harness_gpu_meter' or measured.get('cell')!=cell or measured.get('version')!=version or measured.get('bundle_sha256')!=job['receipt']['bundle']['sha256'] or measured.get('attempt_id')!=bundle['attempt_id']:raise DevConflict('GPU measurement is not bound to this execution')
        value=measured.get('gpu_seconds')
        if type(value) not in (int,float) or not math.isfinite(value) or value<0 or type(measured.get('samples')) is not int or measured['samples']<=0:raise DevConflict('Independent GPU time measurement is unavailable for this arm')
        return value


def provision_factory(config):return Provisioner(config)
