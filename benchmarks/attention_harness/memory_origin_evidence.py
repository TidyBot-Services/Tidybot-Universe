"""Read-only origin export and worker-owned, once-only independent replay."""
import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .core.dev_store import DevConflict
from .dev_ledger import DevV2Store
from .dev_v2_loop import checked, immutable, job_for
from .memory_validation_provision import read


class OriginalStore(DevV2Store):
    """No schema initialization, recovery, expiry or writes on historical ledgers."""
    def __init__(self,path):self.path=Path(path).resolve()

    @contextmanager
    def tx(self):
        db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)
        try:
            db.execute('BEGIN');yield db
        finally:db.close()

    def planner_snapshot(self,cell):
        with self.tx() as db:
            return dict(requests=[dict(json.loads(p),request_id=key) for key,p in db.execute(
                'SELECT id,payload FROM v2_planner_requests WHERE cell=?',(cell,))])


def origin_note(provision,note):
    """Follow the immutable link shared by same-scene and cross-seed checks."""
    mid=note['memory_id'];seen=set()
    while mid:
        if mid in seen:raise DevConflict('Cyclic origin provenance')
        seen.add(mid);p=provision.client.provenance(mid)
        if p.get('origin'):
            o=p['origin'];summary=dict(experiment_id=o['experiment_id'],cell=o['cell'],version=o['origin_version'])
            if mid==note['memory_id'] and note.get('source_summary') and any(note['source_summary'].get(k)!=v for k,v in summary.items()):
                raise DevConflict('Note source summary differs from its sealed provenance')
            return dict(memory_id=mid,source_summary=summary),p
        mid=p.get('from_provisional')
    raise DevConflict('Automatic origin preparation requires sealed provisional provenance; configure explicit catalogs for legacy notes')


def identity(provision,note):
    original,p=origin_note(provision,note)
    key='origin-'+hashlib.sha256(json.dumps(dict(note=original,origin=p['origin'],signature=p['failure_signature']),sort_keys=True).encode()).hexdigest()[:32]
    return key,original,p


def receipt_catalogs(provision,note):
    key,_,_=identity(provision,note);path=provision.state_root/'origins'/key/'receipt.json'
    if not path.exists():raise DevConflict('Origin evidence is awaiting automatic preparation by the validation worker')
    receipt=json.loads(path.read_bytes())
    if receipt['status']!='passed':raise DevConflict(receipt['reason'])
    return receipt['catalogs']


def locate(provision,note):
    key,original,p=identity(provision,note);o=p['origin'];matches=[]
    paths=set(provision.root.rglob('ledger.sqlite3'))|set(provision.root.rglob('ledger.sqlite'))
    for path in sorted(paths):
        if path.resolve()!=path:raise DevConflict('Original ledger path is not canonical')
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
            rows=db.execute('SELECT payload FROM v2_experiments WHERE id=?',(o['experiment_id'],)).fetchall()
            for raw, in rows:
                e=json.loads(raw);m=read(e['manifest'])
                # Import snapshots retain the original ledger_path; never select them.
                if Path(m['ledger_path']).resolve()==path:
                    if m['experiment_id']!=o['experiment_id']:raise DevConflict('Original manifest belongs to another experiment')
                    matches.append((path,e,m))
    if len(matches)!=1:raise DevConflict('Original experiment ledger is missing or ambiguous')
    path,e,m=matches[0];store=OriginalStore(path);c=store.cell(o['cell'])
    if c['experiment_id']!=o['experiment_id']:raise DevConflict('Original cell belongs to another experiment')
    def version(number):
        found=[v for v in c['versions'] if v['version']==number]
        if len(found)!=1:raise DevConflict('Original version is missing or ambiguous')
        return found[0]
    v=version(o['origin_version']);help_version=version(o['help_version'])
    jobs=[j for j in c['jobs'] if j['version']==o['origin_version'] and j['kind']=='execute' and j['state']=='settled']
    if len(jobs)!=1:raise DevConflict('Original settled execution is missing or ambiguous')
    job=jobs[0];digests=o['digests']
    from .memory_provisional import derive_signature
    for ref,name in ((v['source'],'origin_source'),(job['receipt']['public_feedback'],'origin_public_feedback'),
                     (help_version['help'],'help'),(help_version['model_input'],'help_model_input'),(help_version['source'],'help_source')):
        checked(ref)
        if ref['sha256']!=digests[name]:raise DevConflict('Original evidence differs from note provenance: '+name)
    if derive_signature(read(job['receipt']['public_feedback']))!=p['failure_signature'] or c['policy_id']!=o['policy_id']:
        raise DevConflict('Origin policy or failure signature differs from the note')
    requests=[r for r in store.planner_snapshot(o['cell'])['requests'] if r.get('version')==o['help_version'] and r.get('request_type')=='advisor' and r.get('adopted') is True]
    provider=read(help_version['help']).get('provider')
    if provider=='advisor' and len(requests)!=1:raise DevConflict('Original adopted help request is missing or ambiguous')
    if provider not in {'advisor','human_proxy'}:raise DevConflict('Original help provider is unsupported')
    request_id=requests[0]['request_id'] if provider=='advisor' else None
    config=read(dict(uri=str(Path(c['artifact_dir'])/'config.json'),sha256=v['config_sha256']))
    if m['configs'].get(f"{c['task_id']}:{c['seed']}")!=config:raise DevConflict('Original frozen configuration differs')
    return dict(key=key,note=original,provenance=p,store=store,cell=c,version=v,job=job,experiment=e,config=config,request_id=request_id)


class OriginPreparation:
    def __init__(self,runner):
        self.runner=runner;self.provision=runner.provision;self.events={};self.phases={}

    def needed(self,note):
        if self.provision.config.get('evidence_catalogs'):return None
        key,original,_=identity(self.provision,note)
        path=self.provision.state_root/'origins'/key/'receipt.json'
        if path.exists() and json.loads(path.read_bytes())['status']=='passed':
            self.provision.catalogs(original)  # Recheck seals, even on reuse.
            return None
        return dict(kind='origin',origin_key=key,note=original,memory_id=note['memory_id'],title=note.get('title') or 'Untitled note',active=True,receipt_ready=path.exists())

    def directory(self,current):return self.provision.state_root/'origins'/current['origin_key']

    def start(self,current):
        key=current['origin_key'];directory=self.directory(current);directory.mkdir(parents=True,exist_ok=True)
        if (directory/'receipt.json').exists():return
        future=self.runner.futures.get(key)
        if future is not None:return
        marker=directory/'dispatch.json'
        if marker.exists():raise DevConflict('Origin replay was interrupted with an unknown outcome; review the dispatch receipt before retrying')
        # Record dispatch before background work. Unknown work is never redispatched.
        immutable(marker,dict(origin_key=key,note=current['note']))
        event=threading.Event();event.set();self.events[key]=event;self.phases[key]='running'
        self.runner.futures[key]=self.runner.pool.submit(self.run,current)

    def pause(self,current):
        event=self.events.get(current['origin_key'])
        if event:event.clear()

    def resume(self,current):
        self.events[current['origin_key']].set()

    def poll(self,current):
        key=current['origin_key'];future=self.runner.futures.get(key)
        if future is not None and future.done():future.result()
        if (self.directory(current)/'receipt.json').exists():return 'completed'
        if future is None:
            if (self.directory(current)/'dispatch.json').exists():raise DevConflict('Origin replay was interrupted with an unknown outcome; review required')
            return 'pending'
        return self.phases[key]

    def finish(self,current):return json.loads((self.directory(current)/'receipt.json').read_bytes())

    def run(self,current):
        directory=self.directory(current);key=current['origin_key'];runner=self.runner
        try:
            found=locate(self.provision,current['note']);store=found['store'];c=found['cell'];v=found['version'];p=found['provenance'];config=found['config']
            from .memory_source_catalogs import manifest_sources,registration_order
            from .dev_v2_memory_import import prepare
            from .dev_planner_memory import context
            ctx=context(config,p['artifact']['applicability']['namespace'])
            if ctx.get('execution_profile')!=p['artifact']['applicability'].get('execution_profile'):raise DevConflict('Origin execution profile differs from the note')
            sources=manifest_sources(found['experiment']['manifest'],ctx,self.provision.root,contract=config.get('scene_contract'))
            output=self.provision.root/'memory-origin-imports'/key
            def export(name,version,**kwargs):
                out=output/name;ref=out/'catalog.json'
                if not ref.exists():
                    if out.exists():raise DevConflict('Origin export was interrupted; review the incomplete export directory')
                    prepare(store,c['key'],version,ctx['namespace'],out,self.provision.root,source_catalogs=sources,**kwargs)
                return json.loads(ref.read_bytes()),json.loads((out/'descriptor.json').read_bytes())
            catalog,d=export('execution',v['version'])
            def before_replay():
                # Pause at native scene boundaries, like ledger arms finishing a
                # dispatched execution. Never modify the historical experiment.
                if not self.events[key].is_set():self.phases[key]='paused'
                self.events[key].wait();self.phases[key]='running'
            from .memory_independent_validator import validate_arm
            result=validate_arm(experiment_dir=self.provision.root,experiments_root=self.provision.root,cell=c['key'],version=v['version'],execution_import_id=key,
                bundle_ref=found['job']['receipt']['bundle'],config_ref=d['config'],source_ref=v['source'],grant=None,origin=True,
                permission_scope=d['permission_scope'],executor_catalog=catalog,contract=config['scene_contract'],validator_root=runner.validator_root,replay=runner.replay,before_replay=before_replay,
                failure_signature=p['failure_signature'],budget=c.get('budget',{}),developer_model=config.get('model','origin'),gpu_seconds=None,ledger_ref=d['ledger'])
            immutable(directory/'validation.json',result)
            refs={}
            if result['status']!='refused':
                source,sd=export('source',p['origin']['help_version'],source=dict(failed_execution_import_id=key,validator_id=key+'-validator',validation_descriptor='verdict.json',request_id=found['request_id']),failure_signature=p['failure_signature'])
                catalogs=registration_order({**sources,key+'-executor':catalog,key+'-source':source,key+'-validator':result['catalog']})
                for cid,value in catalogs.items():refs[cid]=immutable(directory/(cid+'.json'),value)
            reason='Origin evidence prepared' if result['status']=='passed' else 'Could not finish the check: origin '+('validation refused: '+result.get('reason','unknown reason') if result['status']=='refused' else 'independent checks failed: '+', '.join(k for k,v in result['checks'].items() if not v))
            receipt=dict(status=result['status'],reason=reason,catalogs=refs,validation=immutable(directory/'validation.json',result),origin_key=key,note=current['note'])
            if 'gpu_measurement' in result:receipt['gpu_measurement']=result['gpu_measurement']
        except Exception as exc:
            from .notices import safe_detail
            receipt=dict(status='refused',reason='Could not finish the check: '+safe_detail(str(exc) or type(exc).__name__),catalogs={},origin_key=key,note=current['note'])
        immutable(directory/'receipt.json',receipt)
