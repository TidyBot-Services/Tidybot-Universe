"""Read-only daily and main-bridge display projections; no worker or model."""
import json
import subprocess
from pathlib import Path

import pytest

from benchmarks.attention_harness.daily_dev_v2 import DailyDevV2
from benchmarks.attention_harness.dev_main_bridge import DevMainBridge
from benchmarks.attention_harness.dev_loop import sha
from benchmarks.attention_harness.dev_request_view import display_eval_review
from benchmarks.attention_harness.core.dev_store import DevConflict

# Derived from devexp-f31d9d45ebc4a5a18ab93f7e; image data URLs and long prose are trimmed.
FIXTURE = Path(__file__).parent / 'fixtures' / 'request_view_real_shape_20261019'


def test_daily_and_bridge_show_the_same_verified_review_and_request(tmp_path):
    def artifact(name,value):
        path=tmp_path/name
        path.write_text(json.dumps(value) if not isinstance(value,str) else value)
        return {'uri':str(path),'sha256':sha(path)}

    view=json.loads((FIXTURE/'advisor-input.json').read_text())
    assert all('data_url' not in frame and len(frame['sha256'])==64 for frame in view['frames'])
    review=artifact('eval-review.json',{'status':'completed',**view['eval_review']})
    request=artifact('advisor-input.json',view)
    advisor=json.loads((FIXTURE/'advisor-hint.json').read_text())
    advisor['request_view']=request
    advisor_ref=artifact('advisor-hint.json',advisor)
    source=artifact('policy.py','pass\n')
    diff=artifact('diff.patch','+new code\n')
    decision=artifact('decision.json',{'action':'request'})
    help_content=json.loads((FIXTURE/'help.json').read_text())
    assert help_content['provider']=='human_proxy' and 'request_view' not in help_content and 'request' not in help_content
    help_content['advisor_proxy']=advisor_ref
    help_ref=artifact('help.json',help_content)
    cell={'key':'cell','versions':[{'version':1,'source':source,'diff':diff,'decision':decision,'help':help_ref}],
          'jobs':[{'kind':'eval','version':1,'state':'settled','receipt':{'eval_review':review}}]}
    class Store:
        def export_ledger(self,*_):return {'recompute_matches':True}
    daily=DailyDevV2.__new__(DailyDevV2)
    bridge=DevMainBridge.__new__(DevMainBridge)
    bridge.store=Store();bridge.goal_id='goal';bridge.lifecycle=lambda _:{}
    before={p:p.read_bytes() for p in tmp_path.iterdir()}
    daily_row=daily.view(Store(),cell)['versions'][0]
    bridge_row=bridge.view(cell)['versions'][0]
    for row in (daily_row,bridge_row):
        assert row['help_content']['provider']=='human_proxy'
        assert row['evaluator_review']['reason']==view['eval_review']['reason']
        assert row['evaluator_review']['reference_suggestion']==view['eval_review']['reference_suggestion']
        items={item['label']:item['value'] for item in row['request_view']['items']}
        assert items['Task']==view['task']['task_prompt'] and items['Question']==view['question']
        assert 'Dev reasoning' not in items
        assert items['Overview frames']=='2'
        assert all(items[name+' frames']=='1' for name in ('Side','Top-down','Left wrist','Right wrist'))
        assert items['Shown video duration']=='44.741 s' and items['Steps']=='4'
        assert items['Step failure']=='Moved the arm a small step — Stopped: the action took too long'
        assert items['Diff included']=='Yes' and items['Eval verdict']=='Not completed'
        assert 'data_url' not in json.dumps(row['request_view'])
        assert row['request_view']['never_shown']=='Never shown: simulator ground truth and the native success score'
    script=r'''
const fs=require('fs'),vm=require('vm');
const source=fs.readFileSync(process.argv[1],'utf8');
const block=source.slice(source.indexOf('  // Operator presentation:'),source.indexOf('  // End operator presentation.'));
const esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const render=vm.runInNewContext(block+'\noperatorRequestView',{DEBUG_VIEW:false,esc});
process.stdout.write(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map((view,i)=>render(view,'help-'+i))));
'''
    live_js=Path(__file__).parent.parent/'ui'/'live.js'
    rendered=subprocess.run(['node','-e',script,str(live_js)],input=json.dumps([daily_row['request_view'],bridge_row['request_view']]),
                            text=True,capture_output=True,check=True)
    for html in json.loads(rendered.stdout):
        assert html.count('data-request-view-item')==len(daily_row['request_view']['items'])
        assert 'data-request-view ' in html
    assert {p:p.read_bytes() for p in tmp_path.iterdir()}==before

    Path(request['uri']).write_text('{}')
    with pytest.raises(DevConflict,match='[Dd]igest|drift'):
        daily.view(Store(),cell)
    with pytest.raises(DevConflict,match='[Dd]igest|drift'):
        bridge.view(cell)
    Path(request['uri']).write_bytes(before[Path(request['uri'])])
    Path(advisor_ref['uri']).write_text('{}')
    with pytest.raises(DevConflict,match='[Dd]igest|drift'):
        daily.view(Store(),cell)
    with pytest.raises(DevConflict,match='[Dd]igest|drift'):
        bridge.view(cell)


def test_old_help_without_request_view_has_no_panel(tmp_path):
    def artifact(name,value):
        path=tmp_path/name
        path.write_text(json.dumps(value) if not isinstance(value,str) else value)
        return {'uri':str(path),'sha256':sha(path)}
    source=artifact('policy.py','pass\n')
    diff=artifact('diff.patch','')
    decision=artifact('decision.json',{'action':'request'})
    help_ref=artifact('help.json',{'provider':'advisor','content':'Old answer',
                                   'request':{'uri':'legacy-model-request.json','sha256':'a'*64}})
    cell={'key':'cell','versions':[{'version':1,'source':source,'diff':diff,'decision':decision,'help':help_ref}], 'jobs':[]}
    class Store:
        def export_ledger(self,*_):return {}
    daily=DailyDevV2.__new__(DailyDevV2)
    bridge=DevMainBridge.__new__(DevMainBridge)
    bridge.store=Store();bridge.goal_id='goal';bridge.lifecycle=lambda _:{}
    assert daily.view(Store(),cell)['versions'][0]['request_view'] is None
    assert bridge.view(cell)['versions'][0]['request_view'] is None


def test_legacy_display_fixture_request_view_still_resolves(tmp_path):
    view=json.loads((FIXTURE/'advisor-input.json').read_text())
    path=tmp_path/'advisor-input.json';path.write_text(json.dumps(view))
    from benchmarks.attention_harness.dev_request_view import request_view_ref
    ref={'uri':str(path),'sha256':sha(path)}
    assert request_view_ref({'request_view':ref},lambda _:pytest.fail('unexpected advisor read'))==ref


def test_legacy_review_projection_marks_feedback_without_rewriting_artifact(tmp_path):
    path=tmp_path/'evaluator-review.json'
    path.write_text(json.dumps({'status':'completed','passed':False,'feedback':'Legacy failure'}))
    original=path.read_bytes()
    review=display_eval_review({'jobs':[]},1,legacy_path=path)
    assert review['legacy_review'] is True and review['feedback']=='Legacy failure'
    assert path.read_bytes()==original
