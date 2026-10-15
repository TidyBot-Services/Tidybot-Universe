import json,re,threading,urllib.request,urllib.error
from pathlib import Path
import pytest
from benchmarks.attention_harness.dev_ui_server import serve,live
from benchmarks.attention_harness.dev_experiment import *
from benchmarks.attention_harness.dev_loop import immutable
@pytest.fixture
def api(tmp_path):
    s=DevV2Store(tmp_path/'s.sqlite');budget=tmp_path/'budget.json';budget.write_text(json.dumps({'goal':GOAL,'limits':{'dev_calls':28,'advisor_calls':14,'provider_tokens':500000,'executions':28,'wall_seconds':7200}}))
    http=serve(s,tmp_path/'a',0,budget);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(http.server_port)
    html=urllib.request.urlopen(base+'/ui/').read().decode();token=re.search('name="control-token" content="([^"]+)',html)[1]
    def req(path,body=None,auth=True,headers=None):
        h={'X-Attention-Token':token} if auth else {};h.update(headers or {})
        if body is not None:h['Content-Type']='application/json'
        r=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=h)
        try:
            with urllib.request.urlopen(r) as response:return response.status,response.read(),dict(response.headers)
        except urllib.error.HTTPError as e:return e.code,e.read(),dict(e.headers)
    yield s,req,tmp_path
    http.shutdown();http.server_close();thread.join(2)

def test_options_auth_and_create(api):
    s,r,tmp=api
    assert r('/api/dev/options',auth=False)[0]==403
    status,body,h=r('/api/dev/options');assert status==200 and len(json.loads(body)['policies'])==7
    status,body,h=r('/api/dev/experiments',{'request_key':'api-create-test'});assert status==201
    e=json.loads(body);assert len(e['cells'])==14
    assert r('/api/dev/experiments/'+e['experiment_id']+'/switch',{'cell':e['cells'][0]['key'],'policy':'reactive_help'})[0]==409
    assert r('/api/dev/experiments/'+e['experiment_id']+'/run',{'max_cells':15})[0]==400
    assert s.goal()['used']['executions']==0

def test_files_boundary_range_and_live(api):
    s,r,tmp=api;status,body,h=r('/api/dev/experiments',{'request_key':'api-live-test'});e=json.loads(body);key=e['cells'][0]['key'];base=f"/api/dev/experiments/{e['experiment_id']}/cells/{key}"
    directory=tmp/'cell';ep=directory/'v1/executions/native';ep.mkdir(parents=True);s.update_cell(key,artifact_dir=str(directory),state='running',active_episode=str(ep),active_version=1)
    video=ep/'advisor_replay.mp4';video.write_bytes(b'0123456789')
    status,raw,h=r(base+'/files/v1/executions/native/advisor_replay.mp4',headers={'Range':'bytes=2-5'})
    assert status==206 and raw==b'2345' and h['Content-Range']=='bytes 2-5/10'
    assert r(base+'/files/../../private.json')[0]==404
    assert r(base+'/live')[0]==404
    (ep/'live').mkdir();frame=ep/'live/frame-000001.jpg';frame.write_bytes(b'simulated-frame')
    (ep/'live/latest.json').write_text(json.dumps(dict(file=frame.name,sha256=sha(frame),sequence=1,wall_time=time.time(),control_step=1)))
    assert r(base+'/live')[0]==200 and r(base+'/live/frame')[1]==b'simulated-frame'
    s.update_cell(key,state='completed')
    assert r(base+'/live')[0]==404
