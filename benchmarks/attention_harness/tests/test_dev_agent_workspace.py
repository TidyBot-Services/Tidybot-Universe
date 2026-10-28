"""Offline operator projection contract, using the D5 broker/ledger fixture."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re

import pytest
from benchmarks.attention_harness.tests import test_dev_agent_d5 as d5
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment, offline_dev_identity
from benchmarks.attention_harness.dev_agent_view import artifact, projection
from benchmarks.attention_harness.dev_agent_ui_projection import wire, version_evidence, error_groups
from benchmarks.attention_harness.dev_agent_workspace import atomic_write, encode


@pytest.fixture
def workspace_run(tmp_path, offline_dev_identity, monkeypatch):
    class Executor(d5.Executor):
        def execute(self, *args, **kwargs):
            value = super().execute(*args, **kwargs)
            value.update(frames=[dict(filename='frames/overview-000000-sim-0.000000s.png',
                view='overview', index=0, sim_seconds=0, physical_sequence=0,
                data=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5XcAAAAASUVORK5CYII='))],
                state_log=[dict(physics_step=1,qpos=[0.,.1]),dict(physics_step=2,qpos=[.1,.2])])
            return value
    monkeypatch.setattr(d5, 'Executor', Executor)
    store, cell, ws = d5.fixture_run(tmp_path, offline_dev_identity, 'fatal_error')
    for v in cell['versions']:
        episode = ws.private/'executions'/v['execution_id']/'openarm365-dev-fixture'
        episode.mkdir(parents=True)
        atomic_write(episode, 'camera_recording.json', encode(dict(video_saved=True,
            frame_manifest_sha256='a'*64, replay_timing=dict(frame_trace_seconds=[1],frame_video_seconds=[0]))))
        atomic_write(episode, 'camera_views.json', encode(dict(views={
            'external':dict(status='saved',video='advisor_replay.mp4',label='Overview'),
            'side':dict(status='saved',video='advisor_replay_side.mp4',label='Side')})))
        for name in ('advisor_replay.mp4','advisor_replay_side.mp4'):
            atomic_write(episode, name, b'offline fake recording bytes')
    ws.publish('versions/v0001/help.json',dict(status='unavailable', error='public_view_unavailable',
        requested=False, delivered=False))
    atomic_write(ws.private, 'help/v0001/error.json', encode(dict(error='evaluation-derived text rejected at Dev boundary')))
    ws.publish('versions/v0002/help.json',dict(status='unavailable',error='ValueError',delivered=True,
        hint_human_seconds=1231.960857142857,content='Try a smaller offset.'))
    atomic_write(ws.private, 'help/v0002/error.json', encode(dict(error='invalid_usage')))
    atomic_write(ws.private, 'help/v0002/advisor.json', encode(dict(hint_human_seconds=274.818)))
    ws.publish('versions/v0003/help.json',dict(status='not_requested'))
    ledger = d5.AgentLedger(store.path, cell['key'])
    ledger.reserve('help:v0002', 'help', {'help': 1})
    ledger.claim('help:v0002')
    ledger.settle('help:v0002', dict(version_id='v0002'))
    store.update_cell(cell['key'], agent_resources=ledger.snapshot())
    cell = store.cell(cell['key'])
    return store, cell, ws


def test_compatible_projection_and_digest_bound_evidence(workspace_run):
    store, cell, ws = workspace_run
    before = store.path.read_bytes()
    view = projection(store, cell); compat = view['dev_v2_compat']
    assert len(compat['versions']) == 3
    assert {j['kind'] for j in compat['jobs']} == {'execute','eval','advisor'}
    assert all(j['state']=='settled' for j in compat['jobs'])
    assert compat['ledger']['goal']['wall_used'] + compat['ledger']['goal']['wall_remaining'] == 21600
    # Parity with a finished one-call cell: no lifecycle, ended run controls.
    assert compat['lifecycle'] is None and compat['run_controls']['state']=='ended'
    assert compat['run_controls']['can_resume'] is False
    files = {}
    for version in compat['versions']:
        assert version['source_text'] and version['sources']
        assert version['evaluator_review']['evidence'] and version['evaluator_review']['reference_suggestion']
        assert version['frames'] and version['instrument']
        _, assets = version_evidence(cell, version)
        for name, value in assets.items():
            logical = f'compat/{version["version_id"]}/{name}'
            result, mime = artifact(cell, logical)
            raw = result if isinstance(result, bytes) else result.read_bytes() if isinstance(result, Path) else result.encode() if isinstance(result,str) else wire(result)
            files['agent-view/'+logical] = dict(base64=base64.b64encode(raw).decode(),mime=mime)
        for key in ('source','eval_review','help','bundle','trace','frames','instrument'):
            ref = version[key]
            if ref is None:
                continue
            assert hashlib.sha256(base64.b64decode(files[ref['uri']]['base64'])).hexdigest()==ref['sha256']
        frames = artifact(cell, f'compat/{version["version_id"]}/frames.json')[0]
        image, mime = artifact(cell, f'executions/{version["execution_id"]}/'+frames[0]['filename'])
        assert mime=='image/png' and hashlib.sha256(image).hexdigest()==frames[0]['sha256']
    text = json.dumps(view)
    assert str(ws.root) not in text and '/home/' not in text and '/tmp/' not in text and 'private/' not in text
    forbidden = re.compile(r'native_success|object_in_sink|native_result|check_success|reward')
    for path in ws.ws.rglob('*'):
        if path.is_file() and path.suffix!='.png':
            assert not forbidden.search(path.read_text()), path
    assert store.path.read_bytes()==before
    if os.getenv('UI_AGENT_FIXTURE'):
        Path(os.environ['UI_AGENT_FIXTURE']).write_text(json.dumps(dict(view=view,files=files),ensure_ascii=False,indent=2)+'\n')


def test_media_and_virtual_routes_fail_closed(workspace_run, tmp_path):
    store, cell, ws = workspace_run
    version, assets = version_evidence(cell, cell['versions'][0])
    video = assets['advisor_replay.mp4']['uri'].removeprefix('agent-view/')
    assert artifact(cell, video)[1]=='video/mp4'
    episode = ws.private/'executions'/version['execution_id']/'openarm365-dev-fixture'
    (episode/'advisor_replay.mp4').write_bytes(b'tampered')
    with pytest.raises(FileNotFoundError,match='digest'):
        artifact(cell,video)
    (episode/'advisor_replay.mp4').unlink()
    (episode/'advisor_replay.mp4').symlink_to(tmp_path/'outside')
    with pytest.raises((FileNotFoundError,ValueError)):
        artifact(cell,video)
    for name in ('compat/v9999/bundle.json','compat/v0001/../../config.json',
                 'compat/v0001/native_result.json','replays/e000002/'+ 'a'*64+'/config.json'):
        with pytest.raises(FileNotFoundError):artifact(cell,name)
    frame = ws.ws/'executions'/version['execution_id']/'frames/overview-000000-sim-0.000000s.png'
    frame.write_bytes(b'tampered')
    with pytest.raises(FileNotFoundError,match='digest'):
        artifact(cell,str(frame.relative_to(ws.ws)))


def test_absent_evidence_is_explicit_and_reads_never_create_files(workspace_run):
    store, cell, ws = workspace_run
    before = sorted(str(p.relative_to(ws.root)) for p in ws.root.rglob('*'))
    view = projection(store,cell)
    versions = view['dev_v2_compat']['versions']
    assert versions[0]['help_content'] is None
    assert versions[1]['help_content']['rejected'] is True
    assert versions[2]['help_content']['content']=='Try a smaller offset.'
    assert sorted(str(p.relative_to(ws.root)) for p in ws.root.rglob('*'))==before
    running = projection(store, dict(cell,state='running',reason=None))['dev_v2_compat']
    assert running['lifecycle']['can_stop'] is True


def test_help_boundaries_cost_and_non_requests(workspace_run):
    store, cell, ws = workspace_run
    original = {str(p): p.read_bytes() for p in ws.root.rglob('*') if p.is_file()}
    compat = projection(store, cell)['dev_v2_compat']
    refused, answered = compat['help_intervals']
    assert [(h['after_version'], h['version']) for h in compat['help_intervals']] == [(1, 2), (2, 3)]
    assert refused['help_content']['rejected'] and not refused['help_content']['delivered']
    assert refused['help_content']['reason'] == 'evaluation-derived text rejected at Dev boundary'
    assert answered['help_content']['delivered'] and not answered['help_content']['rejected']
    assert answered['help_content']['hint_human_seconds'] == 274.818
    assert answered['help_content']['recorded_hint_human_seconds'] == 1231.960857142857
    assert [j['version'] for j in compat['jobs'] if j['kind']=='advisor'] == [2, 3]
    assert original == {str(p): p.read_bytes() for p in ws.root.rglob('*') if p.is_file()}
    # No correction for ordinary delivery, and no phantom request for ineligibility.
    ws.publish('versions/v0002/help.json',dict(status='delivered',hint_human_seconds=42.5,content='Recorded hint'))
    ws.publish('versions/v0003/help.json',dict(status='not_eligible'))
    compat = projection(store, cell)['dev_v2_compat']
    assert len(compat['help_intervals']) == 2
    assert compat['versions'][2]['help_content']['hint_human_seconds'] == 42.5
    assert 'cost_corrected' not in compat['versions'][2]['help_content']
    (ws.private/'help/v0002/advisor.json').unlink()
    ws.publish('versions/v0002/help.json',dict(status='unavailable',error='ValueError',delivered=True,
        hint_human_seconds=1231.96,content='Recorded hint'))
    fallback = projection(store, cell)['dev_v2_compat']['versions'][2]['help_content']
    assert fallback['hint_human_seconds'] == 1231.96 and 'cost_corrected' not in fallback
    # A final boundary remains visible without fabricating a fourth formal version.
    ws.publish('versions/v0003/help.json',dict(status='delivered',content='Unused final hint'))
    compat = projection(store, cell)['dev_v2_compat']
    assert len(compat['versions']) == 3 and compat['help_intervals'][-1]['version'] == 4


def test_failures_deduplicate_per_execution_and_routine_details_collapse():
    errors = [dict(source='executions/e000003/controller-error.json',error=dict(error='fatal_sdk_error')),
        dict(source='ask',error='public_view_unavailable'),
        dict(source='submit',error='execution_failed'),
        dict(source='e000003 action',error=dict(error=dict(message='Grasp failed'),status='failed')),
        dict(source='e000003 action',error=dict(status='rejected')),
        dict(source='eval/v0001/stderr',error='Reading additional input from stdin…')]
    failures, details = error_groups(errors+errors, [dict(id='submit',execution_id='e000003'),dict(id='ask',op='ask_help')])
    assert len(failures) == 1 and failures[0]['source'] == 'e000003 action'
    assert len(failures[0]['reports']) == 3
    assert len(details) == 3
    assert {d['source'] for d in details} == {'ask','e000003 action','eval/v0001/stderr'}


def test_replay_range_request_returns_206(workspace_run, tmp_path, monkeypatch):
    # Over an SSH tunnel a full-body 200 blocked seeking until the whole mp4 arrived.
    from benchmarks.attention_harness import ui_server
    from benchmarks.attention_harness.tests.test_daily_bridge_compatibility import OfflineServer, request
    store, cell, ws = workspace_run
    version, assets = version_evidence(cell, cell['versions'][0])
    name = assets['advisor_replay.mp4']['uri'].removeprefix('agent-view/')
    result, mime = artifact(cell, name)
    assert isinstance(result, Path) and mime == 'video/mp4'
    monkeypatch.setattr(ui_server, 'ThreadingHTTPServer', OfflineServer)
    server = ui_server.create_server(dev_v2_root=tmp_path, dev_v2_per_run_budget=True)
    url = f"/api/dev-v2/experiments/{cell['experiment_id']}/cells/{cell['key']}/agent/files/{name}"
    size = len(b'offline fake recording bytes')
    status, payload, headers = request(server, url, headers={'Range': 'bytes=0-9'})
    assert status == 206 and payload == b'offline fa'
    assert headers['Content-Range'] == f'bytes 0-9/{size}' and headers['Accept-Ranges'] == 'bytes'
    status, payload, headers = request(server, url, headers={'Range': 'bytes=8-'})
    assert status == 206 and payload == b'offline fake recording bytes'[8:]
    assert request(server, url, headers={'Range': f'bytes={size}-'})[0] == 416
    status, payload, headers = request(server, url)
    assert status == 200 and len(payload) == size and headers['Accept-Ranges'] == 'bytes'


def test_view_payload_is_slim_and_terminal_projection_cached(workspace_run, monkeypatch):
    from benchmarks.attention_harness import dev_agent_view
    store, cell, ws = workspace_run
    first = projection(store, cell)
    assert all('result' not in r for r in first['agent']['timeline'] if r.get('kind')!='help')
    assert any(r.get('version_id') for r in first['agent']['timeline'])
    calls = []
    real = dev_agent_view._projection
    monkeypatch.setattr(dev_agent_view, '_projection', lambda *a, **kw: calls.append(1) or real(*a, **kw))
    dev_agent_view._CACHE.clear()
    second = projection(store, cell); third = projection(store, cell)
    assert len(calls) == 1 and second == third == first
    third['agent']['timeline'].clear()
    assert projection(store, cell) == first
