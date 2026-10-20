import base64
import json
import os
import stat
import time

import pytest

from benchmarks.attention_harness.codex_client import CodexClient
from benchmarks.attention_harness.parcc_client import ParccError


FAKE = '''#!/usr/bin/env python3
import json, os, pathlib, signal, subprocess, sys, time
here=pathlib.Path(__file__).parent
control=json.loads((here/'control.json').read_text())
argv=sys.argv[1:]
images=[pathlib.Path(argv[i+1]) for i,v in enumerate(argv) if v=='-i']
capture={'argv':argv,'cwd':os.getcwd(),'prompt':sys.stdin.read(),'images':[p.read_bytes().hex() for p in images],
         'files':sorted(p.name for p in pathlib.Path.cwd().iterdir()),'env':sorted(os.environ)}
(here/'capture.json').write_text(json.dumps(capture))
if control.get('sleep'):
    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
    (here/'child.pid').write_text(str(child.pid))
    def stop(*_):
        child.terminate(); child.wait(); sys.exit(143)
    signal.signal(signal.SIGTERM,stop)
    time.sleep(30)
if control.get('exit'):sys.exit(control['exit'])
if control.get('error'):print(json.dumps({'type':'error','message':'sensitive prompt'}))
else:
    print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':control.get('answer','OK')}}))
    if not control.get('no_usage'):
        print(json.dumps({'type':'turn.completed','usage':{'input_tokens':15046,'cached_input_tokens':7424,
              'cache_write_input_tokens':0,'output_tokens':5,'reasoning_output_tokens':0}}))
'''


@pytest.fixture
def fake(tmp_path, monkeypatch):
    script=tmp_path/'codex'
    script.write_text(FAKE)
    script.chmod(script.stat().st_mode|stat.S_IXUSR)
    (tmp_path/'control.json').write_text('{}')
    monkeypatch.setenv('PATH',str(tmp_path)+os.pathsep+os.environ['PATH'])
    monkeypatch.setenv('LITELLM_KEY','should-not-leak')
    monkeypatch.setenv('PARCC_API_KEY','should-not-leak')
    monkeypatch.setenv('ATTENTION_MEMORY_API_KEY','should-not-leak')
    monkeypatch.setattr('benchmarks.attention_harness.codex_client.quota_mark',lambda:{'used_percent':12,'timestamp':'now'})
    return tmp_path


def call(messages=None, **kw):
    client=CodexClient(timeout_seconds=kw.pop('timeout_seconds',2),**kw)
    return client,client.chat(model='gpt-6-astra',messages=messages or [{'role':'user','content':'hello'}],max_tokens=32)


def test_text_argv_cwd_stdin_usage_observer_and_environment(fake):
    observed=[]
    client,response=call(response_observer=lambda status,headers,raw:observed.append((status,headers,json.loads(raw))))
    capture=json.loads((fake/'capture.json').read_text())
    assert capture['argv']==['exec','--json','--ephemeral','--skip-git-repo-check','-s','read-only',
                             '-m','gpt-6-astra','-c','model_reasoning_effort=high','-']
    assert capture['cwd'].startswith('/tmp/attention-codex-')
    assert capture['files']==['stderr.log','stdout.jsonl']
    assert capture['prompt']=='Answer only from the supplied material. Do not use tools or read files.\n[user]\nhello\n'
    assert not any(name.startswith(('LITELLM','PARCC','ATTENTION_MEMORY')) for name in capture['env'])
    assert response.content=='OK'
    assert response.usage['total_tokens']==15051
    assert response.usage['codex_usage']['cached_input_tokens']==7424
    assert observed[0][0:2]==(200,{})
    assert observed[0][2]['choices'][0]['message']['content']=='OK'
    assert client.quota_before==client.quota_after=={'used_percent':12,'timestamp':'now'}


def test_image_bytes_and_placeholder_order(fake):
    one=b'\x89PNG\r\n\x1a\nfirst';two=b'\xff\xd8second'
    messages=[{'role':'system','content':'rule'},{'role':'user','content':[
        {'type':'text','text':'before'},
        {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(one).decode()}},
        {'type':'text','text':'between'},
        {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(two).decode()}}]}]
    call(messages)
    capture=json.loads((fake/'capture.json').read_text())
    assert capture['images']==[one.hex(),two.hex()]
    assert capture['files']==['image-1.png','image-2.jpg','stderr.log','stdout.jsonl']
    assert capture['argv'].count('-i')==2
    assert capture['prompt']==('Answer only from the supplied material. Do not use tools or read files.\n'
                               '[system]\nrule\n[user]\nbefore\n[image 1 supplied with -i]\n'
                               'between\n[image 2 supplied with -i]\n')


@pytest.mark.parametrize('control,error',[
    ({'no_usage':True},'usage'),({'exit':4},'failed'),({'error':True},'error'),({'answer':''},'message')])
def test_bad_response_is_safe(fake,control,error):
    (fake/'control.json').write_text(json.dumps(control))
    with pytest.raises(ParccError,match=error) as caught:call([{'role':'user','content':'secret prompt'}])
    assert 'secret prompt' not in str(caught.value)


def test_deadline_reaps_process_group(fake):
    (fake/'control.json').write_text(json.dumps({'sleep':True}))
    with pytest.raises(ParccError,match='timed out'):call(timeout_seconds=.2)
    pid=int((fake/'child.pid').read_text())
    for _ in range(30):
        if not os.path.exists(f'/proc/{pid}'):
            break
        time.sleep(.05)
    assert not os.path.exists(f'/proc/{pid}')


def test_wrong_model_rejected_before_launch(fake):
    for model in ('parcc/GLM','gpt-6-sol'):
        with pytest.raises(ParccError,match='model'):
            CodexClient().chat(model=model,messages=[],max_tokens=1)
    assert not (fake/'capture.json').exists()


@pytest.mark.parametrize('effort',['low','medium','xhigh',''])
def test_wrong_effort_rejected_before_launch(fake,effort):
    with pytest.raises(ParccError,match='reasoning effort'):
        CodexClient().chat(model='gpt-6-astra',messages=[],max_tokens=1,reasoning_effort=effort)
    assert not (fake/'capture.json').exists()
