"""All-role offline worker: pipe -> providers -> fake Codex/Advisor -> artifacts."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import time
from benchmarks.attention_harness.dev_agent_key_vault import install_run_keys_from_fd,run_role
from benchmarks.attention_harness.dev_agent_loop import providers_for_run,advisor_client
from benchmarks.attention_harness.dev_agent_workspace import Workspace
from benchmarks.attention_harness.dev_agent_session import CodexAgentSession
from benchmarks.attention_harness.dev_agent_eval import Evaluator
from benchmarks.attention_harness.tests.test_dev_agent_d2 import CONFIG
from benchmarks.attention_harness.tests.test_dev_agent_d4 import seed_record


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--key-fd',type=int,required=True)
    args=parser.parse_args();install_run_keys_from_fd(args.key_fd)
    selected={r:{k:v for k,v in run_role(r).items() if k!='base_url'} for r in ('dev','eval','advisor')}
    manifest=dict(agent_provider=dict(roles=selected))
    providers=providers_for_run(manifest,allow_private_hosts=('127.0.0.1',))
    root=Path(args.output);ws=Workspace(root/'agent').prepare(dict(task_id='fake',task_prompt='Move.'),CONFIG)
    script=Path(__file__).with_name('byok_p2_codex.py')
    factory=lambda path,prompt:[sys.executable,'-B',str(script),'exec','-C',str(path),'--json','-m','old','-c','model_reasoning_effort="low"',prompt]
    dev=CodexAgentSession(ws.ws,ws.private,provider=providers['dev'],model=selected['dev']['model'],command_factory=factory)
    list(dev.start('Inspect text'))
    evaluator=Evaluator(ws,provider=providers['eval'],command_factory=factory)
    value=evaluator.evaluate(seed_record(ws),deadline=time.monotonic()+20)
    assert value['status']=='completed'
    client=advisor_client(None,{},provider=providers['advisor'])
    response=client.chat(model=selected['advisor']['model'],messages=[dict(role='user',content=[dict(type='image_url',image_url=dict(url='data:image/png;base64,aW1hZ2U='))])],max_tokens=32)
    (root/'advisor.json').write_text(json.dumps(response.artifact()))
    (root/'eval.json').write_text(json.dumps(value))
    with sqlite3.connect(root/'run.sqlite') as db:
        db.execute('CREATE TABLE results (value TEXT)');db.execute('INSERT INTO results VALUES (?)',(json.dumps(value),));db.commit()


if __name__=='__main__':main()
