"""Continue from an attempt: a new interactive run whose v1 is written from an earlier attempt's code.

The source run is only read. A fork is excluded from comparison (interactive mode) and starts its own
attention and budget accounting; the earlier run's help and time stay with that run."""
import copy,hashlib,json,re
from pathlib import Path
from .dev_ledger import DevV2Store,encoded
from .dev_help import public_screen
from .core.dev_store import DevConflict

SCHEMA='attentionbench.dev-fork.v1'
CARRY=('code','feedback')
# Fields that decide which physical scene and task the code meets.
SCENE_FIELDS=('task_id','official_env','task_prompt','seed','robot','execution_arm','controller','layout_id','style_id','scene_id','object_set_id','base_mode')
GRASP_FIELDS=('contact_exempt_objects','planner_mode','ik_candidates','ik_max_joint_jump')
OPINION='\nEval agent verdict and objective reason for your previous attempt: '

def _ref(path):return {'uri':str(Path(path).resolve()),'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()}
def _checked(ref):
    from .dev_v2_loop import checked
    return checked(ref)
def _json(ref):return json.loads(_checked(ref).read_bytes())

def request_fields(value):
    """The selection's fork request as the UI sends it: which attempt, and what to carry."""
    if not isinstance(value,dict) or set(value)-{'experiment_id','cell','version','carry','record'} or not {'experiment_id','cell','version','carry'}<=set(value):raise ValueError('fork requires experiment_id, cell, version and carry')
    if not isinstance(value['experiment_id'],str) or not re.fullmatch(r'devexp-[a-f0-9]{24}',value['experiment_id']):raise ValueError('fork source experiment ID invalid')
    if not isinstance(value['cell'],str) or not re.fullmatch(r'[a-f0-9]{64}',value['cell']):raise ValueError('fork source cell invalid')
    if type(value['version']) is not int or value['version']<1:raise ValueError('fork source version must be a positive integer')
    if value['carry'] not in CARRY:raise ValueError('fork carry must be one of '+', '.join(CARRY))
    return value

class Source:
    """One settled attempt of an earlier daily run, read-only."""
    def __init__(self,root,experiment_id,cell_key,version):
        from . import dev_experiment as experiments
        self.root=Path(root);self.experiment_id=experiment_id
        path=self.root/experiment_id/'ledger.sqlite3'
        if not path.is_file():raise FileNotFoundError('source run not found')
        self.store=DevV2Store(path);self.experiment=experiments.snapshot(self.store,experiment_id)
        # Only the record's digest: the source's frozen code may differ from today's, which the checks report.
        _checked(self.experiment['manifest'])
        if self.experiment.get('validation_experiment'):raise ValueError('validation runs cannot be continued')
        self.cell=self.store.cell(cell_key)
        if self.cell['experiment_id']!=experiment_id:raise ValueError('source cell belongs to another run')
        self.versions={v['version']:v for v in self.cell['versions']}
        if version not in self.versions:raise ValueError(f'Attempt {version} does not exist in the source run')
        self.version=version;self.record=self.versions[version]
        self.directory=Path(self.cell['artifact_dir'])
        self.config_path=self.directory/'config.json'
        self.config=json.loads(self.config_path.read_bytes())
    def job(self,version,kind):
        from .dev_v2_loop import job_for
        return job_for(self.cell,version,kind)
    def executed(self,version):
        job=self.job(version,'execute');return bool(job and job['state']=='settled')
    def review(self,version):
        from .dev_v2_loop import eval_ref
        job=self.job(version,'eval')
        if not (job and job['state']=='settled' and eval_ref(job.get('receipt') or {})):return None
        value=_json(eval_ref(job['receipt']))
        return value if value.get('status')=='completed' else None
    def source_text(self):return _checked(self.record['source']).read_text()
    def system_prompt(self):
        """The SDK text the source attempt's AI was given (its system message without the review suffix)."""
        ref=self.record.get('model_input')
        if not ref:return None
        messages=_json(ref).get('messages') or []
        system=next((m['content'] for m in messages if m.get('role')=='system'),None)
        return system.split(OPINION,1)[0] if isinstance(system,str) else None
    def feedback(self):
        """What the source run's next version would have been told about this attempt."""
        from .dev_v2_loop import feedback_for
        truncated={**self.cell,'versions':[v for v in self.cell['versions'] if v['version']<=self.version],'jobs':[j for j in self.cell['jobs'] if j['version']<=self.version]}
        feedback,_,_,_=feedback_for(truncated)
        public_screen(feedback);return feedback
    def attempts(self):
        rows=[]
        for v in self.cell['versions']:
            review=self.review(v['version'])
            outcome='invalid' if not v['valid'] else 'not_executed' if not self.executed(v['version']) else 'succeeded' if review and review.get('passed') is True else 'unsuccessful'
            rows.append(dict(version=v['version'],valid=v['valid'],executed=self.executed(v['version']),outcome=outcome,
                reason=(review or {}).get('reason') or v.get('validation_error') or '',
                lines=len(_checked(v['source']).read_text().splitlines())))
        return rows
    def has_run_notes(self):
        from .memory_run_notes import events,PREFIX
        return any(kind==PREFIX+'created' and e.get('help_version',0)<=self.version for kind,e in events(self.store,self.cell['key']))

def _contract_entry(config):
    ref=config.get('scene_contract')
    if not ref:return None
    from openarm365_dev_v2.scene_contract import load_contract,seed_entry
    return seed_entry(load_contract(ref),config['task_id'],config.get('scene_reset_seed',config['seed']))

def _setting(value):return 'not set' if value is None else str(value)
def check(rows,level,check_id,title,body=''):rows.append(dict(id=check_id,level=level,title=title,body=body))

def checks(source_config,source_prompt,new_config,*,carry,executed,valid,has_run_notes=False):
    """Plain-language checks shown before creation and repeated on the real new config at creation.
    level: ok, warn or block. Any block refuses creation."""
    rows=[]
    if not valid:check(rows,'block','source_valid','This attempt\'s code never passed the validator.','Continue from an attempt whose code ran.')
    if carry=='feedback' and not executed:check(rows,'block','source_executed','This attempt has no execution result to carry over.','Choose "Code only", or another attempt.')
    scene=[k for k in SCENE_FIELDS if source_config.get(k)!=new_config.get(k)]
    if scene:check(rows,'block','scene','The task or scene differs from the source run: '+', '.join(scene)+'.','The code would run somewhere else, so this run cannot continue it.')
    else:
        try:old_entry,new_entry=_contract_entry(source_config),_contract_entry(new_config)
        except (ValueError,KeyError,OSError) as exc:check(rows,'block','scene_contract','The scene contract could not be read: '+str(exc)[:160]+'.')
        else:
            if old_entry is not None and new_entry is not None and encoded(old_entry)!=encoded(new_entry):
                check(rows,'block','scene_contract',f'The scene contract for seed {source_config["seed"]} changed after the source run.','The code would start from a different scene. Pick a run made after the change, or run again from scratch.')
            elif old_entry is not None and new_entry is None:
                check(rows,'block','scene_contract','The source run was checked against a scene contract that is no longer registered.')
            else:check(rows,'ok','scene',f'Scene and task match the source run (seed {source_config["seed"]}).')
    if source_prompt is None:check(rows,'warn','sdk','The SDK text of the source attempt was not recorded.','The code may call functions that changed.')
    else:
        from .dev_tasks import get_task
        current=get_task(new_config['task_id']).system_prompt(new_config)
        if current==source_prompt:check(rows,'ok','sdk','The SDK text the AI sees is the same as in the source attempt.')
        else:check(rows,'warn','sdk','The SDK text the AI sees changed after the source run.','The AI is told the current SDK; calls in the old code may need updating.')
    grasp=[k for k in GRASP_FIELDS if source_config.get(k)!=new_config.get(k)]
    if grasp:check(rows,'warn','grasp_settings','Grasp settings differ from the source run: '+', '.join(f'{k} {_setting(source_config.get(k))} → {_setting(new_config.get(k))}' for k in grasp)+'.','The code will run with the new settings and may behave differently.')
    if source_config.get('execution_settings')!=new_config.get('execution_settings'):
        check(rows,'warn','execution_settings','Execution settings differ from the source run: execution_settings '
            +str((source_config.get('execution_settings') or {}).get('profile','legacy'))+' → '
            +str((new_config.get('execution_settings') or {}).get('profile','legacy'))+'.',
            'The code will run with the new settings and may behave differently.')
    if has_run_notes:check(rows,'warn','run_notes','The source run\'s notes are not carried over.','Only code and feedback can be carried in this version.')
    check(rows,'warn','excluded','Excluded from comparison.','A run that continues earlier work reuses its attention and time, so it never enters result tables.')
    return rows

def preview_config(source_config):
    """The source config with today's frozen defaults: what a new run's config differs in without building a Dev identity lock."""
    from openarm365_dev_v2 import dev_config as d
    value=copy.deepcopy(source_config)
    # A fork is a new dev run with today's defaults, even for a legacy origin.
    value['budget_tier']='dev'
    value['execution_settings']=d.new_execution_settings().to_dict()
    value['planner_source']=dict(kind='dev-local',source_root=str(d.ROOT))
    value.update(contact_exempt_objects=d.DEFAULT_CONTACT_EXEMPT_OBJECTS,planner_mode=d.DEFAULT_PLANNER_MODE,ik_candidates=d.DEFAULT_IK_CANDIDATES,ik_max_joint_jump=d.DEFAULT_IK_MAX_JOINT_JUMP)
    if source_config.get('scene_contract'):
        from openarm365_dev_v2.scene_contract import registered_contract_ref
        try:value['scene_contract']=registered_contract_ref()
        except ValueError:value['scene_contract']=None
    return value

def preview(root,experiment_id,cell_key,version,carry='feedback'):
    """What the dialog shows: the source's attempts, the checks for the chosen one, and what v1's prompt will include."""
    if carry not in CARRY:raise ValueError('unknown carry')
    s=Source(root,experiment_id,cell_key,version)
    rows=checks(s.config,s.system_prompt(),preview_config(s.config),carry=carry,executed=s.executed(version),valid=s.record['valid'],has_run_notes=s.has_run_notes())
    lines=len(s.source_text().splitlines())
    includes=[f'policy.py from Attempt {version} ({lines} lines) as the starting code']
    if carry=='feedback':
        review=s.review(version)
        includes+=['the execution feedback of Attempt '+str(version)]+(['the reviewer\'s verdict and reason for Attempt '+str(version)] if review else [])
    else:includes.append('no earlier feedback or trace')
    e=s.experiment
    return dict(experiment_id=experiment_id,cell=cell_key,version=version,carry=carry,task_id=s.cell['task_id'],seed=s.cell['seed'],policy_id=s.cell['policy_id'],
        created_at=e.get('created_at'),attempts=s.attempts(),checks=rows,blocked=any(r['level']=='block' for r in rows),prompt_includes=includes,
        carry_options=[dict(id='code',available=s.record['valid']),dict(id='feedback',available=s.record['valid'] and s.executed(version))])

def materialize(root,directory,request):
    """Freeze the chosen attempt into the new run's folder; the creation subprocess reads only these files."""
    request_fields(request)
    s=Source(root,request['experiment_id'],request['cell'],request['version'])
    folder=Path(directory)/'fork'
    from .dev_v2_loop import immutable
    source=immutable(folder/'source.py',s.source_text())
    feedback=immutable(folder/'feedback.json',s.feedback()) if request['carry']=='feedback' else None
    prompt=s.system_prompt()
    record=dict(schema=SCHEMA,experiment_id=request['experiment_id'],cell=request['cell'],version=request['version'],carry=request['carry'],
        task_id=s.cell['task_id'],seed=s.cell['seed'],policy_id=s.cell['policy_id'],source=source,feedback=feedback,
        source_config=_ref(s.config_path),source_version=dict(s.record['source'],version=request['version']),source_manifest=s.experiment['manifest'],
        source_system_prompt=immutable(folder/'source-system-prompt.txt',prompt) if prompt is not None else None,
        executed=s.executed(request['version']),valid=s.record['valid'],has_run_notes=s.has_run_notes())
    ref=immutable(folder/'fork.json',record)
    return {k:request[k] for k in ('experiment_id','cell','version','carry')}|{'record':ref}

def load(selection_fork):
    """Creation side: the frozen record named by the selection, cross-checked against it."""
    request_fields(selection_fork)
    if 'record' not in selection_fork:raise ValueError('fork record missing: create the run through the daily Dev endpoint')
    record=_json(selection_fork['record'])
    if record.get('schema')!=SCHEMA or any(record.get(k)!=selection_fork[k] for k in ('experiment_id','cell','version','carry')):raise DevConflict('fork record differs from the request')
    return record

def apply(record,configs,sources,*,mode,tasks,seeds):
    """Bind a new run to its source attempt: v1 starts from that code, the scene reset is the source's, every check passes."""
    if mode!='interactive':raise ValueError('a run that continues an attempt is interactive (excluded from comparison)')
    if tasks!=[record['task_id']] or seeds!=[record['seed']]:raise ValueError('a continued run keeps the source task and seed')
    source_config=_json(record['source_config'])
    config=configs[f"{record['task_id']}:{record['seed']}"]
    if source_config.get('scene_contract'):
        # Same reset as the source: the registered contract must still describe the same seed.
        from openarm365_dev_v2.scene_contract import registered_contract_ref
        config['scene_contract']=registered_contract_ref();config['scene_reset_seed']=source_config.get('scene_reset_seed',source_config['seed'])
    prompt=_checked(record['source_system_prompt']).read_text() if record.get('source_system_prompt') else None
    rows=checks(source_config,prompt,config,carry=record['carry'],executed=record['executed'],valid=record['valid'],has_run_notes=record['has_run_notes'])
    blocked=[r['title'] for r in rows if r['level']=='block']
    if blocked:raise ValueError('cannot continue this attempt: '+' '.join(blocked))
    text=_checked(record['source']).read_text();public_screen(text)
    sources[record['task_id']]={'source':text,'sha256':record['source']['sha256']}
    return rows

def initial_feedback(manifest):
    """v1's carried public feedback, or None (no fork, or code only)."""
    fork=manifest.get('fork')
    if not fork or not fork.get('feedback'):return None
    value=_json(fork['feedback']);public_screen(value);return value
