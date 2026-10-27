"""Operator spec mailbox and isolated worker channel using the ordinary worker."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from attention_memory_service.positive_controls import validate_spec, LABEL
from .core.dev_store import DevConflict
from .dev_v2_loop import checked, immutable


def queue(root, path):
    root=Path(root).resolve();path=Path(path).resolve()
    if not path.is_relative_to(root):raise DevConflict('Positive-control spec must be inside worker state directory')
    spec=json.loads(path.read_bytes());sha=validate_spec(spec)
    target=root/'positive-control-queue'/(spec['control_id']+'.json')
    target.parent.mkdir(parents=True,exist_ok=True)
    immutable(target,dict(spec=spec,spec_sha256=sha))
    return 'Positive control queued: '+spec['title']+' · Test · not counted in results'


def local_origin(provision,spec):
    """Locate only canonical, original experiment ledgers (including reference runner)."""
    from .memory_origin_evidence import OriginalStore
    from .memory_validation_provision import read
    from .memory_provisional import derive_signature
    root=provision.root;o=spec['origin'];matches=[]
    paths=set(root.rglob('ledger.sqlite'))|set(root.rglob('ledger.sqlite3'))|set(root.rglob('store.sqlite'))
    for path in sorted(paths):
        if path.resolve()!=path:raise DevConflict('Original ledger path is not canonical')
        with OriginalStore(path).tx() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_experiments'").fetchone():continue
            row=db.execute('SELECT payload FROM v2_experiments WHERE id=?',(o['experiment_id'],)).fetchone()
            if not row:continue
            e=json.loads(row[0]);m=read(e['manifest'])
            if Path(m['ledger_path']).resolve()==path:matches.append((path,e,m))
    if len(matches)!=1:raise DevConflict('Positive-control original experiment missing or ambiguous')
    path,e,m=matches[0];store=OriginalStore(path);c=store.cell(o['cell'])
    if c['experiment_id']!=o['experiment_id'] or m['experiment_id']!=o['experiment_id']:raise DevConflict('Positive-control cell/experiment mismatch')
    versions=[v for v in c['versions'] if v['version']==o['version']]
    jobs=[j for j in c['jobs'] if j['version']==o['version'] and j['kind']=='execute' and j['state']=='settled']
    if len(versions)!=1 or len(jobs)!=1:raise DevConflict('Positive-control settled version/execution missing or ambiguous')
    v=versions[0];job=jobs[0];source=checked(v['source']);feedback=read(job['receipt']['public_feedback']);bundle=read(job['receipt']['bundle'])
    config_ref=dict(uri=str(Path(c['artifact_dir'])/'config.json'),sha256=v['config_sha256']);config=read(config_ref)
    if (bundle['native_success'] is not False or bundle['policy_sha256']!=v['source']['sha256']
        or bundle['config_sha256']!=config_ref['sha256'] or m['configs'].get(f"{c['task_id']}:{c['seed']}")!=config):raise DevConflict('Positive-control origin execution/config mismatch')
    from .dev_planner_memory import context
    ctx=context(config,spec['context']['namespace'])
    if ctx!=spec['context']:raise DevConflict('Positive-control spec context differs from frozen origin (including execution_profile)')
    # The reference runner seals a ScriptedClient model answer. No Advisor is
    # required: source acceptance is the actual failed execution, not advice.
    model=[j for j in c['jobs'] if j['version']==o['version'] and j['kind']=='model']
    if model:
        if len(model)!=1 or model[0]['state']!='settled':raise DevConflict('Original Dev generation is not settled')
        receipt=model[0]['receipt'];output=read(receipt['response'])
        from .memory_validation_generation import parse_generation
        code,_,error=parse_generation(output['content'])
        if error or code.encode()!=source.read_bytes() or v.get('model_input')!=receipt['request'] or v.get('model_output')!=receipt['response']:raise DevConflict('Origin source differs from sealed Dev output')
        checked(receipt['request']);checked(receipt['provider_response'])
    signature=derive_signature(feedback)
    p=dict(channel='positive_control',record_kind='positive_control',artifact=dict(applicability=ctx),failure_signature=signature,
        source_policy_sha256=v['source']['sha256'],origin=dict(experiment_id=o['experiment_id'],cell=o['cell'],origin_version=o['version'],
        policy_id=c['policy_id'],digests=dict(origin_source=v['source']['sha256'],origin_public_feedback=job['receipt']['public_feedback']['sha256'])))
    key='positive-origin-'+hashlib.sha256(json.dumps([spec,p],sort_keys=True).encode()).hexdigest()[:32]
    note=dict(memory_id=spec['control_id'],source_summary=dict(o))
    return dict(key=key,note=note,provenance=p,store=store,cell=c,version=v,job=job,experiment=e,config=config,request_id=None)


def resolve(provision,note):
    """Recheck original and replay seals before allocating ordinary Dev arms."""
    from .memory_validation_provision import read
    found=local_origin(provision,provision.client.spec);catalogs=provision.catalogs(note)
    key=found['key'];cid=key+'-executor';catalog=catalogs[cid]
    candidates=[]
    for name in catalog['files']:
        if not name.endswith('descriptor.json'):continue
        d=read(dict(uri=str(Path(catalog['root'])/name),sha256=catalog['files'][name]))
        if d.get('cell')==found['cell']['key'] and d.get('version')==found['version']['version'] and 'execute_job_id' in d:candidates.append((name,d))
    if len(candidates)!=1:raise DevConflict('Positive-control execution descriptor missing or ambiguous')
    name,d=candidates[0];v=found['version'];job=found['job']
    if read(d['version_record'])!=v or d['manifest']!=found['experiment']['manifest'] or d['execute_job_id']!=job['job_id']:raise DevConflict('Positive-control original differs from replay export')
    import sqlite3
    with sqlite3.connect(checked(d['ledger']).as_uri()+'?mode=ro',uri=True) as db:
        raw=db.execute('SELECT payload,state FROM v2_jobs WHERE id=?',(job['job_id'],)).fetchone()
        if not raw or dict(json.loads(raw[0]),state=raw[1])!=job:raise DevConflict('Positive-control execution changed since replay')
    if key+'-validator' not in catalogs:raise DevConflict('Positive-control independent origin validator missing')
    return dict(sealed=dict(manifest=d['manifest'],config=d['config'],source=v['source'],cell=d['cell'],version=d['version']),
        config=found['config'],feedback=job['receipt']['public_feedback'],signature=found['provenance']['failure_signature'],
        context=d['context'],policy_id=found['cell']['policy_id'],catalogs=catalogs,
        failed_execution=dict(import_id=key,catalog_id=cid,descriptor=name),source_import=None,
        positive_control=dict(spec=provision.client.spec,spec_sha256=validate_spec(provision.client.spec),
                              execution_import_id=key,catalog_id=key+'-validator',descriptor='verdict.json'))


class ControlClient:
    """Importer transport adapter; no source import, trust or online use methods."""
    def __init__(self,client,spec,provision):self.client,self.spec,self.provision=client,spec,provision
    def read(self,action):
        if hasattr(self.client,'positive_control_read'):return self.client.positive_control_read(self.spec['control_id'],action)
        return self.client.positive_controls.read(self.spec['control_id'],action)
    def write(self,action,**payload):
        if hasattr(self.client,'positive_control_write'):return self.client.positive_control_write(action,**payload)
        return self.client.positive_controls.write(action,payload)
    def register_dev_v2_catalog(self,**kw):return self.client.register_dev_v2_catalog(**kw)
    def import_dev_v2_execution(self,**kw):return self.client.import_dev_v2_execution(**kw)
    def import_dev_v2_trial(self,**kw):return self.write('trials',**kw)
    def create_positive_control(self,**kw):return self.write('create',**kw)
    def provenance(self,mid):
        if mid!=self.spec['control_id']:raise DevConflict('Control adapter cannot read other notes')
        return local_origin(self.provision,self.spec)['provenance']
    def get_memory(self,mid):return SimpleNamespace(**self.read('input'))
    def get_plan(self,mid):return self.read('plan')
    def record_plan(self,mid,plan):return self.write('plans',memory_id=mid,plan=plan)
    def record_dev_v2_pair(self,**kw):return self.write('pairs',**kw)
    def record_dev_v2_failed_check(self,**kw):return self.write('failed-checks',**kw)
    def authorize_dev_v2_validation(self,**kw):return self.write('grants',**kw)
    def decision(self,mid):return self.read('decision')
    def evaluate(self,mid):return self.write('evaluate',memory_id=mid)
    def note(self):
        return dict(memory_id=self.spec['control_id'],version=1,uses=0,failure_gone=0,created_at=0,
            record_kind='positive_control',title=self.spec['title'],source_summary=self.spec['origin'],
            source_seed=self.spec['context']['seed'],cross_seed_targets=self.spec['cross_seed_targets'])
    def validation_queue(self):return [] if self.read('decision') else [self.note()]
    def cross_seed_queue(self):
        if (self.read('decision') or {}).get('decision')!='would_pass':return []
        status=self.read('cross-status')
        missing=status['task_trust']['missing']
        if not missing or 'No neutral seed showed harm' in missing:return []
        tested={s['target_seed'] for s in status['seeds']}
        if all(t['seed'] in tested and next(s for s in status['seeds'] if s['target_seed']==t['seed']).get('verdict') for t in self.spec['cross_seed_targets']):return []
        return [dict(self.note(),**{k:status[k] for k in ('seeds','task_trust')})]
    def cross_seed_status(self,*,memory_id):return self.read('cross-status')
    def cross_seed_write(self,route,**kw):return self.write('cross-'+route,**kw)
    def memory_events(self,**kw):return []


SHARED={'day','runs_today','day_start_quota','day_start_quota_resets_at','counted','weekly_quota','weekly_quota_at',
        'weekly_quota_resets_at','quota_refreshes','ignored_stale_runs','enabled'}


def tick_channel(state,deps,ordinary_tick):
    """One channel at a time, one lock and one global quota/run ledger."""
    root=Path(deps.root);active=state.get('positive_control_active')
    if not active and (state.get('current') or state.get('pending') or state.get('cross_pending') or state.get('cross_waiting')):return False
    entries=[]
    for path in sorted((root/'positive-control-queue').glob('*.json')):
        entry=json.loads(path.read_bytes());spec=entry['spec']
        if validate_spec(spec)!=entry['spec_sha256']:raise DevConflict('Queued positive-control spec changed')
        entries.append(entry)
    if not entries:return False
    from .memory_validation_worker import settings
    config=settings(root)
    if not config['enabled'] and not active:
        state['status']='Off';deps.save(state);return True
    cross=config.get('cross_seed',{}).get('enabled',False)
    records=state.setdefault('positive_controls',{})
    entry=next((e for e in entries if e['spec']['control_id']==active),None)
    if entry is None:
        entry=next((e for e in entries if not records.get(e['spec']['control_id'],{}).get('same_done') or
                    cross and not records.get(e['spec']['control_id'],{}).get('cross_done')),None)
    if entry is None:return False
    mid=entry['spec']['control_id'];state['positive_control_active']=mid
    record=records.setdefault(mid,dict(title=entry['spec']['title'],label=LABEL))
    sub=record.setdefault('worker',{});sub.update({k:state[k] for k in SHARED if k in state})
    # Adapter is cached only for this process; all identity is durable in specs/receipts.
    cache=getattr(deps,'positive_adapters',None)
    if cache is None:cache={};deps.positive_adapters=cache
    if mid not in cache:
        from .memory_validation_provision import Provisioner
        from .memory_validation_runtime import LedgerRunner
        original=deps.runner
        provision=Provisioner(dict(original.provision.config),clock=original.provision.clock)
        provision.config.pop('evidence_catalogs',None)
        client=ControlClient(deps.client,entry['spec'],provision);provision.client=client
        runner=LedgerRunner(client,provision,experiments_root=original.experiments_root,validator_root=original.validator_root,
            state_root=original.state_root,replay=original.replay,provider=original.provider,
            runner_factory=original.runner_factory,gpu_meter=original.gpu_meter)
        cache[mid]=(client,runner)
    client,runner=cache[mid]
    def save(value):
        record['worker']={k:v for k,v in value.items() if k not in SHARED}
        state.update({k:v for k,v in value.items() if k in SHARED})
        state['status']='Test · not counted in results: '+value.get('status','Queued')
        deps.save(state)
    scoped=SimpleNamespace(**vars(deps));scoped.client=client;scoped.runner=runner;scoped.save=save
    ordinary_tick(sub,scoped)
    if getattr(scoped,'begin_cycle',None):deps.begin_cycle=scoped.begin_cycle
    try:
        decision=client.read('decision');record['verdict']=decision
        if client.read('record'):record['pairs']=client.read('pairs')
        record.pop('error',None)
        if decision:
            record['same_done']=True;record['cross_seed']=client.read('cross-status')
            record['cross_done']=decision['decision']!='would_pass' or not client.cross_seed_queue()
            if not sub.get('current') and not sub.get('cross_waiting') and (record['cross_done'] or not cross):state.pop('positive_control_active',None)
        if sub.get('service_gaps'):record['error']=sub['service_gaps'][-1]['reason']
        if sub.get('origin_failures') or sub.get('service_gaps') or any(r.get('status')=='inconclusive' for r in sub.get('results',[])):
            record.update(same_done=True,cross_done=True)
            state.pop('positive_control_active',None)
    except Exception as exc:
        from .notices import safe_detail
        record['error']=safe_detail(str(exc))
    save(sub)
    return True
