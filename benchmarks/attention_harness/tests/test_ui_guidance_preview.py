"""Read-only HTTP format checks use the runtime's existing compiler."""
import json
import re
import sqlite3
import threading
import time
from dataclasses import replace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from benchmarks.attention_harness.attention_modes import AssistanceMode
from benchmarks.attention_harness.core.runtime import AttentionRuntime
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.hint_compilation import prepare_human_guidance
from benchmarks.attention_harness.tests.test_attention_store import records
from benchmarks.attention_harness.ui_server import create_server


@pytest.fixture
def preview_server(tmp_path):
    path = tmp_path / 'attention.sqlite3'
    store = AttentionStore(path)
    run, attempt, trace, request = records()
    store.create_run(replace(run, assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST))
    store.create_attempt(attempt)
    store.put_trace(trace)
    request = replace(request, mode=AssistanceMode.LIVE_HUMAN_FIRST, deadline_at=time.time()+60)
    AttentionRuntime(store, None, clock=time.time).open_request(request)
    server = create_server(store=store, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    with urlopen(base+'/ui/') as response:
        token = re.search(r'data-control-token="([^"]+)"', response.read().decode()).group(1)

    def post(body, auth=token, request_id=request.request_id):
        return urlopen(Request(base+'/api/requests/'+request_id+'/guidance-preview',
            method='POST', data=json.dumps(body).encode(), headers={
                'Content-Type':'application/json', 'X-Attention-Token':auth}))

    def dump():
        with sqlite3.connect(path) as connection:
            return '\n'.join(connection.iterdump())

    try:
        yield store, request, run, post, dump
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize('content', [
    'AB_CONTROL_V1 grasp_offset_m=0.005;close_settle_steps=30;',
    'Check the grasp height and lower it slightly. Close the gripper and confirm a secure grip before lifting.',
    '降低抓取高度，夹紧后确认抓牢',
    '请想办法完成任务',
    'AB_CONTROL_V1 grasp_offset_m=99;',
    'AB_CONTROL_V1 grasp_offset_m=0.005;grasp_offset_m=0.01;',
])
def test_guidance_preview_matches_existing_compiler_without_any_store_write(preview_server, content):
    store, request, run, post, dump = preview_server
    before = dump()
    with post({'content':content}) as response:
        result = json.load(response)
    expected = prepare_human_guidance(content.strip(), suite=run.suite, task_id=run.task_id)
    assert result == {'run_id':run.run_id, 'request_id':request.request_id,
                      'status':expected['status'], 'parameters':expected['parameters'], 'rule':expected['rule']}
    assert dump() == before
    assert store.get_request(request.request_id).state.value == 'pending'


@pytest.mark.parametrize('body,auth,request_id,status', [
    ({'content':'hello'}, 'wrong', None, 403),
    ({'content':'hello'}, None, 'missing-request', 404),
    ({'content':''}, None, None, 400),
    ({'content':7}, None, None, 400),
    ({'content':'x'*4001}, None, None, 400),
    ({'content':'hello','suite':'robocasa'}, None, None, 400),
])
def test_guidance_preview_rejects_invalid_inputs_without_store_write(preview_server, body, auth, request_id, status):
    _, request, _, post, dump = preview_server
    before = dump()
    kwargs = {'request_id':request_id or request.request_id}
    if auth is not None:
        kwargs['auth'] = auth
    with pytest.raises(HTTPError) as caught:
        post(body, **kwargs)
    assert caught.value.code == status
    assert dump() == before
