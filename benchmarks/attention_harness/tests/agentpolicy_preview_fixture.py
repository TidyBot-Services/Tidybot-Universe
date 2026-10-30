"""Synthetic display-only fixtures. No executor, model, service or simulation calls."""
import hashlib
import json
from pathlib import Path

from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.dev_ledger import DevV2Store, RESOURCES, encoded
from benchmarks.attention_harness.dev_agent_ledger import AgentLedger
from benchmarks.attention_harness.dev_agent_workspace import Workspace, atomic_write, encode
from benchmarks.attention_harness.dev_agent_view import projection
from benchmarks.attention_harness.dev_agent_policy_freeze import freeze
from benchmarks.attention_harness.dev_agent_policies import normalize_parameters

STAMP=1793328000


def generate(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    AttentionStore(root/'attention.sqlite3')
    daily=root/'daily';daily.mkdir(exist_ok=True)
    (root/'artifacts').mkdir(exist_ok=True)
    pages=[]
    for label,policy,version in (
        ('trace-v2','full_trace_aware_attention_planner','agent-policy-v2'),
        ('initiated-v2','agent_initiated_help','agent-policy-v2'),
        ('legacy-v1','reactive_help','agent-policy-v1')):
        eid='devexp-'+hashlib.sha256(label.encode()).hexdigest()[:24]
        key=hashlib.sha256((label+':cell').encode()).hexdigest()
        directory=daily/eid
        if directory.exists():
            import shutil
            marker=directory/'manifest.json'
            if not marker.is_file() or json.loads(marker.read_bytes()).get('preview_only') is not True:
                raise ValueError('Refusing to replace a non-preview directory')
            shutil.rmtree(directory)
        directory.mkdir()
        store=DevV2Store(directory/'ledger.sqlite3')
        cellroot=directory/'artifacts/cells'/key
        selection=dict(dev_mode='agent',request_key=label,tasks=['open_cabinet'],seeds=[102],policies=[policy],
                       help_budget=3,mode='interactive',dev_model='gpt-6.1-sol',eval_model='gpt-6.1-sol',
                       name_suffix='PREVIEW · '+label+' · synthetic display evidence')
        config=dict(task_id='open_cabinet',seed=102,budget_tier='dev',runner_boundary='dev_loop',dev_mode='agent',
                    task_prompt='Synthetic preview. No robot or model is connected.',robot='OpenArm365',execution_arm='both')
        if version.endswith('v2'):config['agent_policy_version']=version;selection['agent_policy_version']=version
        cell=dict(key=key,experiment_id=eid,task_id='open_cabinet',seed=102,policy_id=policy,mode='interactive',
                  dev_mode='agent',artifact_dir=str(cellroot),state='needs_review' if label=='trace-v2' else 'completed',
                  reason='preview_only',owner=None,budget={k:0 for k in RESOURCES},used={k:0 for k in RESOURCES})
        if version.endswith('v2'):cell['agent_policy_version']=version
        params=normalize_parameters(policy) if version.endswith('v2') else {}
        selection['policy_parameters']={policy:params}
        manifest=dict(schema='attentionbench.preview.display-only',selection=selection,dev_mode='agent',
                      configs={'open_cabinet:102':config},cells=[cell],policies={policy:params},
                      model={'model':'gpt-6.1-sol'},preview_only=True)
        if version.endswith('v2'):freeze(manifest)
        raw=encode(manifest);mp=directory/'manifest.json';mp.write_bytes(raw)
        experiment=dict(experiment_id=eid,selection=selection,manifest={'uri':str(mp),'sha256':hashlib.sha256(raw).hexdigest()},
                        state='completed',cancelled=False,owner=None,created_at=STAMP,artifact_dir=str(directory/'artifacts'),disk_root=str(daily))
        with store.tx() as db:
            store.put(db,'experiments',eid,experiment)
            db.execute('INSERT OR REPLACE INTO v2_cells VALUES (?,?,?)',(key,eid,encoded(cell)))
        ws=Workspace(cellroot/'agent')
        ws.publish('config.json',config)
        ledger=AgentLedger(store.path,key,help_limit=3,clock=lambda:STAMP)
        timeline=[]
        # Three executions: first practice, then a formal attempt, then practice.
        for number,op in ((1,'run'),(2,'submit'),(3,'run')):
            en=f'e{number:06d}';rid=f'preview-{number}';vid='v0001' if op=='submit' else None
            resources={'moving_executions':1,**({'formal_versions':1} if vid else {})}
            response=dict(request_id=rid,op=op,status='completed',sequence=number,execution_id=en,result={})
            if vid:response['version_id']=vid
            ledger.reserve('request:'+rid,'request',resources);ledger.claim('request:'+rid);ledger.settle('request:'+rid,response)
            atomic_write(ws.private,'journal/'+rid+'.json',encode({'response':response}))
            ws.publish('rpc/responses/'+rid+'.json',response)
            for name,value in {'stdout.txt':b'Synthetic motion refusal\n','stderr.txt':b'', 'trace.json':[],
                               'result.json':{'status':'failed','completed':False},'frames.json':[],
                               'policy.py':b'print("synthetic preview")\n'}.items():ws.publish(f'executions/{en}/'+name,value)
            if vid:
                for name,value in {'bundle.py':b'print("synthetic preview")\n','submission.json':{'execution_id':en,'summary':'Inspect the synthetic scene.'},
                                   'eval.json':{'status':'completed','passed':False,'reason':'Synthetic approach blocked.'},
                                   'help.json':{'agent_policy_version':version,'status':'not_eligible' if version.endswith('v2') else 'delivered','help_ids':[]}}.items():ws.publish(f'versions/{vid}/'+name,value)
                store.version(key,dict(version=1,version_id=vid,execution_id=en,valid=True,formal_eligible=False,eval={'passed':False},native_success=False))
        if version.endswith('v2'):
            for number in (1,3):
                en=f'e{number:06d}';hid='help:'+en
                delivered=number==1
                value=dict(agent_policy_version=version,help_id=hid,execution_id=en,origin_version_id=None if number==1 else 'v0001',
                           status='delivered' if delivered else 'unavailable',requested=True,delivered=delivered,
                           provider='advisor',hint_human_seconds=12.5+number,help_count=1,
                           content='Try a smaller approach.' if delivered else '',
                           decision={'action':'request','reason':'stuck_within_version' if label=='trace-v2' else 'agent_requested'},
                           system_injected=label=='trace-v2',forced_fallback=label=='trace-v2',fallback_trigger='next_submit' if label=='trace-v2' else None)
                ledger.reserve(hid,'help',{'help':1});ledger.claim(hid);ledger.settle(hid,value)
                ws.publish(f'executions/{en}/help.json',value)
            if label=='trace-v2':
                # Deliberately assembled state samples so browser review covers every
                # status. Forced asks are exercised by reactive/retry in real runs.
                pending={'origin_version_id':'v0001','execution_id':'e000002','reason':'eval_failed'}
                atomic_write(ws.private,'attention.json',encode({'pending_ask':pending,'active_help':None}))
                ws.publish('events/forced-fallback.json',dict(forced_fallback=True,forced_fallback_blocked='wall_stop',
                    execution_id='e000002',requested=False,help_count=0))
                response=dict(request_id='ask-required',op='submit',status='rejected',sequence=4,error='ask_required',charged={},result={'execution_id':'e000002'})
                atomic_write(ws.private,'journal/ask-required.json',encode({'response':response}))
                ws.publish('rpc/responses/ask-required.json',response)
                store.update_cell(key,error='Synthetic DriveError: visible result.error sample')
        else:
            value=dict(status='delivered',version_id='v0001',delivered=True,content='Legacy hint.',hint_human_seconds=9)
            ws.publish('versions/v0001/help.json',value)
            ledger.reserve('help:v0001','help',{'help':1});ledger.claim('help:v0001');ledger.settle('help:v0001',value)
        atomic_write(directory,'authorization.json',encode({'authorization_mode':'per_run_budget','deadline_epoch':None}))
        atomic_write(directory,'selection.json',encode(selection))
        view=projection(store,store.cell(key),now=STAMP)
        pages.append(dict(label=label,experiment_id=eid,key=key,session_id='v2-'+key,
                          workspace='/ui/?dev=v2-'+key,view=f'/api/dev-v2/experiments/{eid}/cells/{key}/agent/view'))
        (root/(label+'-view.json')).write_text(json.dumps(view,ensure_ascii=False,indent=2))
    (root/'pages.json').write_text(json.dumps(pages,ensure_ascii=False,indent=2))
    return pages
