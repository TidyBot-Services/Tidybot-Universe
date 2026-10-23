import copy,json
from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext
import pytest
from benchmarks.attention_harness.memory_service_client import MemoryServiceClient
from benchmarks.attention_harness.memory_validation_generation import exposure_receipt,generate
from benchmarks.attention_harness.dev_v2_loop import immutable,model_messages
from benchmarks.attention_harness.tests.test_dev_memory_prompt_bound import CONFIG
from benchmarks.attention_harness.tests.autovalidate_fakes import API,IMPORTER
from benchmarks.attention_harness.tests.autovalidate_fakes import registration,catalog,require_m8
from benchmarks.attention_harness.tests.autovalidate_fakes import sync_request
from benchmarks.attention_harness.memory_monitor import automatic_checks
from benchmarks.attention_harness.memory_validation_worker import atomic,set_enabled


def test_file_only_catalog_client_and_inprocess_registration(tmp_path):
    app,roots=registration(tmp_path);keys=tmp_path/'synthetic.env';keys.write_text(f'ATTENTION_MEMORY_API_KEY={API}\nATTENTION_MEMORY_IMPORTER_KEY={IMPORTER}\n')
    client=MemoryServiceClient.offline('http://localhost:1',key_file=keys)
    def call(method,path,payload=None,**kw):
        status,value=sync_request(app,path.split('?')[0],method=method,payload=payload,key=client.api_key,importer_key=client.importer_key if kw.get('importer') else None)
        if status!=200:raise PermissionError(value['detail'])
        return value
    client._call=call
    c=catalog(tmp_path/'executor');args=dict(catalog_id='one',catalog=c)
    assert client.register_dev_v2_catalog(**args)['role']=='executor'
    assert client.register_dev_v2_catalog(**args)['catalog_id']=='one'
    assert client.validation_queue()==[] and client.memory_events()==[]
    with pytest.raises(ValueError):client.memory_events(after=True)
    with pytest.raises(PermissionError):MemoryServiceClient('http://localhost:1',api_key=API,importer_key=IMPORTER).register_dev_v2_catalog(**args)
    with pytest.raises(PermissionError):client.register_dev_v2_catalog(catalog_id='outside',catalog=catalog(tmp_path/'outside'))
    assert sync_request(app,'/dev-v2/catalogs',method='POST',payload=args,key=API)[0]==403


@pytest.mark.parametrize('treatment',[False,True])
def test_actual_dev_generation_clean_and_untruncated(tmp_path,monkeypatch,treatment):
    from benchmarks.attention_harness import memory_validation_generation as generation,dev_v2_loop as loop
    monkeypatch.setattr(loop,'get_task',lambda _:SimpleNamespace(system_prompt=lambda c:'Write a public SDK policy.'))
    config=immutable(tmp_path/'config.json',CONFIG);origin=immutable(tmp_path/'origin.py','def policy(sdk):\n    pass\n')
    note=dict(memory_id='candidate',version=3,guidance='Lift before moving. '*300)
    c=dict(artifact_dir=str(tmp_path),experiment_id='exp',run_notes=False,memory_family=False,budget={'dev_calls':1},used={'dev_calls':0})
    written=[];store=SimpleNamespace(cell=lambda key:c,tx=lambda:nullcontext(None),get=lambda *a:dict(selection={'model_source':'codex'},manifest={'uri':'manifest','sha256':'a'*64}),version=lambda key,v:written.append(v))
    def fake_provider(store,key,version,kind,messages,owner,directory,client):
        packet=json.loads(messages[1]['content']);assert packet['previous_complete_source']==Path(origin['uri']).read_text()
        assert packet['attention_control']=={'memory':[{'content':note['guidance']}] if treatment else []}
        assert not {'run_notes','help','memory_authority','memory_truncated'}&packet.keys()
        request=immutable(directory/'model-input.json',dict(messages=messages,model_source='codex',model='gpt-6-astra'))
        text=json.dumps(dict(source='from robot_sdk import sensors\nsensors.get_observation()\n',reason='Try again'))
        response=immutable(directory/'model-output.json',dict(content=text));return text,request,response,{}
    monkeypatch.setattr(generation,'provider_call',fake_provider)
    record=generate(store,'arm',CONFIG,origin,{'public_error':'TimeoutError'},None,memory=note if treatment else None,client=object())
    assert written==[record] and record['source']['sha256']!=origin['sha256']
    receipt=json.loads(Path(record['dev_memory_exposure']['uri']).read_bytes());assert len(receipt['entries'])==int(treatment)
    assert receipt['model_input']==record['model_input'] and record['parent_source']['sha256']==origin['sha256']


@pytest.mark.parametrize('fault',['extra','changed','wrong_parent','bool_version','second'])
def test_exposure_receipt_refuses_false_claims(tmp_path,fault):
    origin=immutable(tmp_path/'origin.py','origin');packet={'previous_complete_source':'origin','attention_control':{'memory':[]}}
    if fault=='extra':packet['run_notes']=[]
    if fault=='changed':packet['attention_control']['memory']=[{'content':'another note'}]
    if fault=='wrong_parent':packet['previous_complete_source']='other'
    if fault=='second':packet['attention_control']={'memory':[],'help':{'content':'help'}}
    request=immutable(tmp_path/'request.json',{'messages':[{'role':'system','content':'public'},{'role':'user','content':json.dumps(packet)}]})
    from benchmarks.attention_harness.core.dev_store import DevConflict
    with pytest.raises(DevConflict):exposure_receipt(tmp_path,'cell',True if fault=='bool_version' else 1,request,origin)
    assert not (tmp_path/'dev-memory-exposure.json').exists()


def test_public_monitor_and_route_off_and_errors(tmp_path,monkeypatch):
    from benchmarks.attention_harness import ui_server
    from benchmarks.attention_harness.tests.test_daily_bridge_compatibility import OfflineServer,request
    monkeypatch.setattr(ui_server,'ThreadingHTTPServer',OfflineServer);monkeypatch.setenv('ATTENTION_MEMORY_VALIDATION_STATE',str(tmp_path))
    server=ui_server.create_server(port=0)
    status,body,_=request(server,'/api/dev-v2/automatic-checks');assert status==200 and json.loads(body)['status']=='Off'
    set_enabled(tmp_path,True);atomic(tmp_path/'state.json',dict(status='Checking pair 2 of 5',current=dict(title='Cup',pair_index=1,arm='control',pause_requested=True),runs_today=7,weekly_quota=43.5,private_request='do not display'))
    value=automatic_checks(tmp_path);assert value['current']['pair']==2 and value['enabled']
    assert value['runs_today']==7 and value['weekly_quota']==43.5
    assert 'private_request' not in json.dumps(value)
    (tmp_path/'state.json').write_text('{broken');assert automatic_checks(tmp_path)['errors']


def external_sources(tmp_path,*,nested=False):
    from benchmarks.attention_harness.tests.autovalidate_fakes import SceneFixture
    from benchmarks.attention_harness.memory_source_catalogs import source_catalogs
    tmp_path.mkdir(parents=True,exist_ok=True)
    f=SceneFixture(tmp_path);lock=json.loads(Path(f.lock['uri']).read_bytes())
    original=f.root/'openarm365_dev_v2/sdk.py';external=tmp_path/'runtime/openarm365_dev_v2/sdk.py';external.parent.mkdir(parents=True);external.write_bytes(original.read_bytes())
    lock['files'][str(external)]=lock['files'].pop(str(original));lock['source_root']=str(tmp_path/'runtime');lock['source_sets']={str(external.parent):[str(external)]}
    model=tmp_path/'runtime/model/model.json';model.parent.mkdir();model.write_bytes((f.root/'model.json').read_bytes())
    lock['model_sources']={str(model):lock['files'].pop(str(f.root/'model.json'))};lock['files'].update(lock['model_sources'])
    f.sdk['robot_model_identity']=lock['model_sources']
    from attention_memory_service.dev_v2 import digest
    f.sdk_sha=digest(json.dumps(f.sdk,sort_keys=True,ensure_ascii=False,allow_nan=False).encode())
    if nested:
        contract=external.parent/'scene_contracts/scene.json';contract.parent.mkdir();contract.write_bytes(Path(f.contract['uri']).read_bytes())
        f.contract=dict(f.contract,uri=str(contract))
    f.lock=f.write(Path(f.lock['uri']),lock);r=f.make('origin',failure=True)
    cats=f.seal();cats['contract']=dict(root=str(Path(f.contract['uri']).parent),role='contract',context=r['context'],files={'scene.json':f.contract['sha256']})
    sources=source_catalogs(f.lock,r['context'],f.root,contract=f.contract)
    return f,r,external,cats,sources


def test_original_external_sdk_with_experiment_only_catalog(tmp_path):
    require_m8("auto_validation","scene_contract","source")
    from attention_memory_service.memory_service import MemoryService
    from attention_memory_service.memory_service_api import create_app
    f,r,external,cats,sources=external_sources(tmp_path)
    roots=dict(executor=[str(f.root)],validator=[str(f.validator)],contract=[str(tmp_path/'contracts')],source=[str(tmp_path/'runtime')])
    app=create_app(tmp_path/'authority.sqlite',api_key=API,importer_key=IMPORTER,dev_v2_registration_roots=roots)
    for cid,catalog in {**cats,**sources}.items():
        status,result=sync_request(app,'/dev-v2/catalogs',method='POST',key=API,importer_key=IMPORTER,payload=dict(catalog_id=cid,catalog=catalog))
        assert status==200,result
    service=MemoryService(tmp_path/'authority.sqlite')
    assert service.import_dev_v2_execution(import_id='origin',catalog_id='origin',descriptor='origin/execution.json')['cell']=='origin'
    assert any(c['files']=={'sdk.py':r['descriptor']['sdk_identity']['implementation']['openarm365_dev_v2/sdk.py']} for c in sources.values())
    assert any(list(c['files'])==['model.json'] for c in sources.values())


@pytest.mark.parametrize('when',['before_registration','after_registration','extra_file'])
def test_tampered_external_sdk_refused(tmp_path,when):
    require_m8("auto_validation","scene_contract","source")
    from attention_memory_service.memory_service import MemoryService
    from attention_memory_service.core.store import StateConflictError
    from benchmarks.attention_harness.memory_source_catalogs import source_catalogs
    from benchmarks.attention_harness.core.dev_store import DevConflict
    f,r,external,cats,sources=external_sources(tmp_path)
    service=MemoryService(tmp_path/'authority.sqlite',dev_v2_catalogs=cats)
    if when!='before_registration':
        for cid,catalog in sources.items():service.dev_v2.register(cid,catalog)
    if when=='extra_file':external.with_name('extra.py').write_text('# extra')
    else:external.write_text('# tampered SDK')
    with pytest.raises((DevConflict,ValueError)):source_catalogs(f.lock,r['context'],f.root)
    with pytest.raises(StateConflictError):
        if when=='before_registration':
            for cid,catalog in sources.items():service.dev_v2.register(cid,catalog)
        else:service.import_dev_v2_execution(import_id='origin',catalog_id='origin',descriptor='origin/execution.json')


@pytest.mark.parametrize('fault',[None,'unsealed','wrong_digest','tampered'])
def test_contract_nested_inside_original_sdk_source_set(tmp_path,fault):
    require_m8("auto_validation","scene_contract","source")
    from attention_memory_service.memory_service import MemoryService
    from attention_memory_service.memory_service_api import create_app
    from attention_memory_service.core.store import StateConflictError
    f,r,external,cats,sources=external_sources(tmp_path,nested=True)
    cats.pop('contract')
    contract=Path(f.contract['uri']);source=next(c for c in sources.values() if contract.is_relative_to(c['root']))
    relative=str(contract.relative_to(source['root']))
    assert source['files'][relative]==f.contract['sha256'] and set(source['files'])=={'sdk.py','scene_contracts/scene.json'}
    if fault=='unsealed':source['files'].pop(relative)
    if fault=='wrong_digest':source['files'][relative]='0'*64
    app=create_app(tmp_path/'nested.sqlite',api_key=API,importer_key=IMPORTER,dev_v2_registration_roots=dict(executor=[str(f.root)],validator=[str(f.validator)],source=[str(tmp_path/'runtime')]))
    for cid,catalog in {**cats,**sources}.items():
        status,result=sync_request(app,'/dev-v2/catalogs',method='POST',key=API,importer_key=IMPORTER,payload=dict(catalog_id=cid,catalog=catalog))
        if fault=='wrong_digest' and catalog is source:
            assert status==409,result
            return
        assert status==200,result
    service=MemoryService(tmp_path/'nested.sqlite')
    if fault=='tampered':contract.write_text('{}')
    def execute():return service.import_dev_v2_execution(import_id='origin',catalog_id='origin',descriptor='origin/execution.json')
    if fault:
        with pytest.raises(StateConflictError):execute()
    else:assert execute()['cell']=='origin'


def test_trusted_outcomes_even_with_excluded_references_but_not_validation(tmp_path,monkeypatch):
    from benchmarks.attention_harness import dev_planner_memory as memory,dev_v2_memory_import as importer
    queued=[];events=[]
    monkeypatch.setattr(importer,'queue',lambda *a:queued.append(a))
    store=SimpleNamespace(cell=lambda c:{'run_notes':False},tx=lambda:nullcontext(None),event=lambda *a:events.append(a))
    packet=dict(entries=[dict(memory_id='trusted',grant_id='g',development_id='cell:v1',status='trusted',trusted_here=True),
                         dict(memory_id='reference',grant_id='r',development_id='cell:v1',status='trusted',trusted_here=False,excluded_from_results=True)],excluded_from_results=True)
    memory.journal_result(store,'cell',1,packet,{},dict(uri='feedback',sha256='a'*64),tmp_path)
    assert len(queued)==1 and queued[0][4]=='provisional_outcome'
    assert [g['memory_id'] for g in queued[0][5]['grants']]==['trusted']
    (tmp_path/'memory-validation-input.json').write_text('{}');queued.clear()
    memory.journal_result(store,'cell',1,packet,{},dict(uri='feedback',sha256='a'*64),tmp_path);assert queued==[]


@pytest.mark.parametrize('change',[dict(seeded_for_tests=True),dict(status='rejected'),dict(status='disabled'),dict(expired=True),dict(expires_at=1),dict(withdrawn=True)])
def test_family_references_filter_ineligible_sources(monkeypatch,change):
    from benchmarks.attention_harness.tests.test_memory_layers import family_gateway,SETTINGS
    from benchmarks.attention_harness.dev_planner_memory import DevMemory
    gateway,ctx=family_gateway(monkeypatch);gateway.rows=[dict(gateway.rows[0],**change)]
    assert DevMemory(gateway,SETTINGS,ctx,layered=True,family=True).selected()==[]


def test_nondev_daily_selection_does_not_inherit_family(tmp_path):
    from benchmarks.attention_harness.daily_dev_v2 import bind_daily_run_notes
    from benchmarks.attention_harness.dev_configuration import validate_run_notes
    for tier in ('formal','legacy'):
        bound=bind_daily_run_notes({'budget_tier':tier},tmp_path/'missing');assert 'memory_family' not in bound;validate_run_notes(bound)


@pytest.mark.parametrize('exposure',['none','candidate_validation'])
def test_origin_grant_and_original_native_consumption(tmp_path,exposure):
    import hashlib
    from benchmarks.attention_harness.dev_memory_validation import prepare_validation,policy_input
    from benchmarks.attention_harness.core.dev_store import DevConflict
    config={'seed':101};config_ref=immutable(tmp_path/'config.json',config);source=immutable(tmp_path/'source.py','source')
    record=dict(version=1,config_sha256=config_ref['sha256'],source=source);events=[];grants=[]
    db=SimpleNamespace(execute=lambda *a:[(json.dumps(e),) for e in events])
    store=SimpleNamespace(start_cell_clock=lambda c:None,cell=lambda c:dict(versions=[record],jobs=[],artifact_dir=str(tmp_path)),tx=lambda:nullcontext(db),event=lambda db,k,p:events.append(p))
    def authorize(**kw):
        grants.append(kw);return dict(grant_id='grant',binding={**kw,'memory_version':2,'guidance_sha256':hashlib.sha256(b'Lift first').hexdigest()})
    client=SimpleNamespace(authorize_dev_v2_validation=authorize,get_memory=lambda mid:SimpleNamespace(guidance='Lift first'))
    directory=tmp_path/'v1';directory.mkdir()
    envelope=prepare_validation(store,'arm',1,config,{},client,'candidate',exposure,directory,origin_source_sha256='a'*64)
    assert grants[0]['origin_source_sha256']=='a'*64 and grants[0]['policy_sha256']==source['sha256']
    request=SimpleNamespace(policy_sha256=source['sha256'],config_sha256=config_ref['sha256'],seed=101,run_id='run:arm',attempt_id='attempt:arm:0')
    public,receipt=policy_input(envelope,request)
    assert public['memory_ids']==(['candidate'] if exposure=='candidate_validation' else [])
    assert receipt['consumed_before_policy'] and receipt['input_sha256']==envelope['input']['sha256']
    assert receipt['memory_version']==(2 if exposure=='candidate_validation' else None)
    request.seed=102
    with pytest.raises(DevConflict):policy_input(envelope,request)
    assert len(events)==1 and events[0]['source']==source


def test_failed_check_client_file_capability_and_inprocess_route(tmp_path):
    from benchmarks.attention_harness.tests.autovalidate_fakes import FakeRunner
    runner=FakeRunner(tmp_path/'evidence',failed_check=('c0','safety'))
    current=runner.begin(runner.service.validation_queue()[0],'operation');current.update(arm_id='c0',arm='control')
    result=runner.finish(current);args=result['failed_check']
    keys=tmp_path/'synthetic.env';keys.write_text(f'ATTENTION_MEMORY_API_KEY={API}\nATTENTION_MEMORY_IMPORTER_KEY={IMPORTER}\n')
    client=MemoryServiceClient.offline('http://localhost:1',key_file=keys)
    def call(method,path,payload=None,**kw):
        assert kw['importer'] is True and path=='/dev-v2/failed-checks'
        status,value=sync_request(runner.app,path,method=method,payload=payload,key=client.api_key,importer_key=client.importer_key)
        assert status==200,value
        return value
    client._call=call
    assert sync_request(runner.app,'/dev-v2/failed-checks',method='POST',payload=args,key=API)[0]==403
    with pytest.raises(PermissionError):MemoryServiceClient('http://localhost:1',api_key=API,importer_key=IMPORTER).record_dev_v2_failed_check(**args)
    decision=client.record_dev_v2_failed_check(**args)
    assert decision['decision']=='inconclusive' and client.record_dev_v2_failed_check(**args)==decision


@pytest.mark.parametrize('registered',[False,True])
def test_exporter_keeps_external_original_sources_in_separate_catalogs(tmp_path,monkeypatch,registered):
    import sqlite3
    from benchmarks.attention_harness import dev_v2_memory_import as importer,dev_planner_memory,dev_planner
    from openarm365_dev_v2 import scene_contract
    f,r,external,cats,sources=external_sources(tmp_path)
    directory=f.root/'origin';f.write(directory/'v1/version.json',r['record'])
    r['bundle']['artifact_dir']=str(directory);bundle=f.write(directory/'bundle.json',r['bundle'])
    with sqlite3.connect(r['descriptor']['ledger']['uri']) as db:
        job=json.loads(db.execute("SELECT payload FROM v2_jobs WHERE kind='execute'").fetchone()[0])
    job.update(state='settled');job['receipt']['bundle']=bundle
    cell=dict(r['cell'],versions=[r['record']],artifact_dir=str(directory),jobs=[job])
    store=SimpleNamespace(path=Path(r['descriptor']['ledger']['uri']),cell=lambda key:cell,tx=lambda:nullcontext(None),get=lambda *a:{'manifest':r['descriptor']['manifest']})
    monkeypatch.setattr(dev_planner_memory,'context',lambda *a:r['context'])
    monkeypatch.setattr(dev_planner_memory,'sdk_identity',lambda *a:r['descriptor']['sdk_identity'])
    monkeypatch.setattr(dev_planner,'PlannerControl',lambda store:SimpleNamespace(snapshot=lambda key:{}))
    monkeypatch.setattr(scene_contract,'registered_contract_ref',lambda:f.contract)
    call=lambda:importer.prepare(store,'origin',1,r['context']['namespace'],f.root/'export',f.root,source_catalogs=sources if registered else None)
    if not registered:
        with pytest.raises(ValueError,match='catalog root must contain'):call()
        return
    ref=call();catalog=json.loads(Path(ref['uri']).read_bytes())
    assert catalog['role']=='executor' and catalog['root']==str(f.root)
    assert all((f.root/name).is_relative_to(f.root) for name in catalog['files'])
    assert external.read_text()=='# controlled_test SDK\n'
    lock=json.loads(Path(f.lock['uri']).read_bytes());assert str(external) in lock['files']
    assert 'lock.json' in catalog['files'] and 'export/ledger.sqlite' in catalog['files']
