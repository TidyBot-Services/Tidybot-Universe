import json,copy
from pathlib import Path
import pytest
from benchmarks.attention_harness.dev_experiment import *
from benchmarks.attention_harness.dev_v2_loop import *
from openarm365_dev_v2.tests.fakes import Client,Runner,EvalRunner,FailedEvalClient

def setup(tmp_path,policy='autonomous',key='loop-test',eval_enabled=False):
    selection=dict(request_key=key,tasks=['open_cabinet'],seeds=[102],policies=[policy])
    if eval_enabled:selection['eval_agent']={'enabled':True,'model':'parcc/Qwen'}
    s=DevV2Store(tmp_path/'s.sqlite');e=create(s,tmp_path/'artifacts',selection)
    budget=tmp_path/'budget.json';budget.write_text(json.dumps({'goal':GOAL,'budget_tier':'dev','limits':{'dev_calls':28,'advisor_calls':14,'provider_tokens':500000,'executions':28,'wall_seconds':s.goal()['wall_limit']}}))
    return s,e,budget

def test_fake_complete_revision_keeps_previous_and_billing(tmp_path):
    s,e,b=setup(tmp_path);client=Client()
    final=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=client,runner_factory=Runner)
    c=final['cells'][0];assert len(c['versions'])==2 and c['state']=='completed'
    packet=json.loads(client.inputs[1]['messages'][1]['content'])
    assert packet['locked_task']['task_id']=='open_cabinet' and packet['locked_task']['seed']==102
    assert packet['previous_complete_source']==Path(c['versions'][0]['source']['uri']).read_text()
    assert 'IK planning rejected' in str(packet['public_feedback'])
    assert all('native_success' not in m['content'] for i in client.inputs for m in i['messages'])
    assert s.goal()['used']['dev_calls']==2 and s.goal()['used']['executions']==2
    assert s.export_ledger()['recompute_matches']
    count=len(client.inputs);run(s,e['experiment_id'],max_cells=1,budget_file=b,client=client,runner_factory=Runner)
    assert len(client.inputs)==count

def test_format_failure_paid_and_revised_without_invalid_execution(tmp_path):
    s,e,b=setup(tmp_path);client=Client(True)
    final=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=client,runner_factory=Runner)
    c=final['cells'][0];assert not c['versions'][0]['valid'] and c['versions'][1]['valid']
    assert s.goal()['used']['provider_tokens']==200 and s.goal()['used']['executions']==1
    assert 'format invalid' in json.loads(client.inputs[1]['messages'][1]['content'])['public_feedback']['public_error']

def test_independent_success_never_early_stops(tmp_path):
    s,e,b=setup(tmp_path)
    class SuccessRunner(Runner):native_success=True
    final=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=Client(),runner_factory=SuccessRunner)
    assert len(final['cells'][0]['versions'])==2 and s.goal()['used']['executions']==2
@pytest.mark.parametrize('policy',['demo_first','reactive_help'])
def test_actual_help_job_in_simulated_loop(tmp_path,policy):
    s,e,b=setup(tmp_path,policy,key='help-loop-'+policy,eval_enabled=policy=='reactive_help')
    client=FailedEvalClient()
    final=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=client,runner_factory=EvalRunner)
    assert final['cells'][0]['used']['help_count']==1
    if policy=='reactive_help':assert 'eval' in client.role_calls and 'advisor' in client.role_calls
    assert any(j['kind']==('demo' if policy=='demo_first' else 'advisor') for j in final['cells'][0]['jobs'])

def test_unknown_no_retry_and_reservation_retained(tmp_path):
    s,e,b=setup(tmp_path)
    class UnknownClient(Client):
        def chat(self,**kw):self.inputs.append(kw);raise TimeoutError('response unknown')
    client=UnknownClient()
    with pytest.raises(TimeoutError):run(s,e['experiment_id'],max_cells=1,budget_file=b,client=client,runner_factory=Runner)
    c=s.cell(e['cells'][0]['key']);assert c['state']=='needs_review' and s.goal()['reserved']['dev_calls']==1
    assert len(client.inputs)==1
