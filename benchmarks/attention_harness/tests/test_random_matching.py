from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
from benchmarks.attention_harness.core.models import RunStatus, RequestType, RequestPriority
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.random_matching import derive_random_config, main, PLANNER
from benchmarks.attention_harness.tests.test_attention_store import records


def source(tmp_path,terminal=True):
    directory=tmp_path/'source';directory.mkdir()
    store=AttentionStore(tmp_path/'source.sqlite')
    run,attempt,trace,request=records()
    run=replace(run,run_id='run:source',policy_id=PLANNER)
    attempt=replace(attempt,run_id=run.run_id)
    trace=replace(trace,run_id=run.run_id)
    request=replace(request,run_id=run.run_id)
    store.create_run(run);store.create_attempt(attempt);store.put_trace(trace);store.create_request(request)
    interrupt=replace(request,request_id='interrupt',request_type=RequestType.INTERRUPT,priority=RequestPriority.CRITICAL)
    store.create_request(interrupt)
    store.record_task_plan(run.run_id,max_attempts=4)
    store.transition_run(run.run_id,RunStatus.RUNNING,event_key='start')
    if terminal:store.transition_run(run.run_id,RunStatus.FAILED,event_key='end')
    value={'suite':run.suite,'task_id':run.task_id,'seed':run.seed,'policy_id':run.policy_id,
           'artifact_dir':str(directory),'store':str(store.path),
           'requests':[{'request_id':request.request_id},{'request_id':interrupt.request_id}]}
    path=directory/'attention_run.json';path.write_text(json.dumps(value))
    return path,store


def test_count_excludes_interrupt_and_cli_emits_exact_config_sha(tmp_path,capsys):
    path,store=source(tmp_path)
    before=store.path.read_bytes()
    result=derive_random_config(path,suite='robosuite',task_id='cube_lift',seed=101)
    assert result['policy_config']=={'target_request_count':1,'total_failure_slots':3,'seed':101}
    assert result['excluded_interrupt_ids']==['interrupt']
    out=tmp_path/'out'
    assert main(['--source-run',str(path),'--suite','robosuite','--task','cube_lift','--seed','101','--output-dir',str(out)])==0
    assert hashlib.sha256((out/'policy_config.json').read_bytes()).hexdigest()==result['policy_config_sha256']
    assert json.loads((out/'match.json').read_text())==result
    assert store.path.read_bytes()==before


@pytest.mark.parametrize('changes',[{'suite':'robocasa'},{'task_id':'other'},{'seed':102}])
def test_reject_other_cell(tmp_path,changes):
    path,_=source(tmp_path)
    with pytest.raises(ValueError,match='mismatch'):
        derive_random_config(path,**({'suite':'robosuite','task_id':'cube_lift','seed':101}|changes))


def test_reject_nonterminal_source(tmp_path):
    path,_=source(tmp_path,terminal=False)
    with pytest.raises(ValueError,match='not terminal'):
        derive_random_config(path,suite='robosuite',task_id='cube_lift',seed=101)


def test_reject_summary_request_omission(tmp_path):
    path,_=source(tmp_path)
    value=json.loads(path.read_text());value['requests'].pop();path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='identities differ'):
        derive_random_config(path,suite='robosuite',task_id='cube_lift',seed=101)
