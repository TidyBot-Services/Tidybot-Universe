"""Offline R5 interactive-only boundaries and existing Memory lifecycle."""
import copy,json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest

pytestmark=[pytest.mark.scene_gate,pytest.mark.eval_gate]
from benchmarks.attention_harness import dev_configuration as config,dev_planner_memory as memory,memory_run_notes as notes,memory_provisional as provisional
from benchmarks.attention_harness.tests.test_memory_provisional import ADVICE,REJECTED,TIMEOUT,write_version
from openarm365_dev_v2.tests.test_fixture_phase3 import exp,registry,fi,selection,DailyDevV2,DevV2Store,identity,r4_deployment,source_only_lock,base

@pytest.mark.parametrize('path',['product','daily'])
@pytest.mark.parametrize('dev_mode',['one_call','agent'])
@pytest.mark.parametrize('explicit_profile',[False,True])
@pytest.mark.parametrize('mode',[None,'experiment','diagnostic'])
def test_noninteractive_refused_without_artifacts(tmp_path,path,dev_mode,explicit_profile,mode):
    request=selection('open_cabinet' if explicit_profile else 'open_drawer',102,dev_mode)
    if not explicit_profile:request.pop('rules_profile')
    if mode is None:request.pop('mode')
    else:request['mode']=mode
    with pytest.raises(ValueError) as error:
        if path=='daily':DailyDevV2(tmp_path/'daily',[],per_run_budget=True).create(dict(selection=request,start=False))
        else:exp.create(None,tmp_path/'runs',request)
    assert str(error.value)==registry.INTERACTIVE_ONLY
    assert not [p for p in tmp_path.rglob('*') if p.is_file()]

@pytest.mark.parametrize('path',['product','daily'])
@pytest.mark.parametrize('dev_mode',['one_call','agent'])
def test_reset_claim_refused_without_artifacts(tmp_path,path,dev_mode):
    request=dict(selection('open_drawer',101,dev_mode),reproducible_reset=True)
    with pytest.raises(ValueError,match='R5 fixture runs cannot claim reproducible_reset: true'):
        if path=='daily':DailyDevV2(tmp_path/'daily',[],per_run_budget=True).create(dict(selection=request,start=False))
        else:exp.create(None,tmp_path/'runs',request)
    assert not [p for p in tmp_path.rglob('*') if p.is_file()]

@pytest.mark.parametrize('profile',[None,fi.PROFILE])
def test_experiment_measured_gate_never_skips_r5(profile):
    request={} if profile is None else dict(rules_profile=profile)
    with pytest.raises(ValueError,match='requires reproducible_reset: true'):
        config.require_scene_gate('experiment',request,['open_cabinet'],[102])
    request['reproducible_reset']=True
    with pytest.raises(ValueError,match='requires a registered scene_contract'):
        config.require_scene_gate('experiment',request,['open_cabinet'],[102])
    from openarm365_dev_v2.scene_contract import registered_contract_ref
    request['scene_contract']=registered_contract_ref()
    config.require_scene_gate('experiment',request,['open_cabinet'],[102])
    with pytest.raises(ValueError):config.require_scene_gate('experiment',request,['open_cabinet'],[125])

@pytest.mark.parametrize('field',['tasks','seeds','policies'])
@pytest.mark.parametrize('dev_mode',['one_call','agent'])
def test_interactive_is_single_cell(tmp_path,identity,field,dev_mode):
    request=selection('open_drawer',101,dev_mode)
    request[field]+={'tasks':['turn_on_microwave'],'seeds':[102],'policies':['reactive_help']}[field]
    with pytest.raises(ValueError,match='one task/seed/policy|agent_single_cell_required'):
        exp.create(DevV2Store(tmp_path/'db'),tmp_path/'runs',request)
    assert not list(tmp_path.rglob('identity-lock.json'))

@pytest.mark.parametrize('dev_mode',['one_call','agent'])
def test_interactive_r5_notes_and_profile_context(tmp_path,identity,dev_mode):
    request=dict(selection('open_drawer',101,dev_mode),run_notes=True)
    store=DevV2Store(tmp_path/'db');e=exp.create(store,tmp_path/'runs',request)
    m=exp.verify_manifest(e);c=m['configs']['open_drawer:101'];key=e['cells'][0]['key']
    root=Path(e['artifact_dir'])/'cells'/key;root.mkdir(parents=True)
    (root/'config.json').write_text(json.dumps(c))
    write_version(root,1,public=REJECTED)
    directory=write_version(root,2,public=TIMEOUT,help_value=ADVICE)
    ref=notes.create(store,key,2,directory)
    assert ref
    note=json.loads(Path(ref['uri']).read_bytes())
    assert note['cell']==key and note['run_local'] and note['unvalidated'] and note['excluded_from_results']
    assert notes.deliver(store,key,2,directory)==[]
    packet=notes.deliver(store,key,3,root/'v3')
    assert [n['note_id'] for n in packet]==[note['note_id']]
    assert packet[0]['trusted_here'] is False and packet[0]['excluded_from_results']
    assert notes.deliver(store,key,3,root/'v3')==packet
    if dev_mode=='agent':
        from benchmarks.attention_harness.dev_agent_memory import Memory
        published={}
        workspace=NS(private=root/'private',publish=lambda name,value:published.update({name:value}))
        Memory(store,key,workspace,c,m['initial_sources']['open_drawer']['source']).open(3,'print(1)')
        assert any(note['content'] in str(v) for v in published.values())
    notes.turn_off(store,key,note['note_id'])
    assert notes.deliver(store,key,4,root/'v4')==[]
    queued=provisional.stage(store,key,2,directory)
    assert queued
    proposal=json.loads(Path(queued['uri']).read_bytes())
    assert proposal['context']['execution_profile']==fi.PROFILE
    assert proposal['excluded_from_results'] and proposal['unvalidated'] and not proposal['authority_candidate']
    assert memory.memory_scope(c,'interactive')=={'level':'scene'}
    ctx=memory.context(c,'test')
    variants=[]
    for profile,sdk,planner in [(fi.PROFILE,ctx['sdk_interface_sha256'],ctx['planner_dev_local_sha256']),('r4-20261027',ctx['sdk_interface_sha256'],ctx['planner_dev_local_sha256']),(fi.PROFILE,'wrong',ctx['planner_dev_local_sha256']),(fi.PROFILE,ctx['sdk_interface_sha256'],'wrong')]:
        variants.append(dict(ctx,execution_profile=profile,sdk_interface_sha256=sdk,planner_dev_local_sha256=planner))
    class Gateway:
        def store_id(self):return 'store'
        def retrieve_entries(self,*args,**kwargs):
            return [dict(memory_id=str(i),version=1,guidance='Observe before moving.',status='trusted',applicability=v,match_level='same_scene',trusted_here=True) for i,v in enumerate(variants)]
    service=memory.DevMemory(Gateway(),dict(namespace='test',store_id='store'),c,layered=True,mode='interactive')
    assert [n.memory_id for n in service.selected()]==['0']

@pytest.mark.parametrize('mode',[None,'experiment'])
@pytest.mark.parametrize('reset',[None,True])
def test_nonr5_experiment_creation_requires_measured_contract(tmp_path,mode,reset):
    request=selection('open_cabinet',102,'one_call');request.pop('rules_profile')
    if mode is None:request.pop('mode')
    else:request['mode']=mode
    if reset is not None:request['reproducible_reset']=reset
    with pytest.raises(ValueError,match='requires a registered scene_contract' if reset else 'requires reproducible_reset: true'):
        exp.create(None,tmp_path/'runs',request)
    assert not list(tmp_path.rglob('*'))
