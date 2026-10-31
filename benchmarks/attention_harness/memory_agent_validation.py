"""Restricted agent arms of the offline single-scene validation plan."""
import hashlib,json
from pathlib import Path
from .core.dev_store import DevConflict
from .dev_v2_loop import checked,immutable

ARM_BUDGET=dict(formal_versions=1,moving_executions=5,read_only_probes=60,eval_runs=4,help=0,
    trial_runs=4,sdk_calls_per_execution=320,control_steps_per_execution=9600,
    execution_wall_seconds=1920,active_wall_seconds=1920,model_turns=None,tokens=None)


MODEL_FIELDS=('dev_model','dev_reasoning_effort','eval_model')
ENVELOPE_SCHEMA='attentionbench.agent-validation-envelope.v1'
ARM_FIELDS=('batch_id','arm_id','pair_index','arm_role','note_id','memory_id')
EXECUTION_FIELDS=(*MODEL_FIELDS,'agent_policy_version')


def mismatch(item):
    raise DevConflict('agent validation envelope mismatch: '+item)


def require_match(condition,item):
    if not condition:mismatch(item)


def evidence(ref,item):
    try:return json.loads(checked(ref).read_bytes())
    except (OSError,ValueError,TypeError,KeyError,DevConflict) as exc:
        raise DevConflict('agent validation envelope mismatch: '+item) from exc


def seal_worker_arms(batch,note_id,directory):
    """Worker-owned authority, sealed before any arm's Dev session is started."""
    from .dev_ledger import DevV2Store
    refs={}
    for index,pair in enumerate(batch['pairs']):
        for role in ('control','treatment'):
            key=pair[role+'_cell'];store=DevV2Store(batch['cells'][key]['ledger'])
            with store.tx() as db:
                cell=store.get(db,'cells',key)
                experiment=store.get(db,'experiments',cell['experiment_id'])
            manifest=evidence(experiment['manifest'],'experiment')
            validate_manifest(manifest)
            require_match(manifest['experiment_id']==batch['experiment_id'],'batch_id')
            frozen_cell=next(c for c in manifest['cells'] if c['key']==key)
            require_match(frozen_cell['validation_pair']==index,'pair_index')
            require_match(frozen_cell['validation_arm']==role,'arm_role')
            require_match(evidence(batch['config'],'worker_config')==manifest['configs'][f"{cell['task_id']}:{cell['seed']}"],'config')
            settings={k:manifest[k] for k in MODEL_FIELDS}
            settings['agent_policy_version']=origin_policy_version(manifest['validation_origin'])
            require_match(batch['dev_model']==dict(provider='codex',model=settings['dev_model']),'dev_model')
            for field in ('dev_reasoning_effort','eval_model'):
                require_match(batch[field]==settings[field],field)
            value=dict(schema_version='attentionbench.agent-validation-arm.v1',
                batch_id=batch['experiment_id'],arm_id=key,pair_index=index,arm_role=role,
                note_id=note_id,memory_id=batch['memory_id'],experiment=experiment['manifest'],
                config=batch['config'],agent_configuration=settings)
            refs[key]=immutable(Path(directory)/(key+'.json'),value)
            store.update_cell(key,agent_validation_arm=refs[key])
    return refs


def verify_runner_envelope(envelope,request,config):
    """Fail closed with an item name, including malformed evidence/ledgers."""
    import sqlite3
    try:_verify_runner_envelope(envelope,request,config)
    except DevConflict as exc:
        if str(exc).startswith('agent validation envelope mismatch: '):raise
        raise DevConflict('agent validation envelope mismatch: evidence') from exc
    except KeyError as exc:mismatch(str(exc.args[0]))
    except sqlite3.Error as exc:
        raise DevConflict('agent validation envelope mismatch: ledger') from exc
    except (OSError,ValueError,TypeError,AttributeError) as exc:
        raise DevConflict('agent validation envelope mismatch: schema') from exc


def _verify_runner_envelope(envelope,request,config):
    import sqlite3
    from .dev_memory_validation import policy_input
    require_match(isinstance(envelope,dict),'schema')
    require_match(set(envelope)=={'schema_version','experiment','input','grant','guidance',*ARM_FIELDS},'schema')
    require_match(envelope['schema_version']==ENVELOPE_SCHEMA,'schema_version')
    # This reference comes from the worker via the trusted request, never from
    # the envelope. Compare its identity fields before trusting any envelope ref.
    arm_ref=getattr(request,'worker_arm',None)
    arm=evidence(arm_ref,'worker_arm')
    require_match(arm['schema_version']=='attentionbench.agent-validation-arm.v1','worker_arm.schema_version')
    for field in ARM_FIELDS:
        require_match(type(envelope[field]) is type(arm[field]) and envelope[field]==arm[field],field)
    require_match(type(arm['pair_index']) is int and 0<=arm['pair_index']<5,'pair_index')
    require_match(arm['arm_role'] in ('control','treatment'),'arm_role')
    require_match(request.run_id=='run:'+arm['arm_id'],'arm_id')
    require_match(request.attempt_id==f"attempt:{arm['arm_id']}:0",'version')
    require_match(envelope['experiment']==arm['experiment'],'experiment')
    manifest=evidence(arm['experiment'],'experiment')
    require_match(manifest['experiment_id']==arm['batch_id'],'batch_id')
    try:
        frozen_models=origin_models(manifest['validation_origin'])
        frozen_models['agent_policy_version']=origin_policy_version(manifest['validation_origin'])
    except (ValueError,KeyError,TypeError,DevConflict) as exc:
        raise DevConflict('agent validation envelope mismatch: validation_origin') from exc
    running=getattr(request,'agent_configuration',None)
    require_match(isinstance(running,dict) and set(running)==set(EXECUTION_FIELDS),'agent_configuration')
    for field in EXECUTION_FIELDS:
        expected=frozen_models[field]
        require_match(manifest.get(field,'agent-policy-v1' if field=='agent_policy_version' else None)==expected,field)
        require_match(arm['agent_configuration'].get(field)==expected,field)
        require_match(type(running[field]) is str and running[field]==expected,field)
        # Models are controller config, not fields in the frozen simulator JSON.
        # Its epoch is still checked explicitly, including the legacy v1 default.
        if field=='agent_policy_version' or field in config:
            require_match(config.get(field,'agent-policy-v1')==expected,field)
    require_match(manifest.get('agent_budget')==ARM_BUDGET and all(type(manifest['agent_budget'].get(k)) is type(v) for k,v in ARM_BUDGET.items()),'agent_budget')
    require_match(len(manifest['cells'])==10,'cells')
    require_match(all(c.get('excluded_from_results') is True for c in manifest['cells']),'excluded_from_results')
    require_match(manifest.get('schedule')==[dict(cell=c['key'],through_version=1) for c in manifest['cells']],'schedule')
    require_match(manifest.get('agent_validation')==manifest['validation_origin'],'validation_origin')
    require_match(type(manifest['selection'].get('help_budget')) is int and manifest['selection']['help_budget']==0,'help_budget')
    try:validate_manifest(manifest)
    except (ValueError,KeyError,TypeError) as exc:
        item='memory_visibility' if 'visibility' in str(exc) else 'selection'
        raise DevConflict('agent validation envelope mismatch: '+item) from exc
    grant=evidence(envelope['grant'],'grant');binding=grant['binding']
    require_match(binding['cell']==arm['arm_id'],'arm_id')
    require_match(binding['memory_id']==arm['memory_id'],'memory_id')
    cell=next((c for c in manifest['cells'] if c['key']==arm['arm_id']),None)
    require_match(cell is not None,'arm_id')
    require_match(cell['validation_pair']==arm['pair_index'],'pair_index')
    require_match(cell['validation_arm']==arm['arm_role'],'arm_role')
    rows=(manifest,manifest['selection'],*manifest['cells'])
    for field,expected in (('schema','attentionbench.dev-experiment.v3'),('formal_eligible',False),
            ('validation_experiment',True),('dev_mode','agent')):
        require_match(type(manifest.get(field)) is type(expected) and manifest[field]==expected,field)
    for field,expected in (('dev_mode','agent'),('budget_tier','dev'),('runner_boundary','dev_loop')):
        require_match(config.get(field)==expected,field)
    require_match(manifest['configs'].get(f"{request.task_id}:{request.seed}")==config,'config')
    for field in ('task_id','seed'):require_match(cell[field]==getattr(request,field),field)
    require_match(request.suite=='robocasa365','suite')
    require_match(type(binding['version']) is int and binding['version']==1,'version')
    require_match(len({c['key'] for c in manifest['cells']})==10,'cells')
    require_match({(c['validation_pair'],c['validation_arm']) for c in manifest['cells']}=={(i,a) for i in range(5) for a in ('control','treatment')},'pairs')
    require_match(all(c.get('dev_mode','agent')=='agent' and all(c[f]==cell[f] for f in ('task_id','seed','policy_id')) for c in manifest['cells']),'cells')
    for field in ('run_notes','memory_family','memory_authority','planner_features','human_proxy','positive_control','fork'):
        require_match(not any(row.get(field) for row in rows),field)
    for field in ('advisor_proxy','eval_agent'):
        require_match(not any(row.get(field,{}).get('enabled') for row in (*rows,config)),field)
    require_match(grant.get('schema_version')=='attentionbench.dev-v2-validation-grant.v1','grant.schema_version')
    require_match(binding.get('origin_source_sha256')==manifest['validation_origin']['source']['sha256'],'origin_source_sha256')
    require_match(binding.get('dev_model')==dict(provider='codex',model=manifest['dev_model']),'dev_model')
    require_match(binding['exposure']==('candidate_validation' if arm['arm_role']=='treatment' else 'none'),'arm_role')
    require_match('target_seed' not in binding,'target_seed')
    for field in ('policy_sha256','config_sha256','seed'):
        require_match(type(binding[field]) is type(getattr(request,field)) and binding[field]==getattr(request,field),field)
    for path,digest,field in ((request.policy_code_path,request.policy_sha256,'policy_sha256'),(request.config_path,request.config_sha256,'config_sha256')):
        try:raw=path.read_bytes()
        except OSError as exc:
            raise DevConflict('agent validation envelope mismatch: '+field) from exc
        require_match(hashlib.sha256(raw).hexdigest()==digest,field)
    evidence(arm['config'],'worker_config')
    require_match(arm['config']['sha256']==request.config_sha256,'config_sha256')
    evidence(envelope['input'],'validation_input')
    require_match((isinstance(envelope['guidance'],str) and bool(envelope['guidance'].strip()))
        if arm['arm_role']=='treatment' else envelope['guidance'] is None,'guidance')
    try:policy_input(envelope,request)
    except (DevConflict,KeyError,ValueError,TypeError) as exc:
        raise DevConflict('agent validation envelope mismatch: validation_input') from exc
    if envelope['guidance'] is not None:
        require_match(hashlib.sha256(envelope['guidance'].encode()).hexdigest()==binding['guidance_sha256'],'guidance')
    with sqlite3.connect(Path(manifest['ledger_path']).resolve().as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute('SELECT payload FROM v2_experiments WHERE id=?',(manifest['experiment_id'],)).fetchone()
        cell_row=db.execute('SELECT payload FROM v2_cells WHERE id=?',(binding['cell'],)).fetchone()
        dispatches=[(at,json.loads(p)) for at,p in db.execute("SELECT at,payload FROM v2_events WHERE kind='memory_validation_dispatch'")]
        envelopes=[(at,json.loads(p)) for at,p in db.execute("SELECT at,payload FROM v2_events WHERE kind='agent_validation_runner_dispatch'")]
        deliveries=[json.loads(p) for p, in db.execute("SELECT payload FROM v2_events WHERE kind='agent_validation_memory'")]
    runtime_cell=json.loads(cell_row[0]) if cell_row else {}
    require_match(row is not None and json.loads(row[0]).get('manifest')==arm['experiment'],'experiment')
    require_match(runtime_cell.get('agent_validation_arm')==arm_ref,'worker_arm')
    for field,expected in (('experiment_id',manifest['experiment_id']),('dev_mode','agent'),('validation_experiment',True),('excluded_from_results',True)):
        require_match(type(runtime_cell.get(field)) is type(expected) and runtime_cell[field]==expected,field)
    verify_delivered_packet(deliveries,runtime_cell,arm,binding,envelope['guidance'])
    matches=[]
    for at,payload in dispatches:
        if payload.get('cell')!=binding['cell'] or payload.get('version')!=1:continue
        try:source=checked(payload['source']).read_bytes()
        except (OSError,DevConflict,ValueError,KeyError,TypeError) as exc:
            raise DevConflict('agent validation envelope mismatch: policy_sha256') from exc
        require_match(hashlib.sha256(source).hexdigest()==request.policy_sha256,'policy_sha256')
        evidence(payload['config'],'config_sha256')
        expected=dict(cell=binding['cell'],version=1,validation_input=envelope['input'],validation_grant_id=grant['grant_id'],
            source=dict(uri=payload['source']['uri'],sha256=request.policy_sha256),
            config=dict(uri=payload['config']['uri'],sha256=request.config_sha256))
        if payload==expected and grant['issued_at']<=at:
            matches.append(payload)
    sealed=dict(cell=binding['cell'],version=1,envelope=envelope)
    require_match(len(matches)==1,'dispatch')
    require_match(len([at for at,p in envelopes if p==sealed and grant['issued_at']<=at])==1,'envelope_seal')


def verify_delivered_packet(deliveries,cell,arm,binding,guidance):
    from .dev_agent_workspace import read_beneath
    from .dev_agent_eval import feedback
    from .dev_v2_loop import prompt_memory_view
    rows=[p for p in deliveries if p.get('cell')==arm['arm_id'] and p.get('version')==1]
    require_match(len(rows)==1,'memory_delivery')
    delivery=rows[0];packet=evidence(delivery['memory_input'],'memory_packet')
    entries=packet.get('entries')
    require_match(isinstance(entries,list) and len(entries)==(1 if arm['arm_role']=='treatment' else 0),'memory_packet.entries')
    if entries:
        entry=entries[0]
        for field,expected in (('memory_id',arm['memory_id']),('version',binding['memory_version']),
                ('content',guidance),('development_id',arm['arm_id']+':v1'),
                ('source_policy_sha256',binding['origin_source_sha256']),('unvalidated',True),
                ('excluded_from_results',True),('trusted_here',False)):
            require_match(type(entry.get(field)) is type(expected) and entry[field]==expected,'memory_packet.'+field)
        require_match(set(entry)=={'memory_id','version','content','development_id','source_policy_sha256','unvalidated','excluded_from_results','trusted_here'},'memory_packet.entries')
    require_match(packet==dict(schema_version='attentionbench.memory-input.v1',entries=entries,excluded_from_results=True),'memory_packet')
    exposure=evidence(delivery['dev_memory_exposure'],'memory_exposure')
    expected_entries=[dict(memory_id=arm['memory_id'],memory_version=binding['memory_version'],guidance_sha256=binding['guidance_sha256'])] if entries else []
    require_match(exposure==dict(schema_version='attentionbench.dev-memory-exposure.v1',cell=arm['arm_id'],version=1,model_input=delivery['memory_input'],entries=expected_entries),'memory_exposure')
    expected=feedback(prompt_memory_view(dict(memory=entries)))
    base=Path(cell['artifact_dir'])/'agent'
    for root,item in ((base/'private/public','memory_packet.canonical'),(base/'ws','memory_packet.delivered')):
        try:actual=json.loads(read_beneath(root,'memory/v1.json',150000,shared=True))
        except (OSError,ValueError,PermissionError) as exc:
            raise DevConflict('agent validation envelope mismatch: '+item) from exc
        require_match(actual==expected,item)


def origin_models(origin):
    """Read explicit model choices only from the digest-checked origin manifest."""
    from .dev_agent_config import MODELS
    manifest=json.loads(checked(origin['manifest']).read_bytes())
    models={k:manifest.get(k) for k in MODEL_FIELDS}
    if (manifest.get('dev_mode')!='agent' or any(type(v) is not str for v in models.values())
            or models['dev_model'] not in MODELS or models['eval_model'] not in MODELS
            or models['dev_reasoning_effort'] not in ('low','medium','high')):
        raise ValueError('unsupported agent validation origin models')
    return models


def origin_policy_version(origin):
    """Legacy origins have the v1 epoch; new arms never use creation defaults."""
    if not isinstance(origin,dict) or not isinstance(origin.get('manifest'),dict):
        raise ValueError('agent_memory_or_control_not_supported')
    manifest=json.loads(checked(origin['manifest']).read_bytes())
    version=manifest.get('agent_policy_version','agent-policy-v1')
    if manifest.get('dev_mode')!='agent' or version not in ('agent-policy-v1','agent-policy-v2'):
        raise ValueError('unsupported agent validation origin policy version')
    return version


def origin_visibility(origin):
    from .dev_agent_policies import memory_exposed
    manifest=json.loads(checked(origin['manifest']).read_bytes())
    cell=next(c for c in manifest['cells'] if c['key']==origin['cell'])
    if origin_policy_version(origin)=='agent-policy-v2' and not memory_exposed(cell['policy_id']):
        return 'validation_override'
    return None


def validate_options(selection,origin):
    if not isinstance(origin,dict) or set(origin)!={'manifest','config','source','cell','version'}:
        raise ValueError('agent_memory_or_control_not_supported')
    manifest=json.loads(checked(origin['manifest']).read_bytes());config=json.loads(checked(origin['config']).read_bytes())
    if manifest.get('dev_mode')!='agent' or config.get('dev_mode')!='agent':raise ValueError('mixed agent validation is unsupported')
    if selection.get('agent_policy_version','agent-policy-v1')!=origin_policy_version(origin):
        raise ValueError('agent validation policy version differs from origin')
    expected=dict(mode='diagnostic',help_budget=0,run_notes=False,memory_family=False,target_depth=1,
        target_valid_executions=1,max_versions=1,reproducible_reset=True,attempt_wall_seconds=1920,
        **origin_models(origin))
    if any(selection.get(k)!=v or type(selection.get(k)) is not type(v) for k,v in expected.items()) or config.get('attempt_wall_seconds')!=1920:
        raise ValueError('exact agent validation arm required')
    if any(selection.get(k) for k in ('memory_authority','planner_features','human_proxy','positive_control','fork')) or selection.get('advisor_proxy',{}).get('enabled') or selection.get('eval_agent',{}).get('enabled'):
        raise ValueError('agent validation cannot acquire extra assistance')
    if selection.get('agent_validation',origin)!=origin:raise ValueError('agent validation origin differs')
    defaults=dict(coverage='budget_limited',startup_cleanup_seconds=60,model_reserve_seconds=360,max_input_bytes_per_request=150000)
    if any(selection.get(k,v)!=v for k,v in defaults.items()):raise ValueError('exact agent validation resource allocation required')


def validate_manifest(manifest):
    origin=manifest['validation_origin'];validate_options(manifest['selection'],origin)
    version=origin_policy_version(origin)
    rows=[manifest,*manifest['configs'].values(),*manifest['cells']]
    visibility=origin_visibility(origin)
    if any((row.get('memory_visibility')!=visibility or ('memory_visibility' in row)!=(visibility is not None)) for row in (manifest,*manifest['cells'])) or any('memory_visibility' in row for row in manifest['configs'].values()):
        raise ValueError('agent validation memory visibility differs from origin')
    if any(row.get('agent_policy_version','agent-policy-v1')!=version for row in rows):
        raise ValueError('agent validation policy version differs from origin')
    if any(manifest.get(k)!=v for k,v in origin_models(origin).items()):raise ValueError('agent validation models differ from origin')
    if (manifest.get('agent_validation')!=origin or manifest.get('agent_budget')!=ARM_BUDGET
            or any(type(manifest['agent_budget'].get(k)) is not type(v) for k,v in ARM_BUDGET.items())
            or manifest.get('schedule')!=[dict(cell=c['key'],through_version=1) for c in manifest['cells']]
            or len(manifest['cells'])!=10 or any(c.get('excluded_from_results') is not True for c in manifest['cells'])):raise ValueError('agent validation frozen budget/schedule differs')


def shared(ref):
    from .dev_agent_workspace import read_beneath
    path=checked(ref)
    data=read_beneath(path.parent,path.name,32*1024*1024,shared=True)
    if hashlib.sha256(data).hexdigest()!=ref['sha256']:raise DevConflict('agent validation origin digest differs')
    return data


def seed(workspace,current):
    from .dev_agent_workspace import atomic_write
    source=shared(current['origin']);feedback=json.loads(shared(current['origin_feedback']))
    private=workspace.private/'validation';private.mkdir(parents=True,exist_ok=True)
    immutable(private/'parent-source.py',source.decode())
    immutable(private/'origin-feedback.json',feedback)
    if not (workspace.canonical/'origin/public-feedback.json').exists():
        atomic_write(workspace.ws,'work/main.py',source)
        workspace.publish('origin/policy.py',source)
        workspace.publish('origin/public-feedback.json',feedback)


class Delivery:
    def __init__(self,memory,current,client,*,arm_record):
        self.memory,self.current,self.client=memory,current,client
        self.arm_record=arm_record
        self.arm=evidence(arm_record,'worker_arm')
        require_match(self.arm['arm_id']==memory.key,'arm_id')
        require_match(self.arm['arm_role']==current['arm'],'arm_role')
        require_match(self.arm['memory_id']==current['memory_id'],'memory_id')
        self.note=client.get_memory(current['memory_id']);self.plan=current['plan']
        require_match(self.note.memory_id==self.arm['memory_id'],'memory_id')
        self.version=self.note.version;self.guidance=self.note.guidance

    def recheck(self,packet=None):
        note=self.client.get_memory(self.current['memory_id'])
        if (getattr(note.status,'value',note.status)!='candidate' or note.version!=self.version
                or note.guidance!=self.guidance or self.client.get_plan(note.memory_id)!=self.plan
                or self.client.decision(note.memory_id) is not None):raise PermissionError('validation candidate changed')
        if packet is not None and packet!=self.packet:raise PermissionError('validation packet changed')
        if hasattr(self,'input'):
            if json.loads(checked(self.input).read_bytes())!=self.packet:raise PermissionError('validation input changed')
            checked(self.exposure)

    def load(self,number,previous,directory):
        self.recheck()
        if number!=1 or hashlib.sha256(previous.encode()).hexdigest()!=self.current['origin']['sha256']:
            raise PermissionError('agent validation has one origin generation')
        entries=[]
        if self.current['arm']=='treatment':
            entries=[dict(memory_id=self.note.memory_id,version=self.version,content=self.guidance,
                development_id=f'{self.memory.key}:v1',source_policy_sha256=self.current['origin']['sha256'],
                unvalidated=True,excluded_from_results=True,trusted_here=False)]
        self.packet=dict(schema_version='attentionbench.memory-input.v1',entries=entries,excluded_from_results=True)
        self.input=immutable(directory/'memory-input.json',self.packet)
        self.exposure=immutable(directory/'dev-memory-exposure.json',dict(schema_version='attentionbench.dev-memory-exposure.v1',
            cell=self.memory.key,version=1,model_input=self.input,entries=[dict(memory_id=e['memory_id'],
                memory_version=e['version'],guidance_sha256=hashlib.sha256(e['content'].encode()).hexdigest()) for e in entries]))
        with self.memory.store.tx() as db:
            payload=dict(cell=self.memory.key,version=1,memory_input=self.input,dev_memory_exposure=self.exposure)
            rows=[json.loads(p) for p, in db.execute("SELECT payload FROM v2_events WHERE kind='agent_validation_memory'")]
            prior=[p for p in rows if p['cell']==self.memory.key]
            if prior and prior!=[payload]:raise DevConflict('agent validation delivery differs')
            if not prior:self.memory.store.event(db,'agent_validation_memory',payload)
        return self.packet,self

    def prepare(self,response):
        self.recheck(self.memory.packet)
        if response['version_id']!='v0001':raise PermissionError('agent validation formal version exceeded')
        key=self.memory.key;store=self.memory.store;config=self.memory.config
        source=dict(uri=str(self.memory.workspace.private/'sources'/(response['request_id']+'.py')))
        source['sha256']=hashlib.sha256(Path(source['uri']).read_bytes()).hexdigest()
        config_ref=immutable(Path(store.cell(key)['artifact_dir'])/'config.json',config)
        grant=self.client.authorize_dev_v2_validation(memory_id=self.note.memory_id,cell=key,version=1,seed=config['seed'],
            context=self.current['context'],policy_sha256=source['sha256'],config_sha256=config_ref['sha256'],
            exposure='candidate_validation' if self.current['arm']=='treatment' else 'none',origin_source_sha256=self.current['origin']['sha256'])
        directory=Path(store.cell(key)['artifact_dir'])/'v1'
        value=dict(memory_ids=[self.note.memory_id] if self.packet['entries'] else [],exposure=grant['binding']['exposure'],
            context=self.current['context'],policy_sha256=source['sha256'],config_sha256=config_ref['sha256'],validation_grant_id=grant['grant_id'])
        envelope=dict(input=immutable(directory/'memory-validation-input.json',value),
            grant=immutable(directory/'memory-validation-grant.json',grant),guidance=self.guidance if self.packet['entries'] else None)
        with store.tx() as db:
            experiment=store.get(db,'experiments',store.get(db,'cells',key)['experiment_id'])
        envelope.update(schema_version=ENVELOPE_SCHEMA,experiment=experiment['manifest'])
        envelope.update({field:self.arm[field] for field in ARM_FIELDS})
        immutable(directory/'memory-validation-envelope.json',envelope)
        with store.tx() as db:
            store.event(db,'memory_validation_dispatch',dict(cell=key,version=1,validation_input=envelope['input'],
                validation_grant_id=grant['grant_id'],source=source,config=config_ref))
            store.event(db,'agent_validation_runner_dispatch',dict(cell=key,version=1,envelope=envelope))
        self.envelope=envelope
        return envelope


def invalid_arm(runner,current,store,key,cell):
    import sqlite3
    from .dev_experiment import verify_manifest
    with store.tx() as db:experiment=store.get(db,'experiments',cell['experiment_id'])
    manifest=verify_manifest(experiment)
    out=Path(experiment['artifact_dir'])/'invalid-import'/key;out.mkdir(parents=True,exist_ok=True)
    result_path=out/'result.json'
    if result_path.exists():return json.loads(result_path.read_bytes())
    marker=out/'catalog.json'
    if marker.exists():catalog=json.loads(marker.read_bytes())
    else:
        backup=out/'ledger.sqlite'
        with sqlite3.connect(store.path.as_uri()+'?mode=ro',uri=True) as source,sqlite3.connect(backup) as dest:source.backup(dest)
        ledger=dict(uri=str(backup),sha256=hashlib.sha256(backup.read_bytes()).hexdigest())
        config=immutable(out/'config.json',manifest['configs'][f"{cell['task_id']}:{cell['seed']}"])
        immutable(out/'descriptor.json',dict(cell=key,ledger=ledger,manifest=experiment['manifest'],config=config,reason=cell['reason']))
        root=Path(experiment['artifact_dir'])
        refs=[ledger,config,experiment['manifest'],dict(uri=str(out/'descriptor.json'),sha256=hashlib.sha256((out/'descriptor.json').read_bytes()).hexdigest())]
        catalog=dict(root=str(root),role='executor',context=current['context'],ledger_origin=str(store.path),
            files={str(Path(ref['uri']).relative_to(root)):ref['sha256'] for ref in refs})
        immutable(marker,catalog)
    eid='invalid-'+key;runner.client.register_dev_v2_catalog(catalog_id=eid,catalog=catalog)
    raw=json.loads((out/'descriptor.json').read_bytes())
    envelope=Path(cell['artifact_dir'])/'v1/memory-validation-envelope.json'
    if envelope.exists():grant=json.loads(checked(json.loads(envelope.read_bytes())['grant']).read_bytes())
    else:
        grant=runner.client.authorize_dev_v2_validation(memory_id=current['memory_id'],cell=key,version=1,
            seed=manifest['configs'][f"{cell['task_id']}:{cell['seed']}"]['seed'],context=current['context'],
            policy_sha256=current['origin']['sha256'],config_sha256=current['config']['sha256'],
            exposure='candidate_validation' if current['arm']=='treatment' else 'none',origin_source_sha256=current['origin']['sha256'])
    independent=runner.validator_root/('invalid-'+key);independent.mkdir(parents=True,exist_ok=True)
    descriptor=immutable(independent/'invalid.json',dict(schema_version='attentionbench.agent-validation-invalid.v1',
        execution_import_id=key,executor_catalog_id=eid,executor_descriptor=str((out/'descriptor.json').relative_to(catalog['root'])),
        ledger=raw['ledger'],manifest=raw['manifest'],config=raw['config'],cell=key,reason=raw['reason'],validation_grant_id=grant['grant_id']))
    validator=dict(root=str(independent),role='validator',context=current['context'],files={'invalid.json':descriptor['sha256']})
    runner.client.register_dev_v2_catalog(catalog_id='validator-'+eid,catalog=validator)
    result=dict(status='failed',descriptor=descriptor,checks=dict(native_evaluation=False),failed_check=dict(memory_id=current['memory_id'],
        validation_grant_id=grant['grant_id'],execution_import_id=key,catalog_id='validator-'+eid,descriptor='invalid.json'))
    immutable(result_path,result)
    return result
