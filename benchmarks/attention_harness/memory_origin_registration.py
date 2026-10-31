"""Operator recovery of registration after an already passed origin validation."""
import fcntl
import getpass
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from .core.dev_store import DevConflict
from .dev_v2_loop import checked, immutable
from .memory_source_catalogs import covers, verify_catalogs
from .memory_validation_provision import read


def canonical(path):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts or path.resolve()!=path:
        raise DevConflict('Origin registration recovery path is not canonical.')
    return path


def seal(path):
    path=canonical(path)
    return dict(uri=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def durable_immutable(path,value):
    """Publish complete retry artifacts atomically, retaining normal JSON bytes."""
    import tempfile
    path=canonical(path)
    if path.exists():return immutable(path,value)
    path.parent.mkdir(parents=True,exist_ok=True)
    data=json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n'
    fd,name=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.chmod(name,0o444);os.replace(name,path)
        fd=os.open(path.parent,os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        if os.path.exists(name):os.unlink(name)
    return seal(path)


def move(source,target):
    """Resume a journaled rename and sync both parents, including after a crash."""
    canonical(source);canonical(target)
    if source.exists() and target.exists():raise DevConflict('Origin archive destination already exists.')
    if source.exists():
        target.parent.mkdir(parents=True,exist_ok=True)
        source.rename(target)
    if not target.exists():raise DevConflict('Origin recovery artifact is missing.')
    for parent in {source.parent,target.parent}:
        fd=os.open(parent,os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)


def validator_result(receipt,directory,key):
    ref=receipt.get('validation',{})
    if ref.get('uri')!=str(canonical(directory/'validation.json')):
        raise DevConflict('The origin validation reference does not match its file.')
    result=read(ref)
    if result.get('status')!='passed':raise DevConflict('Only passed origin validation can be reused.')
    catalog=result['catalog'];descriptor=read(result['descriptor'])
    if catalog.get('role')!='validator' or not covers(catalog,result['descriptor']):
        raise DevConflict('The validator descriptor is not sealed by its catalog.')
    verify_catalogs({key+'-validator':catalog})
    if (descriptor.get('execution_import_id')!=key or descriptor.get('checks')!=result.get('checks')
            or not result.get('checks') or not all(v is True for v in result['checks'].values())):
        raise DevConflict('The passed validator bindings or checks differ.')
    # Descriptor references can include original executor evidence outside the
    # validator root; verify those seals too, without running any replay code.
    def verify_refs(value):
        if isinstance(value,dict):
            if 'sha256' in value and ('uri' in value or 'path' in value):
                checked(dict(uri=value.get('uri',value.get('path')),sha256=value['sha256']))
            else:
                for item in value.values():verify_refs(item)
        elif isinstance(value,list):
            for item in value:verify_refs(item)
    verify_refs(descriptor)
    return result,descriptor


def exported(directory,sources):
    """The catalog completion marker is useful only after all its seals verify."""
    ref=seal(directory/'catalog.json');catalog=read(ref)
    if not isinstance(catalog,dict) or catalog.get('role')!='executor' or not isinstance(catalog.get('files'),dict):
        raise DevConflict('The executor catalog is invalid.')
    verify_catalogs({**sources,'export':catalog})
    descriptor_ref=seal(directory/'descriptor.json')
    if not covers(catalog,descriptor_ref):raise DevConflict('The executor descriptor is not sealed by its catalog.')
    return catalog,read(descriptor_ref),ref


def retry_origin_registration(root,memory_id,reason,*,client=None,clock=time.time):
    from . import memory_origin_evidence as origin
    from . import memory_validation_worker as worker
    from .dev_v2_memory_import import prepare
    root=Path(root).resolve()
    if not reason or not reason.strip():raise DevConflict('A reason is required to retry origin registration.')
    with (root/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DevConflict('The validation worker still holds its lock; exit that worker before retrying origin registration.') from exc
        provision,config=worker.origin_command_config(root,client);client=provision.client
        path=root/'state.json';state=json.loads(path.read_bytes()) if path.exists() else {}
        events=state.get('origin_registration_retries',[])
        if any(e['status']=='retrying' and e['memory_id']!=memory_id for e in events):
            raise DevConflict('Another origin registration retry is incomplete.')
        event=next((e for e in reversed(events) if e['memory_id']==memory_id),None)
        if event and event['status'] not in ('retrying','failed','registered'):
            raise DevConflict('Only a failed origin registration retry can start a fresh attempt.')
        if event and event['status']=='registered':
            receipt=read(event['passed_receipt'])
            if receipt.get('status')!='passed' or receipt.get('validation')!=event['validation']:
                raise DevConflict('The completed registration retry receipt differs.')
            provision.catalogs(event['note'])
            return 'Origin registration already retried; previous run reservation remains counted.'
        pending=event is not None and event['status']=='retrying'
        queue=client.validation_queue()
        note=event['queue_note'] if pending else next((n for n in queue if n['memory_id']==memory_id),None)
        if note is None:raise DevConflict('This note is not eligible under the normal validation queue rules.')
        key,original,_=origin.identity(provision,note)
        if not re.fullmatch(r'origin-[0-9a-f]{32}',key):raise DevConflict('Invalid origin key.')
        directory=canonical(root/'origins'/key)
        current=state.get('current')
        if current and (current.get('kind')!='origin' or current.get('memory_id')!=memory_id
                or current.get('origin_key')!=key or current.get('note')!=original):
            raise DevConflict('Current work must be empty or the matching origin item.')
        if any(r.get('memory_id')==memory_id for r in state.get('results',[])):
            raise DevConflict('This note already has a recorded result.')
        if state.get('pending') and state['pending']['note']['memory_id']!=memory_id:
            raise DevConflict('A different note is awaiting provisioning.')
        if pending and (event['origin_key']!=key or event['note']!=original):
            raise DevConflict('The pending registration retry belongs to a different origin.')
        archive=canonical(Path(event['archive']) if pending else root/'origins-archive'/(key+'-registration-'+uuid.uuid4().hex))
        if archive.parent!=root/'origins-archive' or not archive.name.startswith(key+'-registration-'):
            raise DevConflict('The pending registration archive path is invalid.')
        receipt_path=directory/'receipt.json'
        old_path=archive/'receipt.json' if pending and (archive/'receipt.json').exists() else receipt_path
        receipt=read(event['error_receipt']) if pending and old_path==receipt_path else json.loads(canonical(old_path).read_bytes())
        if pending and old_path!=receipt_path:
            checked(dict(uri=str(old_path),sha256=event['error_receipt']['sha256']))
        if receipt.get('origin_key')!=key or receipt.get('note')!=original:
            raise DevConflict('The origin receipt does not match this note.')
        if receipt.get('status')!='error':raise DevConflict('Only an error origin receipt can retry registration.')
        if any(k in receipt for k in ('registration','candidate','candidate_id','service_candidate')) or receipt.get('catalogs'):
            raise DevConflict('The error receipt already contains registration or catalogs.')
        result,verdict=validator_result(receipt,directory,key)
        linked=[r for r in client.list_dev_v2_memories() if
            (r['memory_id']==memory_id and r['status']=='candidate') or r.get('from_provisional')==memory_id]
        # A candidate created by this journaled register call is the sole resume
        # exception. Repeating register rechecks the immutable service bindings.
        if linked and not (pending and event.get('registration_catalogs') and len(linked)==1
                and linked[0]['memory_id']==key+'-candidate' and linked[0]['status']=='candidate'
                and linked[0].get('from_provisional')==memory_id):
            raise DevConflict('This note already has a service candidate.')
        found=origin.locate(provision,original)
        if found['key']!=key:raise DevConflict('The located origin differs from this retry.')
        ctx,sources=origin.export_context(provision,found)
        verify_catalogs(sources)
        if verdict['bundle']!=found['job']['receipt']['bundle'] or verdict['failure_signature']!=found['provenance']['failure_signature']:
            raise DevConflict('The validator result belongs to different origin evidence.')
        if not pending:
            dispatch=json.loads(canonical(directory/'dispatch.json').read_bytes())
            old_key=dispatch.get('export_key',key)
            if (dispatch.get('origin_key')!=key or dispatch.get('note')!=original or not isinstance(old_key,str)
                    or Path(old_key).name!=old_key or not (old_key==key or old_key.startswith(key+'-'))
                    or current and current.get('export_key',old_key)!=old_key):
                raise DevConflict('The original origin dispatch or export key differs.')
            old_output=canonical(provision.root/'memory-origin-imports'/old_key)
            partial_source=canonical(old_output/'source')
            execution=None
            try:
                catalog,d,execution=exported(old_output/'execution',sources)
            except (DevConflict,OSError,ValueError,KeyError,TypeError):pass
            event=dict(event='origin_registration_retried',status='retrying',memory_id=memory_id,origin_key=key,
                note=original,queue_note=note,actor=getpass.getuser(),uid=os.getuid(),at=clock(),reason=reason.strip(),
                archive=str(archive),error_receipt=seal(receipt_path),validation=receipt['validation'],
                partial_source=str(partial_source) if partial_source.exists() else None,
                executor_catalog=execution,export_keys=[key+'-'+uuid.uuid4().hex],
                run_reservation='Previous reservation remains counted; registration retry reserves no new run.')
            state.setdefault('origin_registration_retries',[]).append(event)
            worker.atomic(path,state)  # Intent is durable before either rename.
        elif event['validation']!=receipt['validation']:
            raise DevConflict('The journaled validation seal differs.')
        if not receipt_path.exists() or not (archive/'receipt.json').exists():move(receipt_path,archive/'receipt.json')
        if event['partial_source']:
            source=canonical(Path(event['partial_source']))
            if source.name!='source' or source.parent.parent!=provision.root/'memory-origin-imports':
                raise DevConflict('The partial source export path is invalid.')
            move(source,archive/'source')
        # Every additional interrupted export is retained under this same intent.
        # Freeze the next key before moving the previous attempt's partial tree.
        for i,export_key in enumerate(event['export_keys'][:-1]):
            if not re.fullmatch(re.escape(key)+r'-[0-9a-f]{32}',export_key):raise DevConflict('Invalid retry export key.')
            move(provision.root/'memory-origin-imports'/export_key,archive/('export-'+str(i)))
        export_key=event['export_keys'][-1]
        if not re.fullmatch(re.escape(key)+r'-[0-9a-f]{32}',export_key):raise DevConflict('Invalid retry export key.')
        output=canonical(provision.root/'memory-origin-imports'/export_key)
        if output.exists() and not event.get('registration_catalogs'):
            # A catalog is a completed export. Never resume a partial prepare.
            incomplete=False
            for part in (output/'execution',output/'source'):
                if not part.exists():continue
                try:exported(part,sources)
                except (DevConflict,OSError,ValueError,KeyError,TypeError):incomplete=True
            if incomplete:
                event['export_keys'].append(key+'-'+uuid.uuid4().hex);worker.atomic(path,state)
                move(output,archive/('export-'+str(len(event['export_keys'])-2)))
                output=canonical(provision.root/'memory-origin-imports'/event['export_keys'][-1])
        def export(name,version,**kwargs):
            out=output/name
            if not (out/'catalog.json').exists():
                prepare(found['store'],found['cell']['key'],version,ctx['namespace'],out,provision.root,source_catalogs=sources,**kwargs)
            catalog,d,_=exported(out,sources)
            return catalog,d
        def before_register(catalogs,execution,source):
            verify_catalogs(catalogs)
            refs={cid:durable_immutable(archive/'registration-catalogs'/(cid+'.json'),value) for cid,value in catalogs.items()}
            if event.get('registration_catalogs') and event['registration_catalogs']!=refs:
                raise DevConflict('The journaled registration catalogs differ.')
            event['registration_catalogs']=refs;worker.atomic(path,state)
        try:
            if event['executor_catalog']:
                catalog=read(event['executor_catalog']);verify_catalogs({**sources,key+'-executor':catalog})
                execution_dir=Path(event['executor_catalog']['uri']).parent
                catalog,d,_=exported(execution_dir,sources)
            else:
                catalog,d=export('execution',found['version']['version'],motion_permission='dev_tier_sim_motion',
                    import_purpose='memory_origin' if found['cell'].get('dev_mode')=='agent' else 'memory_validation')
            if event.get('registration_catalogs'):
                verify_catalogs({cid:read(ref) for cid,ref in event['registration_catalogs'].items()})
            preparation=origin.OriginPreparation(SimpleNamespace(provision=provision))
            final=preparation.post_validation(dict(origin_key=key,note=original),found,result,catalog,d,sources,export,
                before_register=before_register,write=durable_immutable)
        except Exception as exc:
            from .memory_auto_import import Redactor
            from .notices import safe_detail
            redact=Redactor([getattr(client,name,None) for name in ('api_key','importer_key','operator_key')])
            message='Origin registration retry failed: '+safe_detail(redact(str(exc) or type(exc).__name__))
            failure=dict(status='error',reason=message,validation=event['validation'],catalogs={},
                origin_key=key,note=original,run_reservation='counted')
            # Publish the failure before unblocking the worker. The archived
            # receipt and exports remain evidence of the previous attempt.
            event['failed_receipt']=durable_immutable(receipt_path,failure)
            event.update(status='failed',failure_reason=message,failed_at=clock())
            title=note.get('title') or 'Untitled note'
            state.setdefault('origin_errors',{})[key]=dict(failure,memory_id=memory_id,title=title)
            state.setdefault('note_errors',{})[memory_id]=dict(reason=message,memory_id=memory_id,title=title)
            state['current']=None
            state.pop('pending',None)
            state['queue']=[n for n in queue if n['memory_id'] not in state['note_errors']]
            state['status']=message
            worker.atomic(path,state)
            raise DevConflict(message) from exc
        durable_immutable(receipt_path,final)
        event['passed_receipt']=seal(receipt_path)
        state['current']=None
        for field in ('origin_failures','origin_errors'):state.get(field,{}).pop(key,None)
        state.get('note_errors',{}).pop(memory_id,None)
        state['check_errors']=[e for e in state.get('check_errors',[]) if e.get('memory_id')!=memory_id]
        state['queue']=queue
        # Registration creates a linked candidate and removes the provisional
        # note from the service queue. Keep its normal worker continuation.
        operation=hashlib.sha256(f"{note['memory_id']}:{note['version']}:{note['uses']}".encode()).hexdigest()[:24]
        state['pending']=state.get('pending') or dict(note=note,operation=operation)
        if found['cell'].get('dev_mode')=='agent':
            state['pending']['note']=dict(state['pending']['note'],channel='dev_agent')
        event['status']='registered'
        state['status']='Origin registration retried; note queued. Previous run reservation remains counted.'
        worker.atomic(path,state)
        return state['status']
