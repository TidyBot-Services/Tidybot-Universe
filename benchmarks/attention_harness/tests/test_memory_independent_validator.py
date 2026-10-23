import copy,json
from pathlib import Path
import pytest
from benchmarks.attention_harness.tests.autovalidate_fakes import SceneFixture,STATE,OBS,SIGNATURE
from benchmarks.attention_harness.memory_independent_validator import validate_arm
from openarm365_dev_v2.scene_contract import fingerprint


@pytest.mark.parametrize('state,expected',[
    ({},'44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a'),
    ({'z':[1,1.0,-0.0],'a':{'x':1.23456789,'y':-0.00000049}},'63ae03bffe689f5dd30a90c8c478d2e6471f732536acd826c19a249ce0a1a003'),
    ({'杯':[0.00000149,0.00000151,-1.23456789]},'bb8827827fadd0eb720834d94e1eb95aa07e68a17ccd31b1b4c6e479df9b75fe'),
    ({'robot':{'quat':[0.0,0.0,0.0,1.0]},'objects':{'cup':[1.00000049,2.0,3.0]}},'b8cacf00454f83a8c454bc50f9a9c4029afbe2e4ba6087398d1b68c9b05a1b7a')])
def test_service_golden_vectors(state,expected):
    service_fingerprint=pytest.importorskip("attention_memory_service.scene_contract",reason="needs the M-8 Memory service").fingerprint
    assert fingerprint(state,True)==expected==service_fingerprint(state,True)


def arm(tmp_path,*,failure=True,success=False):
    f=SceneFixture(tmp_path);r=f.make('arm',failure=failure,success=success)
    d=r['descriptor'];b=r['bundle'];grant=dict(grant_id='synthetic-grant',binding=dict(cell='arm',version=1,policy_sha256=r['record']['source']['sha256'],config_sha256=d['config']['sha256'],seed=101,exposure='none',memory_id='m'),permission_scope=d['permission_scope'])
    args=dict(experiment_dir=f.root,experiments_root=f.root,cell='arm',version=1,execution_import_id='arm',bundle_ref=r['verdict']['bundle'],config_ref=d['config'],source_ref=r['record']['source'],grant=grant,permission_scope=d['permission_scope'],executor_catalog=f.seal()['arm'],contract=f.contract,validator_root=tmp_path/'independent',replay=lambda *args:(OBS,STATE),failure_signature=SIGNATURE,budget=r['cell']['budget'],developer_model='fake',gpu_seconds=0,ledger_ref=d['ledger'])
    return f,r,args


@pytest.mark.parametrize('failure,success',[(True,False),(False,False),(False,True)])
def test_validator_keeps_observed_signature_and_native_outcome(tmp_path,failure,success):
    f,r,args=arm(tmp_path,failure=failure,success=success);before={p:p.read_bytes() for p in f.root.rglob('*') if p.is_file()}
    result=validate_arm(**args);assert result['status']=='passed'
    v=json.loads(Path(result['descriptor']['uri']).read_bytes())
    assert v['signature_present'] is failure and v['native_success'] is success
    assert v['stop_position']==(1 if failure else 0)
    assert all(p.read_bytes()==raw for p,raw in before.items())
    assert result['catalog']['role']=='validator' and len(v['scene_replay_states'])==2


@pytest.mark.parametrize('fault',['tamper','missing','binding','permission','overlap','symlink'])
def test_refusal_writes_nothing(tmp_path,fault):
    f,r,args=arm(tmp_path)
    if fault=='tamper':Path(args['source_ref']['uri']).write_text('changed')
    if fault=='missing':Path(args['source_ref']['uri']).unlink()
    if fault=='binding':args['grant']['binding']['seed']=102
    if fault=='permission':args['grant']['permission_scope']={}
    if fault=='overlap':args['validator_root']=f.root/'nested'
    if fault=='symlink':
        path=Path(args['source_ref']['uri']);data=path.read_bytes();path.unlink();outside=tmp_path/'outside';outside.write_bytes(data);path.symlink_to(outside)
    assert validate_arm(**args)['status']=='refused'
    assert not Path(args['validator_root']).exists()


@pytest.mark.parametrize('fault',['state','between','observation','replay_error','safety','native'])
def test_failed_checks_still_write_descriptor(tmp_path,fault):
    f,r,args=arm(tmp_path)
    changed=copy.deepcopy(STATE);changed['objects']['cup'][0]+=1
    if fault=='state':args['replay']=lambda *a:(OBS,changed)
    if fault=='between':
        values=iter([(OBS,STATE),(OBS,changed)]);args['replay']=lambda *a:next(values)
    if fault=='observation':
        values=iter([(OBS,STATE),({'robot':{},'perception':{'values':{}}},STATE)]);args['replay']=lambda *a:next(values)
    if fault=='replay_error':args['replay']=lambda *a:(_ for _ in ()).throw(RuntimeError('reset failed'))
    if fault in {'safety','native'}:
        key='safety' if fault=='safety' else 'native_result';ref=r['bundle']['artifacts'][key];value=json.loads(Path(ref['uri']).read_bytes())
        value.update(monitor_available=False) if fault=='safety' else value.update(evaluated=False)
        r['bundle']['artifacts'][key]=f.write(Path(ref['uri']),value)
        args['bundle_ref']=f.write(f.root/'arm/bundle.json',r['bundle']);args['executor_catalog']=f.seal()['arm']
    result=validate_arm(**args);assert result['status']=='failed'
    assert Path(result['descriptor']['uri']).is_file()
    assert not all(result['checks'].values())
