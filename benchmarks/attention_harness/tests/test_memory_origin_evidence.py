import json
from pathlib import Path
import pytest
from benchmarks.attention_harness.tests.autovalidate_fakes import AutomaticOriginFakeRunner,synthetic_creation
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment
from benchmarks.attention_harness.memory_validation_worker import tick,set_enabled

pytestmark=pytest.mark.usefixtures('r3_deployment')


def run(fake,state=None):
    state={} if state is None else state;deps=fake.deps(fake.provision.state_root);set_enabled(deps.root,True)
    for _ in range(35):
        tick(state,deps);fake.phase='completed'
        if state.get('results'):break
    return state,deps


def test_automatic_origin_then_five_pairs_and_receipt_reuse(tmp_path):
    fake=AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker')
    from benchmarks.attention_harness.memory_origin_evidence import OriginalStore
    history_store=OriginalStore(fake.failed['descriptor']['ledger']['uri'])
    assert all('gpu_measurement' not in j['receipt'] for j in history_store.cell('failed')['jobs'] if j['kind']=='execute')
    # Origins must work even when no historical-meter accessor is configured.
    fake.origin_runner.gpu_meter=None
    note=fake.service.validation_queue()[0]
    history={p:p.read_bytes() for p in (fake.f.root/'failed').rglob('*') if p.is_file()}
    state,deps=run(fake)
    assert state['results'][0]['status']=='trusted',state['status']
    assert state['runs_today']==11 and len(fake.starts)==10 and fake.replays==2
    assert fake.meter_events==['start','replay','stop']*2 and not fake.meter_active
    assert all(p.read_bytes()==b for p,b in history.items())
    receipt=next((deps.root/'origins').glob('*/receipt.json'));before=receipt.read_bytes()
    from benchmarks.attention_harness.memory_validation_provision import read
    saved=json.loads(before);measurement=read(saved['gpu_measurement']);validation=read(saved['validation'])
    verdict=read(validation['descriptor'])
    assert validation['gpu_measurement']==verdict['gpu_measurement']==saved['gpu_measurement']
    assert measurement['source']=='harness_gpu_meter' and measurement['scope']=='origin_scene_replays'
    assert measurement['cell']=='failed' and measurement['version']==1
    assert measurement['bundle_sha256']==verdict['bundle']['sha256'] and measurement['attempt_id']==verdict['bindings']['attempt_id']
    assert measurement['gpu_seconds']==verdict['costs']['gpu_seconds']==3.75 and measurement['samples']==6
    assert measurement['replays']==fake.meter_results
    assert measurement['scene_replay_observations']==verdict['scene_replay_observations']
    assert measurement['scene_replay_states']==verdict['scene_replay_states']
    assert validation['catalog']['files']['gpu-measurement.json']==saved['gpu_measurement']['sha256']
    assert fake.origin_needed(note) is None
    with synthetic_creation(fake.f):assert fake.provision.resolve(note)['failed_execution']['import_id'].startswith('origin-')
    assert fake.replays==2 and receipt.read_bytes()==before


@pytest.mark.parametrize('fault',['scene','safety','refused','meter'])
def test_origin_failure_never_starts_arms_or_promotes(tmp_path,fault):
    fake=AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker',origin_fault=fault)
    state,deps=run(fake)
    assert state['results'][0]['status']=='inconclusive',state['status']
    assert 'Could not finish' in state['status']
    assert fake.service.get_memory(fake.mid).status.value=='provisional'
    assert not fake.starts and state['runs_today']==1 and not state.get('current')
    before=fake.replays;tick(state,deps)
    assert fake.replays==before and state['runs_today']==1
    if fault=='meter':
        assert state['status'].endswith('Independent GPU time measurement is unavailable for the origin replays.')
        assert fake.replays==1 and not fake.meter_active
        receipt=json.loads(next((deps.root/'origins').glob('*/receipt.json')).read_bytes())
        assert 'gpu_measurement' not in receipt and not receipt['catalogs']
        assert not list(fake.f.validator.glob('arm-*'))


@pytest.mark.parametrize('gate',['off','active','matrix','weekly','daily_quota','unreadable','cap'])
def test_origin_obeys_worker_gates(tmp_path,gate):
    fake=AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker');deps=fake.deps(fake.provision.state_root)
    set_enabled(deps.root,gate!='off');state={}
    if gate in ('active','matrix'):deps.activity=lambda c:'Another run is active' if gate=='active' else 'The frozen main matrix is planned or running'
    if gate=='weekly':deps.quota=lambda:dict(used_percent=70,observed_at=deps.clock())
    if gate=='unreadable':deps.quota=lambda:None
    if gate in ('daily_quota','cap'):
        from datetime import datetime
        state=dict(day=datetime.fromtimestamp(deps.clock()).astimezone().date().isoformat(),runs_today=20 if gate=='cap' else 0,day_start_quota=0)
    tick(state,deps)
    assert fake.replays==0 and not fake.starts and not list((deps.root/'origins').glob('*/dispatch.json'))


def test_origin_takes_last_slot_before_whole_batch_wait(tmp_path):
    fake=AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker');deps=fake.deps(fake.provision.state_root)
    from datetime import datetime
    state=dict(day=datetime.fromtimestamp(deps.clock()).astimezone().date().isoformat(),runs_today=10,day_start_quota=10)
    set_enabled(deps.root,True)
    for _ in range(3):tick(state,deps)
    assert state['runs_today']==11 and fake.replays==2 and not fake.starts
    assert 'fewer than 10' in state['status']


def test_cross_seed_reuses_automatic_origin(tmp_path):
    from benchmarks.attention_harness.tests.cross_seed_fakes import AutomaticCrossFakeRunner
    fake=AutomaticCrossFakeRunner(tmp_path/'fake',tmp_path/'worker',roles={114:'transfer'})
    note=fake.promote_origin();assert fake.origin_state['runs_today']==11
    receipts=list((fake.provision.state_root/'origins').glob('*/receipt.json'));assert len(receipts)==1
    before=receipts[0].read_bytes();fake.enable_cross()
    state={};deps=fake.deps(fake.provision.state_root)
    for _ in range(35):
        tick(state,deps);fake.phase='completed'
        if 'paired checks finished' in state['status']:break
    assert 'paired checks finished' in state['status'],state['status']
    assert state['runs_today']==11 and fake.replays==2
    assert receipts[0].read_bytes()==before
    from benchmarks.attention_harness.memory_cross_seed import resolve_origin
    resolved=resolve_origin(fake.provision,note)
    assert resolved['failed_execution']['import_id']==json.loads(before)['origin_key']


def test_origin_reservation_is_saved_before_dispatch(tmp_path):
    fake=AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker');deps=fake.deps(fake.provision.state_root);set_enabled(deps.root,True)
    original=fake.start
    def start(current,key):
        saved=json.loads((deps.root/'state.json').read_bytes())
        assert saved['runs_today']==1 and saved['counted'][key]==saved['day']
        assert saved['current']['dispatch_pending']
        original(current,key)
    fake.start=start;state={};tick(state,deps)
    assert fake.replays==2


def test_unknown_origin_dispatch_cannot_replay_after_restart(tmp_path):
    from benchmarks.attention_harness.dev_v2_loop import immutable
    fake=AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker');deps=fake.deps(fake.provision.state_root);set_enabled(deps.root,True)
    current=fake.origin_needed(fake.service.validation_queue()[0]);directory=fake.origin_runner.origins.directory(current);directory.mkdir(parents=True)
    immutable(directory/'dispatch.json',dict(origin_key=current['origin_key'],note=current['note']))
    state={};tick(state,deps)
    assert 'unknown outcome' in state['status'] and fake.replays==0 and state['runs_today']==1
    tick(state,deps);assert fake.replays==0 and state['runs_today']==1


def test_origin_drift_refuses_without_replay(tmp_path):
    fake=AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker')
    Path(fake.failed['record']['source']['uri']).write_text('changed')
    state,_=run(fake)
    assert state['results'][0]['status']=='inconclusive' and fake.replays==0
    assert 'digest mismatch' in state['status']


def test_origin_pause_and_resume_uses_worker_gates(tmp_path):
    from types import SimpleNamespace
    from benchmarks.attention_harness.memory_validation_worker import atomic
    calls=[];phase=['running'];current=dict(kind='origin',origin_key='origin-test',memory_id='p',title='Note',active=True)
    runner=SimpleNamespace(poll=lambda c:phase[0],pause=lambda c:calls.append('pause'),resume=lambda c:calls.append('resume'))
    deps=SimpleNamespace(root=tmp_path,clock=lambda:1700000000,save=lambda s:atomic(tmp_path/'state.json',s),runner=runner,quota=lambda:dict(used_percent=10,observed_at=1700000000),activity=lambda c:'Another run is active')
    set_enabled(tmp_path,True);state=dict(current=current)
    tick(state,deps);assert calls==['pause'] and current['pause_requested']
    phase[0]='paused';deps.activity=lambda c:None
    tick(state,deps);assert calls==['pause','resume'] and not current['pause_requested']
    set_enabled(tmp_path,False);tick(state,deps);assert calls==['pause','resume','pause']
    set_enabled(tmp_path,True);tick(state,deps);assert calls==['pause','resume','pause','resume']


def test_ambiguous_original_ledgers_refuse(tmp_path):
    import sqlite3
    import shutil
    fake=AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker')
    original=Path(fake.failed['descriptor']['ledger']['uri']);duplicate=fake.f.root/'duplicate'/'ledger.sqlite3';duplicate.parent.mkdir()
    shutil.copyfile(original,duplicate)
    manifest=json.loads(Path(fake.failed['descriptor']['manifest']['uri']).read_bytes());manifest['ledger_path']=str(duplicate)
    ref=fake.f.write(duplicate.parent/'manifest.json',manifest)
    with sqlite3.connect(duplicate) as db:db.execute('UPDATE v2_experiments SET payload=?',(json.dumps(dict(manifest=ref)),))
    state,_=run(fake)
    assert 'ambiguous' in state['status'] and fake.replays==0 and not fake.starts


def test_origin_catalog_tampering_is_visible_on_reuse(tmp_path):
    fake=AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker');deps=fake.deps(fake.provision.state_root);set_enabled(deps.root,True)
    note=fake.service.validation_queue()[0];state={};tick(state,deps);tick(state,deps)
    receipt=json.loads(next((deps.root/'origins').glob('*/receipt.json')).read_bytes())
    catalog=Path(next(iter(receipt['catalogs'].values()))['uri']);catalog.chmod(0o644);catalog.write_text('{}')
    tick(state,deps)
    assert 'digest mismatch' in state['status'] and fake.replays==2 and not fake.starts


def test_read_only_history_cannot_refresh_or_write(tmp_path):
    import sqlite3
    from benchmarks.attention_harness.memory_origin_evidence import OriginalStore
    fake=AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker');store=OriginalStore(fake.failed['descriptor']['ledger']['uri'])
    before=store.path.read_bytes()
    with pytest.raises(sqlite3.OperationalError,match='readonly'):
        with store.tx() as db:db.execute('DELETE FROM v2_jobs')
    assert store.path.read_bytes()==before


def test_origin_validator_does_not_invent_grant_or_trial(tmp_path):
    fake=AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker');deps=fake.deps(fake.provision.state_root);set_enabled(deps.root,True)
    state={};tick(state,deps);tick(state,deps)
    receipt=json.loads(next((deps.root/'origins').glob('*/receipt.json')).read_bytes())
    validation=json.loads(Path(receipt['validation']['uri']).read_bytes())
    assert validation['status']=='passed' and len(list(fake.f.validator.glob('arm-*')))==1
    assert not list(fake.f.validator.glob('arm-*/trial.json'))
    assert not list(fake.f.root.glob('devexp-*'))
    assert all(json.loads(Path(ref['uri']).read_bytes())['role'] in {'executor','source','validator'} for ref in receipt['catalogs'].values())


def test_monitor_projects_origin_progress_and_failure_without_paths(tmp_path):
    from benchmarks.attention_harness.memory_monitor import automatic_checks
    fake=AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker',origin_fault='meter');deps=fake.deps(fake.provision.state_root);set_enabled(deps.root,True)
    state={};tick(state,deps)
    progress=automatic_checks(deps.root)
    from benchmarks.attention_harness.notices import safe_text
    assert progress['current']==dict(title=safe_text(state['current']['title']),paused=False,stage='origin')
    assert not progress['errors'] and str(tmp_path) not in json.dumps(progress)
    tick(state,deps);value=automatic_checks(deps.root)
    assert value['results'][0]['status']=='inconclusive' and 'GPU' in value['results'][0]['reason']
    assert not value['errors'] and str(tmp_path) not in json.dumps(value)


def test_cross_origin_at_daily_cap_and_cross_switch_pause(tmp_path):
    from types import SimpleNamespace
    from benchmarks.attention_harness.memory_validation_worker import prepare_origin,advance_origin,atomic
    calls=[];current=dict(kind='origin',origin_key='origin-cross',memory_id='p',title='Note',active=True)
    runner=SimpleNamespace(origin_needed=lambda n:dict(current),start=lambda c,k:calls.append('start'),poll=lambda c:'running',pause=lambda c:calls.append('pause'))
    deps=SimpleNamespace(root=tmp_path,runner=runner,clock=lambda:1700000000,
                         quota=lambda:dict(used_percent=10,observed_at=1700000000));state=dict(day='today',runs_today=19)
    save=lambda:atomic(tmp_path/'state.json',state)
    status=lambda text:state.update(status=text) or state
    set_enabled(tmp_path,True)
    prepare_origin(state,deps,{},save,status,cross_seed=True)
    assert state['runs_today']==20 and calls==['start']
    advance_origin(state,deps,None,save,status)
    assert calls==['start','pause'] and state['status']=='Cross-seed checks are Off'
    state.pop('current');prepare_origin(state,deps,{},save,status,cross_seed=True)
    assert state['runs_today']==20 and calls==['start','pause'] and '20 runs' in state['status']


def test_failed_origin_does_not_block_other_queued_notes(tmp_path):
    from types import SimpleNamespace
    from benchmarks.attention_harness.memory_validation_worker import atomic
    bad=dict(memory_id='bad',version=1,uses=1);good=dict(memory_id='good',version=1,uses=1)
    seen=[]
    def begin(note,operation):
        seen.append(note['memory_id']);raise ValueError('Controlled stop after selecting the next note')
    deps=SimpleNamespace(root=tmp_path,clock=lambda:1700000000,save=lambda s:atomic(tmp_path/'state.json',s),quota=lambda:dict(used_percent=10,observed_at=1700000000),activity=lambda c:None,
                         client=SimpleNamespace(validation_queue=lambda:[bad,good]),runner=SimpleNamespace(begin=begin))
    state=dict(results=[dict(memory_id='bad',origin_failure=dict(reason='Could not finish'))]);set_enabled(tmp_path,True)
    tick(state,deps)
    assert seen==['good'] and 'Controlled stop' in state['status']


def test_origin_execution_profile_mismatch_refuses_before_replay(tmp_path):
    from types import SimpleNamespace
    fake=AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker');provenance=fake.service.provenance
    def changed(mid):
        p=provenance(mid);p['artifact']['applicability']['execution_profile']='different-profile';return p
    fake.provision.client=SimpleNamespace(provenance=changed)
    state,_=run(fake)
    assert 'execution profile differs' in state['status'] and fake.replays==0 and not fake.starts


def test_arm_gpu_meter_binds_the_actual_note_version(tmp_path):
    from types import SimpleNamespace
    from benchmarks.attention_harness.dev_v2_loop import immutable
    from benchmarks.attention_harness.memory_validation_provision import Provisioner
    bundle=immutable(tmp_path/'bundle.json',dict(attempt_id='attempt:cell:3'))
    meter=immutable(tmp_path/'gpu.json',dict(source='harness_gpu_meter',cell='cell',version=4,bundle_sha256=bundle['sha256'],attempt_id='attempt:cell:3',gpu_seconds=2.5,samples=4))
    store=SimpleNamespace(cell=lambda key:dict(jobs=[dict(kind='execute',version=4,state='settled',receipt=dict(bundle=bundle,gpu_measurement=meter))]))
    assert Provisioner.gpu_seconds(store,'cell',4)==2.5
    from benchmarks.attention_harness.core.dev_store import DevConflict
    with pytest.raises(DevConflict):Provisioner.gpu_seconds(store,'cell',1)


@pytest.mark.parametrize('index,field,value',[
    (0,'gpu_seconds',None),(0,'gpu_seconds',float('nan')),(1,'gpu_seconds',float('inf')),
    (1,'gpu_seconds',-1),(0,'gpu_seconds',True),(0,'samples',0),(1,'samples',-1),
    (1,'samples',True),(0,'samples',1.5),(1,'wall_seconds',float('nan')),
])
def test_origin_invalid_replay_measurement_refuses_without_a_number(tmp_path,index,field,value):
    fake=AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker')
    fake.meter_results[index][field]=value
    state,deps=run(fake)
    assert state['results'][0]['status']=='inconclusive' and not fake.starts
    assert state['status'].endswith('Independent GPU time measurement is unavailable for the origin replays.')
    assert fake.replays==index+1 and not fake.meter_active
    receipt=json.loads(next((deps.root/'origins').glob('*/receipt.json')).read_bytes())
    assert 'gpu_measurement' not in receipt and not list(fake.f.validator.glob('arm-*'))


@pytest.mark.parametrize('phase',['start','stop'])
def test_origin_meter_exception_is_a_plain_refusal(tmp_path,phase):
    fake=AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker');factory=fake.origin_meter
    def meter():
        instance=factory();original=getattr(instance,phase)
        def broken():
            original()
            raise RuntimeError('Controlled NVML error with internal detail')
        setattr(instance,phase,broken)
        return instance
    fake.origin_meter=meter
    state,_=run(fake)
    assert state['status'].endswith('Independent GPU time measurement is unavailable for the origin replays.')
    assert 'NVML' not in state['status'] and not fake.starts and not fake.meter_active


def test_origin_replay_exception_stops_the_meter(tmp_path):
    fake=AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker');replay=fake.origin_replay
    def broken(*args):
        replay(*args)
        raise RuntimeError('Controlled scene replay failure')
    fake.origin_runner.replay=broken
    state,deps=run(fake)
    assert state['results'][0]['status']=='inconclusive' and not fake.starts
    assert fake.meter_events==['start','replay','stop']*2 and not fake.meter_active
    receipt=json.loads(next((deps.root/'origins').glob('*/receipt.json')).read_bytes())
    assert receipt['status']=='failed' and 'gpu_measurement' in receipt


def test_origin_zero_gpu_seconds_requires_real_samples(tmp_path):
    fake=AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker')
    for measured in fake.meter_results:measured['gpu_seconds']=0.0
    state,deps=run(fake)
    assert state['results'][0]['status']=='trusted'
    from benchmarks.attention_harness.memory_validation_provision import read
    receipt=json.loads(next((deps.root/'origins').glob('*/receipt.json')).read_bytes())
    measured=read(receipt['gpu_measurement'])
    assert measured['gpu_seconds']==0.0 and measured['samples']==6
