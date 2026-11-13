"""T2 offline delivery, fake M-11 provider, and Dev citation semantics."""
import copy
import json
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import memory_layers as layers, memory_use, memory_family
from benchmarks.attention_harness import dev_planner_memory as delivery
from benchmarks.attention_harness.dev_v2_loop import compact_memory
from attention_memory_service.dev_v2 import encoded
from attention_memory_service.skill_tags import tags_for
from attention_memory_service.core.store import StateConflictError


def entry(mid,level,verified=False,created=1):
    return dict(memory_id=mid,level=level,match_level=level,source_verified=verified,created_at=created,
                title='Retreat before lifting',content='Retreat until clear.',trusted_here=False)


def test_delivery_global_cap_level_verified_newest_and_local_notes():
    rows=[entry('general','general',True,1000),entry('skill','same_skill',True,1000),
          entry('l1-old','same_task',True,1),entry('l1-new','same_task',True,5),
          entry('l1-unverified','same_task',False,999)]
    result=layers.order(rows)
    assert [e['memory_id'] for e in result]==['l1-new','l1-old','l1-unverified']
    assert layers.order(result)==result
    result=layers.order(rows[:2]+rows[2:3])
    assert [e['memory_id'] for e in result]==['l1-old','skill','general']
    local=dict(memory_id='local',layer='run',help_version=2,content='Retreat.',title='Retreat')
    assert len(layers.order(rows,[local]))==3
    view,_=compact_memory(rows)
    assert len(view)==3 and all('level' in n and 'memory_id' in n for n in view)


def test_legacy_placeholder_prompt_title_and_ids():
    notes,_=compact_memory([dict(memory_id='p-legacy',title='Unvalidated: note',content='Retreat from the cabinet before lifting.',trusted_here=False,match_level='same_scene')])
    assert 'Retreat from the cabinet' in notes[0]['title']
    assert notes[0]['memory_id']=='p-legacy' and notes[0]['level']=='same_task'


def test_using_and_not_using_are_counted_separately_in_same_authored_item():
    notes=[dict(memory_id='p-1',title='Retreat',level='same_skill',content='Retreat in small steps.')]
    result=memory_use.analyse(notes,[dict(file='events.jsonl',event_index=8,
        text='Not using note p-1: no cabinet here.\nUsing note p-1: the next step exits the cabinet.')])
    assert result['used']==1 and result['considered_not_used']==1
    assert result['notes'][0]['referenced']=='explicit'
    declined=memory_use.analyse(notes,[dict(file='policy.py',line=4,text='# Not using note p-1: no carried object.')])
    assert declined['used']==0 and declined['considered_not_used']==1
    assert declined['notes'][0]['referenced']=='none'
    memory_use.validate_report(declined)


def test_generalizer_uses_m11_receipts_and_immutable_retry(tmp_path):
    source=dict(title='Retreat before lifting',guidance='Retreat at x=1.2 before lifting.',context=dict(task_id='pick_place_counter_to_cabinet',task_prompt='Pick the object'))
    response=encoded(dict(title='Retreat before lifting an object',guidance='Retreat slowly until clear of the door.',applies_when='When carrying an object inside a cabinet.'))
    calls=[]
    class Provider:
        def chat(self,**kw):
            calls.append(kw)
            return SimpleNamespace(artifact=lambda:dict(content=response))
    class Client:
        def reuse_note_detail(self,mid):return dict(original=dict(source,memory_id=mid))
        def provenance(self,mid):return dict(artifact=dict(applicability=source['context']))
        def generalize_note(self,**kw):return kw
    result=memory_family.generalize(Client(),'memory:p-1',reuse=True,output_dir=tmp_path,provider=Provider(),clock=lambda:1)
    assert result['rewrite']['applies_when'].startswith('When')
    assert result['rewrite']['receipt']['raw_response']==response
    again=memory_family.generalize(Client(),'memory:p-1',reuse=True,output_dir=tmp_path,provider=Provider())
    assert again==result and len(calls)==1
    assert (tmp_path/'rewrite-0.json').exists()


def test_dev_citation_instruction_is_dev_only_and_identity_locked():
    from benchmarks.attention_harness.dev_agent_workspace import MEMORY_CITATION_RULE
    from openarm365_dev_v2 import dev_config
    assert 'Using note <memory_id>' in MEMORY_CITATION_RULE and 'Not using note <memory_id>' in MEMORY_CITATION_RULE
    import inspect
    assert "glob('dev*.py')" in inspect.getsource(dev_config.make_lock)
    from benchmarks.attention_harness import dev_agent_prompts
    assert 'Using note <memory_id>' not in dev_agent_prompts.EVAL_PROMPT


def test_delivery_skill_identity_and_experiment_guard(monkeypatch):
    ctx=dict(task_id='kettle_boiling',benchmark='b',suite='s',robot='r',controller='c',perception_mode='sim_gt',namespace='n',
             sdk_interface_sha256='a'*64,planner_behavior_sha256='b'*64,execution_profile='r5')
    origin=dict(ctx,task_id='turn_off_stove')
    row=dict(memory_id='p-g',version=1,guidance='Turn carefully.',status='provisional',applicability=origin,
             match_level='same_skill',level='same_skill',skill_tags=list(tags_for('turn_off_stove')),
             trusted_here=False,source_verified=True)
    obj=object.__new__(delivery.DevMemory)
    obj.context=ctx;obj.scope=dict(level='scene');obj.layered=True;obj.family=False;obj.sdk_description_sealed=True
    obj.excluded=False
    obj.gateway=SimpleNamespace(retrieve_entries=lambda *a,**kw:[row]);obj.mode='interactive'
    assert [n.memory_id for n in obj.notes()]==['p-g']
    row['applicability']=dict(origin,planner_behavior_sha256='c'*64)
    assert not obj.notes()
    row['applicability']=origin;obj.mode='experiment'
    assert not obj.notes()
    obj.excluded=True
    assert obj.notes()
    obj.mode='interactive';row['skill_tags']=['press_button:microwave']
    assert not obj.notes()


def test_local_notes_share_the_cap_and_expose_their_id():
    local=dict(note_id='r-local',layer='run',help_version=2,created_at=9,content='Retreat carefully.',title='Retreat',
               trusted_here=False,level='same_task')
    rows=[entry('old','same_task',False,1),entry('skill','same_skill',False,99),entry('general','general',True,999)]
    selected=layers.order(rows,[local])
    assert [e.get('memory_id') or e['note_id'] for e in selected]==['r-local','old','skill']
    prompt,omitted=compact_memory(selected)
    assert prompt[0]['memory_id']=='r-local' and all('level' in n for n in prompt)
    assert len(prompt)==3


def test_operator_view_uses_existing_authority_and_passes_attention_fields():
    from benchmarks.attention_harness.memory_view import MemoryView
    seen={}
    client=SimpleNamespace(confirm_note_level=lambda **kw:seen.update(kw) or dict(ok=True))
    view=MemoryView(client,source='service',can_operate=True)
    assert view.operate('p-1','level',actor='alice',reason=None,dev_v2=True,level='same_skill',opened_at=1)==dict(ok=True)
    assert seen==dict(memory_id='p-1',level='same_skill',actor='alice',opened_at=1,edit=None)
    with pytest.raises(PermissionError):
        MemoryView(client,source='service',can_operate=False).operate('p-1','level',actor='a',reason=None,dev_v2=True)
