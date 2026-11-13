"""Offline nested-layer integration, including frozen-format compatibility."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import memory_layers as layers, memory_titles as titles
from benchmarks.attention_harness import dev_planner_memory as memory, dev_v2_loop as loop
from benchmarks.attention_harness import memory_provisional as provisional, memory_run_notes as run_notes
from benchmarks.attention_harness import memory_auto_import as importer, memory_monitor as monitor
from benchmarks.attention_harness import daily_dev_v2 as daily, dev_configuration, dev_experiment
from benchmarks.attention_harness.dev_memory_view import projection
from benchmarks.attention_harness.tests.test_memory_provisional import (
    make, write_version, REJECTED, TIMEOUT, ADVICE, SETTINGS, SOURCE, events,
    missing_model_copy_fixture, populated, staged,
)
from benchmarks.attention_harness.tests.test_memory_allmodes import Gateway
from attention_memory_service.note_title import validate_title


def test_order_caps_labels_and_idempotence():
    trusted = [{'id':'legacy'}, {'id':'trusted','trusted_here':True,'match_level':'same_scene'}]
    runs = [dict(id=f'r{n}', layer='run', help_version=n, trusted_here=False) for n in range(5)]
    scenes = [dict(id=f's{n}', match_level='same_scene', trusted_here=False, created_at=n) for n in range(2)]
    tasks = [dict(id='t',match_level='same_task',trusted_here=False,created_at=99,source_seed=101)]
    families = [dict(id=f'f{n}',match_level='same_family',trusted_here=False,source_task_id='pick_place_plate',created_at=n) for n in range(2)]
    result = layers.order(trusted + tasks + families + scenes, runs, family=True)
    assert [e['id'] for e in result] == ['t','r4','r3']
    assert layers.order(result, family=True) == result
    assert len(layers.order(trusted+tasks+families+scenes,runs)) == 3
    assert layers.order([tasks[0],scenes[0]]) == [tasks[0],scenes[0]]
    assert layers.label(runs[2]) == 'This run (v2) · Unvalidated: '
    assert layers.label(trusted[0]) == ''
    assert layers.label(scenes[0]) == 'Unvalidated: '
    assert layers.label(tasks[0]) == 'Reference (scene 101): '
    assert layers.label(dict(tasks[0],unvalidated=True)) == 'Unvalidated reference (scene 101): '
    assert layers.label(dict(tasks[0],source_seed=None)) == 'Reference: '
    assert layers.label(families[0]) == 'Reference (task pick_place_plate): '
    assert layers.label(dict(families[0],unvalidated=True)) == 'Reference (task pick_place_plate): '
    assert layers.layer_of({}) == 'scene'
    assert layers.layer_of({'match_level':'same_task'}) == 'task'


def test_title_validation_and_collisions():
    assert titles.note_title('Use `obj` and **sink** by exact name. Then move.') == 'Use obj and sink by exact name.'
    assert titles.note_title('Lift the cup\nThen turn') == 'Lift the cup'
    assert titles.note_title('This first clause is long enough; '+ 'additional words '*10) == 'This first clause is long enough'
    assert titles.note_title('go: '+ 'additional words '*10).endswith('…')
    assert titles.note_title('Use /left\\arm <slowly>') == 'Use left arm slowly'
    assert titles.note_title('https://example.com') == 'Apply guidance for robot motion'
    assert titles.note_title('private evaluator', {'operation':'left_arm.move_delta'}) == 'Apply guidance for left arm.move delta'
    base = 'Lift the cup before moving sideways'
    assert titles.unique_title(base, [base.upper()], 4) == base+' (v4)'
    assert titles.unique_title(base, [base,base+' (v4)'], 4) == base+' (v4, 2)'
    long = titles.note_title('Lift the cup carefully ' * 10)
    suffixed = titles.unique_title(long,[long],5)
    assert len(suffixed)<=80 and suffixed.endswith(' (v5)')
    validate_title(suffixed)


def test_daily_default_and_manifest_switch(tmp_path, monkeypatch):
    absent = tmp_path/'selection.json'
    assert daily.bind_daily_run_notes({},absent)['memory_family'] is True
    assert 'memory_family' not in daily.bind_daily_run_notes({'memory_family':False},absent)
    assert daily.bind_daily_run_notes({'memory_family':True},absent)['memory_family'] is True
    with monkeypatch.context() as patch:
        patch.setattr(layers,'FAMILY_DEFAULT',True)
        assert daily.bind_daily_run_notes({},absent)['memory_family'] is True
        assert 'memory_family' not in daily.bind_daily_run_notes({'memory_family':False},absent)
    absent.write_text('{}')
    assert 'memory_family' not in daily.bind_daily_run_notes({'memory_family':True},absent)
    absent.write_text('{"memory_family":true}')
    assert daily.bind_daily_run_notes({},absent)['memory_family'] is True
    for tier in ('formal','legacy'):
        with pytest.raises(ValueError,match='memory_family'):
            dev_configuration.validate_run_notes({'budget_tier':tier,'memory_family':True})
    with pytest.raises(ValueError,match='bool'):
        dev_configuration.validate_run_notes({'memory_family':1})
    store,cell,_,manifest,_ = make(tmp_path/'run','autonomous',memory_family=True)
    assert cell['memory_family'] is True and manifest['memory_family'] is True
    dev_experiment.switch(store,cell['key'],'reactive_help')
    assert store.cell(cell['key'])['memory_family'] is True


def family_gateway(monkeypatch):
    ctx = dict(task_id='pick_place_cup',benchmark='AttentionBench',suite='robocasa365',robot='robot',
               controller='controller',perception_mode='sim_gt',namespace=SETTINGS['namespace'],seed=102)
    monkeypatch.setattr(memory,'context',lambda *a:ctx)
    gateway = Gateway.__new__(Gateway)
    gateway.context, gateway.rows, gateway.levels, gateway.grants = ctx, [], [], {}
    gateway.store_id=lambda:SETTINGS['store_id']
    for n in range(3):
        gateway.rows.append(dict(memory_id=f'f{n}',version=1,guidance='Lift first',status='trusted',
            applicability=dict(ctx,task_id='pick_place_plate'),match_level='same_family',
            source_task_id='pick_place_plate',trusted_here=False,created_at=n,source_seed=103))
    return gateway,ctx


def test_family_opt_in_exclusion_and_packet_recheck(tmp_path,monkeypatch):
    gateway,ctx=family_gateway(monkeypatch)
    off=memory.DevMemory(gateway,SETTINGS,ctx,layered=True)
    assert off.packet(development_id='off',source=SOURCE)['entries'] == []
    assert 'same_family' not in gateway.levels[-1]
    on=memory.DevMemory(gateway,SETTINGS,ctx,layered=True,family=True)
    packet=on.packet(development_id='on',source=SOURCE)
    assert gateway.levels[-1] == ['same_scene','same_task','same_family','same_skill','general']
    assert packet['family_reference'] and packet['family_cap']==1 and packet['not_trusted_here']
    entry,=packet['entries']
    assert entry['memory_id']=='f2' and entry['layer']=='family' and entry['source_task_id']=='pick_place_plate'
    assert packet['excluded_from_results'] and entry['excluded_from_results']
    assert loop.memory_label(entry)=='Reference (task pick_place_plate): '
    assert loop.memory_provenance(packet)['entries'][0]['layer']=='family'
    on.recheck(packet)
    with pytest.raises(PermissionError,match='family'):
        off.recheck(packet)
    on.scope={'level':'reference_only','reason':'no_scene_contract'}
    on.selected()
    assert gateway.levels[-1]==['same_task','same_family','same_skill','general']
    store,cell,config,_,root=make(tmp_path,'autonomous',memory_authority=SETTINGS,memory_family=True)
    monkeypatch.setattr(memory,'client_for',lambda _:gateway)
    directory=root/'v1';directory.mkdir()
    packet,_=memory.load_memory(store,cell['key'],1,SOURCE,config,SETTINGS,directory,True)
    read,=events(store,'planner_memory_retrieved')
    assert read['versions'][0]['layer']=='family'
    assert events(store,'planner_memory_unvalidated_used')[0]['layers']==['family']
    assert projection(store,cell)['retrievals'][0]['entries'][0]['layer']=='family'
    memory.journal_result(store,cell['key'],1,packet,{}, {},directory)
    assert events(store,'planner_memory_result_skipped')[-1]['reason']=='not_trusted_here_excluded_from_results'


def test_run_note_link_layers_and_old_note_resume(tmp_path):
    store,cell,_,_,root=make(tmp_path,'reactive_help',run_notes=True)
    write_version(root,1,public=REJECTED)
    directory=write_version(root,2,public=TIMEOUT,help_value=ADVICE)
    ref=provisional.stage_safely(store,cell['key'],2,directory)
    proposal=json.loads(Path(ref['uri']).read_bytes())
    expected='r-'+hashlib.sha256(f"{cell['experiment_id']}:{cell['key']}:2".encode()).hexdigest()[:24]
    assert proposal['from_run_note']==dict(experiment_id=cell['experiment_id'],cell=cell['key'],version=2,run_note_id=expected)
    assert provisional.run_note_link(store,cell['key'],2,directory)==proposal['from_run_note']
    assert provisional.run_note_link(store,cell['key'],3,root/'v3') is None
    note_path=directory/'memory-run-note.json'
    note=json.loads(note_path.read_bytes())
    assert note['layer']=='run'
    assert importer.provisional_payload(proposal,SETTINGS['namespace'])['from_run_note']==proposal['from_run_note']
    assert run_notes.deliver(store,cell['key'],3,root/'v3')[0]['layer']=='run'
    # Simulate the complete immutable pre-M7 note and its matching ledger digest.
    note.pop('layer');note['title']='Earlier saved title'
    note_path.chmod(0o644);note_path.write_text(json.dumps(note))
    assert provisional.run_note_link(store,cell['key'],2,directory) is None
    digest=hashlib.sha256(note_path.read_bytes()).hexdigest()
    with store.tx() as db:
        row=db.execute("SELECT seq,payload FROM v2_events WHERE kind='planner_memory_run_note_created'").fetchone()
        event=json.loads(row[1]);event['note']['sha256']=digest;event['title']=note['title']
        db.execute('UPDATE v2_events SET payload=? WHERE seq=?',(json.dumps(event),row[0]))
    assert run_notes.create(store,cell['key'],2,directory)['sha256']==digest
    assert 'layer' not in json.loads(note_path.read_bytes())


def test_unlinked_proposal_and_payload_unchanged(tmp_path):
    store,cell,ref,root=staged(tmp_path)
    before=Path(ref['uri']).read_bytes()
    assert 'from_run_note' not in json.loads(before)
    assert provisional.run_note_link(store,cell['key'],2,root/'v2') is None
    provisional.stage(store,cell['key'],2,root/'v2')
    assert Path(ref['uri']).read_bytes()==before
    assert 'from_run_note' not in importer.provisional_payload(json.loads(before),SETTINGS['namespace'])


def test_monitor_layer_and_path_helpers():
    cells=[dict(task_id='pick_place_cup',seed=102)]
    row=dict(memory_id='p',applicability_summary={'task_id':'pick_place_cup'},source_seed=102,
             origin_summary=dict(experiment_id='devexp-4a6bb2d8387428ca4f32b849',help_version=2))
    assert monitor._here_layer(row,cells)=='scene'
    assert monitor._here_layer(dict(row,source_seed=103),cells)=='task'
    assert monitor._here_layer(dict(row,applicability_summary={'task_id':'pick_place_plate'}),cells)=='family'
    assert monitor._here_layer(dict(row,applicability_summary={'task_id':'other'}),cells) is None
    assert monitor._path(row,'task',{})=='Learned in devexp-4a6bb2d8… v2 → provisional on scene 102 → read here as task reference'
    validated=dict(source_seed=102,from_provisional='p')
    assert '→ Validated on scene 102 → read here as family reference' in monitor._path(validated,'family',{'p':row})
    assert monitor._path(dict(source_seed=102,seeded_for_tests=True),'scene',{})=='Test note → scene 102'


def test_importer_titles_replay_receipts_in_queue_order(tmp_path,monkeypatch):
    from benchmarks.attention_harness import memory_family
    monkeypatch.setattr(memory_family,'generalize',lambda *a,**kw: {})
    store,cell,_,root=staged(tmp_path)
    write_version(root,3,public=REJECTED)
    directory=write_version(root,4,public=TIMEOUT,help_value=ADVICE)
    provisional.stage(store,cell['key'],4,directory)
    seen=[]
    class Client:
        def create_provisional_note(self,**payload):
            seen.append(payload['title'])
            if len(seen)==2:
                raise ConnectionError('retry later')
            return dict(memory_id=payload['memory_id'],status='provisional')
    client=Client()
    assert importer.run([store.path],client=client,namespace=SETTINGS['namespace'])['deferred']==1
    assert importer.run([store.path],client=client,namespace=SETTINGS['namespace'])['created']==1
    assert seen==['open cabinet: '+ADVICE['content'],'open cabinet: '+ADVICE['content']+' (v4)','open cabinet: '+ADVICE['content']+' (v4)']


def test_family_scope_rejected_and_unavailable_mapping(monkeypatch):
    import builtins
    gateway,ctx=family_gateway(monkeypatch)
    on=memory.DevMemory(gateway,SETTINGS,ctx,layered=True,family=True)
    for key in ('benchmark','suite','robot','controller','perception_mode','namespace','task_id'):
        saved=copy.deepcopy(gateway.rows)
        for row in gateway.rows:
            row['applicability'][key]='other'
        assert on.selected()==[]
        gateway.rows=saved
    original=builtins.__import__
    def without_families(name,*args,**kwargs):
        if name=='attention_memory_service.families':
            raise ImportError('older service package')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',without_families)
    assert on.selected()==[]


def test_old_packet_projection_derives_layers_without_rewriting(tmp_path,monkeypatch):
    store,cell,config,_,root=make(tmp_path,'autonomous',memory_authority=SETTINGS)
    gateway,_=populated(tmp_path,config)
    gateway.svc.disable('p-old',actor='test',reason='exercise old task-layer packet')
    monkeypatch.setattr(memory,'client_for',lambda _:gateway)
    directory=root/'v1';directory.mkdir()
    packet,_=memory.load_memory(store,cell['key'],1,SOURCE,config,SETTINGS,directory,True)
    packet.pop('family_reference');packet.pop('family_cap')
    for entry in packet['entries']:
        entry.pop('layer');entry.pop('source_task_id');entry.pop('created_at')
    path=directory/'memory-input.json'
    path.chmod(0o644);path.write_text(json.dumps(packet))
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    with store.tx() as db:
        seq,raw=db.execute("SELECT seq,payload FROM v2_events WHERE kind='planner_memory_retrieved'").fetchone()
        event=json.loads(raw);event['packet']['sha256']=digest
        db.execute('UPDATE v2_events SET payload=? WHERE seq=?',(json.dumps(event),seq))
    memory.load_memory(store,cell['key'],1,SOURCE,config,SETTINGS,directory,True)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
    assert [e['layer'] for e in projection(store,cell)['retrievals'][0]['entries']]==['scene','task','scene']


def test_monitor_projection_counts_lineage_and_legacy_read(tmp_path,monkeypatch):
    from benchmarks.attention_harness.tests.test_memory_run_notes import learned, bridge_for
    from attention_memory_service import families
    store,cell,_,_,root,note=learned(tmp_path,memory_family=True)
    bridge=bridge_for(store,cell)
    bridge.get=lambda _: (dev_experiment.snapshot(store,cell['experiment_id']),'application/json')
    run_notes.deliver(store,cell['key'],3,root/'v3')
    monkeypatch.setattr(families,'FAMILIES',{'fixture':(cell['task_id'],'pick_place_*')})
    origin=dict(experiment_id=cell['experiment_id'],help_version=2,policy_id=cell['policy_id'])
    rows=[dict(memory_id='scene',source_seed=cell['seed'],status='provisional',origin_summary=origin),
          dict(memory_id='task',source_seed=cell['seed']+1,status='trusted',from_provisional='scene'),
          dict(memory_id='family',source_seed=104,status='trusted',seeded_for_tests=True),
          dict(memory_id='unrelated',source_seed=cell['seed'],status='trusted')]
    for row,task in zip(rows,(cell['task_id'],cell['task_id'],'pick_place_plate','unrelated')):
        row['applicability_summary']={'task_id':task}
    catalogue=SimpleNamespace(dev_catalogue=lambda:rows)
    result=monitor.project(bridge,catalogue,cell['experiment_id'])
    assert result['run']['layer_counts']==dict(run=1,scene=1,task=1,skill=0,general=0,family=1)
    assert result['run']['family_reference'] is True
    assert [n['layer'] for n in result['notes']]==['scene','task','family',None]
    assert result['run_notes'][0]['layer']=='run'
    assert result['run_notes'][0]['path'].endswith('→ this run only')
    assert 'provisional on scene' in result['notes'][1]['path']
    assert result['notes'][1]['path'].endswith('→ read here as task reference')
    # An old read without an explicit layer overrides the inferred catalogue layer.
    with store.tx() as db:
        store.event(db,'planner_memory_retrieved',dict(cell=cell['key'],version=3,
            versions=[dict(memory_id='task',match_level='same_scene')]))
    result=monitor.project(bridge,catalogue,cell['experiment_id'])
    assert next(n for n in result['notes'] if n['id']=='task')['layer']=='scene'
