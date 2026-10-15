"""Real loopback HTTP with controlled sources only; zero provider/native calls.

Run on the supervisor host. Socket EPERM is a failure, never a passing skip.
"""
import json
import re
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from benchmarks.attention_harness.ui_server import create_server
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.dev_planner import PlannerControl, binding
from benchmarks.attention_harness.dev_loop import process_identity
from openarm365_dev_v2.tests.test_main_integration import make_bridge
from openarm365_dev_v2.tests.test_planner_features import add_version, SENSOR, SOURCE


def test_main_workbench_real_http_creation_permissions_refresh_and_cancel(tmp_path,monkeypatch):
    human='controlled-human-capability-only-123456'
    monkeypatch.setenv('ATTENTION_HUMAN_APPROVAL_TOKEN',human)
    bridge,body=make_bridge(tmp_path)
    server=create_server(store=AttentionStore(tmp_path/'ui.sqlite'),artifact_root=tmp_path,port=0,dev_v2_bridge=bridge)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    def call(path,body=None,token=None,human_key=None):
        headers={}
        if token:headers['X-Attention-Token']=token
        if human_key:headers['X-Attention-Human-Token']=human_key
        if body is not None:headers['Content-Type']='application/json'
        req=Request(origin+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with urlopen(req,timeout=10) as r:return r.status,r.read()
        except HTTPError as r:return r.code,r.read()
    try:
        status,page=call('/ui/');assert status==200
        token=re.search(rb'data-control-token="([^"]+)"',page)[1].decode()
        options=json.loads(call('/api/run-options')[1]);assert options['dev_v2']['bridge_schema']=='main-dev-v3'
        assert len([p for p in options['dev_loop']['profiles'] if p.get('channel')=='dev_v2'])==2 and options['dev_loop']['enabled']
        assert call('/api/dev-v2/experiments',body)[0]==403
        status,result=call('/api/dev-v2/experiments',body,token);assert status==200,result
        e=json.loads(result);assert len(e['cells'])==2
        assert bridge.store.goal(bridge.goal_id)['real_started_at'] is None
        listed=json.loads(call('/api/runs')[1]);assert len(listed['dev_sessions'])==2
        c=e['cells'][0];key=c['key'];root=tmp_path/'cell';root.mkdir()
        from benchmarks.attention_harness.dev_v2_loop import immutable
        from pathlib import Path
        cfg=json.loads(Path(e['manifest']['uri']).read_bytes())['configs'][f"{c['task_id']}:{c['seed']}"]
        immutable(root/'config.json',cfg);bridge.store.update_cell(key,state='running',owner=process_identity(),artifact_dir=str(root))
        record,d=add_version(bridge.store,c,root)
        request=PlannerControl(bridge.store).request(key,1,'approval',binding(key,1,SOURCE,cfg),60)
        base='/api/dev-v2/experiments/'+e['experiment_id']
        row=json.loads(call(base+'/cells/'+key+'/view')[1]);assert row['planner']['requests'][0]['state']=='waiting'
        assert row['versions'][0]['source_text']==SOURCE
        decision=dict(binding=request['binding'],approved=False,response_id='controlled-http-rejection')
        path=base+'/approvals/'+request['request_id']+'/decision'
        assert call(path,decision,token)[0]==403
        assert call(path,decision,token,token)[0]==403
        assert call(path,decision,token,human)[0]==200
        assert call(path,decision,token,human)[0]==200
        assert json.loads(call(base+'/cells/'+key+'/view')[1])['planner']['requests'][0]['state']=='rejected'
        assert call(base+'/cancel',{},token)[0]==200
        assert json.loads(call(base)[1])['cancelled'] is True
        assert not bridge.store.cell(key)['jobs']
    finally:
        server.shutdown();server.server_close();thread.join(5)
        assert not thread.is_alive()
