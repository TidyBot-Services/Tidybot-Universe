"""The audit grant can be large without becoming model input."""
import copy
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import dev_v2_loop as loop
from benchmarks.attention_harness.dev_feedback_projection import input_bound, project_messages
from benchmarks.attention_harness.dev_loop import immutable
from benchmarks.attention_harness.parcc_client import ParccResponse


CONFIG = dict(task_id='open_cabinet', official_env='OpenCabinet', task_prompt='Open the cabinet',
              seed=102, robot='OpenArm365', execution_arm='both', controller='BASIC',
              layout_id=11, style_id=34, scene_id='scene-102', horizon=100,
              sdk_call_limit=100, attempt_wall_seconds=100, perception_mode='sim_gt')


def packet(count, *, long=False):
    entries=[]
    for i in range(count):
        entries.append(dict(memory_id=f'memory-{i:02}', version=1,
            title=f'Guidance {i:02}', summary=f'Public summary {i:02}',
            content=('G' * 1300 if long and i == 0 else f'Guidance {i:02}: ' + 'g' * 285),
            context={'scene_id':'scene-102', 'applicability':'x' * 2000},
            grant_id=f'grant-secret-{i:02}', evidence_refs=[f'evidence-secret-{i:02}'],
            source_trace_id=f'trace-secret-{i:02}', authority='authority-secret',
            development_id='cell:v2', source_policy_sha256='hash-secret',
            seeded_for_tests=False, excluded_from_results=False, status='trusted'))
    return {'entries':entries,'explicit_registry_authorization':True,'schema':'attentionbench.dev-memory.v1'}


@pytest.mark.parametrize('count', [5, 12])
def test_dev_prompt_bound_single_copy_and_audit_packet(tmp_path, monkeypatch, count):
    monkeypatch.setattr(loop,'get_task',lambda _:SimpleNamespace(system_prompt=lambda _:'S' * 700))
    audit=packet(count)
    frozen=copy.deepcopy(audit)
    ref=immutable(tmp_path/'memory-input.json',audit)
    policy='P' * 10000
    feedback={'action_trace':[{'status':'ok','detail':'F' * 735} for _ in range(20)]}
    assert 15000 <= len(json.dumps(feedback)) < 16000
    attention={'action':'retrieve_memory','help':{'provider':'cache','content':audit['entries']},
               'memory':audit['entries']}
    messages=loop.model_messages(CONFIG,policy,feedback,attention,{'dev_calls':1})
    messages,_=project_messages(messages,30000)
    bound=input_bound(messages)
    assert bound < 30000, bound
    visible=json.loads(messages[-1]['content'])['attention_control']
    assert visible['help']=={'provider':'cache'}
    assert len(visible['memory'])==min(count,8)
    assert visible['memory'][0]=={'title':'Guidance 00','summary':'Public summary 00',
                                  'content':audit['entries'][0]['content']}
    for note in visible['memory']:
        assert set(note)=={'title','summary','content'}
        assert messages[-1]['content'].count(note['content'])==1
    if count==5:
        assert 'memory_truncated' not in visible
    else:
        assert visible['memory_truncated']=={'omitted_notes':4,'cut_notes':0}
    for secret in ('grant-secret','evidence-secret','trace-secret','authority-secret',
                   'hash-secret','applicability','seeded_for_tests','excluded_from_results'):
        assert secret not in messages[-1]['content']
    assert audit==frozen
    assert json.loads(Path(ref['uri']).read_bytes())==frozen


def test_guidance_cut_is_reported_and_advisor_uses_same_view(tmp_path, monkeypatch):
    audit=packet(12,long=True)
    notes,truncated=loop.compact_memory(audit['entries'])
    assert len(notes)==8 and len(notes[0]['content'])==1200
    assert truncated=={'omitted_notes':4,'cut_notes':1}
    monkeypatch.setattr(loop,'get_task',lambda _:SimpleNamespace(public=lambda:{'task_id':'open_cabinet'}))
    from benchmarks.attention_harness import dev_planner_memory
    monkeypatch.setattr(dev_planner_memory,'load_memory',lambda *args:(audit,None))
    monkeypatch.setattr(loop,'decide',lambda *args,**kwargs:{'action':'request','request_type':'hint'})
    captured=[]
    def provider_call(*args):
        captured.append(args[4])
        return 'Move the gripper slowly.',{}, {}, {}
    monkeypatch.setattr(loop,'provider_call',provider_call)
    from benchmarks.attention_harness.dev_planner import POLICY
    cell=dict(policy_id=POLICY,budget={'help_count':1},used={'help_count':0},
              jobs=[],task_id='open_cabinet',seed=102,versions=[],planner_features={'memory':{'namespace':'test'}})
    manifest=dict(memory={'entries':[]},configs={'open_cabinet:102':CONFIG},policies={POLICY:{}},
                  trace_visible=True,selection={'mode':'experiment'})
    answer,_,_=loop.attention(SimpleNamespace(cell=lambda _:cell),'cell',2,{},'P' * 10000,
                              1,[],manifest,None,tmp_path,None)
    advisor=json.loads(captured[0][-1]['content'])
    assert advisor['memory']==notes
    assert advisor['memory_truncated']==truncated
    assert answer['memory']==audit['entries']
    for secret in ('grant-secret','evidence-secret','trace-secret','authority-secret',
                   'hash-secret','applicability','seeded_for_tests'):
        assert secret not in captured[0][-1]['content']
    assert json.loads((tmp_path/'help.json').read_bytes())['content']=='Move the gripper slowly.'


@pytest.mark.parametrize('trusted_count,run_count,family', [(5,2,False),(5,2,True),(10,3,True)])
def test_layered_prompt_retains_capped_references(trusted_count, run_count, family):
    from benchmarks.attention_harness import memory_layers as layers
    trusted=[dict(e, trusted_here=True, layer='scene') for e in packet(trusted_count)['entries']]
    runs=[dict(title=f'Run {v}',content='r'*1300,layer='run',trusted_here=False,help_version=v)
          for v in range(1,run_count+1)]
    others=[dict(title='Scene',content='s',layer='scene',trusted_here=False),
            dict(title='Task',content='t',layer='task',trusted_here=False,source_seed=103),
            dict(title='Family',content='f',layer='family',trusted_here=False,source_task_id='pick_place_cup')]
    entries=layers.order(trusted+others,runs,family=family)
    frozen=copy.deepcopy(entries)
    notes,truncated=loop.compact_memory(entries)
    kept=trusted[:layers.TRUSTED_PROMPT_CAP]+entries[trusted_count:]
    assert len(notes)==min(trusted_count,8)+run_count+2+int(family)
    assert [n['title'] for n in notes]==[layers.label(e)+e['title'] for e in kept]
    assert all(len(n['content'])<=1200 for n in notes)
    assert truncated=={'omitted_notes':max(0,trusted_count-8),'cut_notes':run_count}
    assert entries==frozen


@pytest.mark.parametrize('shared', [False,True])
@pytest.mark.parametrize('count', range(9))
def test_small_prompt_byte_identity(shared, count):
    entries=packet(count,long=True)['entries'][::-1]
    if shared:
        for e in entries:e.update(trusted_here=False,match_level='same_task',source_seed=103)
    ordered=entries if shared else sorted(entries,key=lambda e:(e.get('memory_id',''),e.get('version',0)))
    expected=[dict(title=loop.memory_label(e)+e['title'],summary=e['summary'],content=e['content'][:1200])
              for e in ordered]
    cuts=sum(len(e['content'])>1200 for e in ordered)
    assert json.dumps(loop.compact_memory(entries))==json.dumps((expected,{'omitted_notes':0,'cut_notes':cuts} if cuts else None))


@pytest.mark.parametrize('kind', ['model','advisor'])
def test_truncation_flag_is_saved_in_request_artifact(tmp_path, kind):
    notes,truncated=loop.compact_memory(packet(12,long=True)['entries'])
    visible={'attention_control':{'memory':notes,'memory_truncated':truncated}} if kind=='model' else {
        'memory':notes,'memory_truncated':truncated}
    messages=[{'role':'user','content':json.dumps(visible)}]

    class Store:
        def cell(self,key):return {'experiment_id':'experiment','jobs':[],'runtime':{}}
        def tx(self):return nullcontext(None)
        def get(self,db,kind,key):return {'selection':{'model_source':'parcc'}}
        def reserve_check(self,*args,**kwargs):pass
        def reserve(self,*args,**kwargs):return {'job_id':'job','state':'reserved'}
        def claim(self,*args,**kwargs):pass
        def settle(self,*args,**kwargs):pass

    class Client:
        def chat(self,**kwargs):
            return ParccResponse(kwargs['model'],'A hint',None,0.01,1,
                                 {'total_tokens':2,'prompt_tokens':1,'completion_tokens':1})

    loop.provider_call(Store(),'cell',2,kind,messages,None,tmp_path,Client())
    request=json.loads((tmp_path/f'{kind}-input.json').read_bytes())
    assert request['memory_truncated']==truncated
    assert request['messages']==messages
