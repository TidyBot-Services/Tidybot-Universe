"""Offline run substitute: inherited key pipe, real session command and bridge."""
import argparse
import json
import os
from pathlib import Path
import secrets
import sqlite3
import urllib.request
from types import SimpleNamespace
from benchmarks.attention_harness.dev_agent_key_vault import harden_process,install_run_keys_from_fd,run_key,run_role,KEY_ENV
from benchmarks.attention_harness.dev_agent_loop import provider_for_run
from benchmarks.attention_harness.dev_agent_session import CodexAgentSession


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--key-fd',type=int,required=True)
    parser.add_argument('--allow-private-provider-hosts',default='')
    args=parser.parse_args();harden_process();install_run_keys_from_fd(args.key_fd)
    assert not (set(KEY_ENV)&set(os.environ))
    role=run_role('dev')
    root=Path(args.output);root.mkdir(parents=True,exist_ok=True);private=root/'private';private.mkdir()
    (root/'CODEX_HOME').mkdir();(root/'Memory').mkdir()
    provider=provider_for_run(dict(agent_provider=dict(roles=dict(dev=role))),allow_private_hosts=tuple(args.allow_private_provider_hosts.split(',')));token=provider.token
    factory=lambda ws,prompt:['codex','exec','-C',str(ws),'--json','-m','old','-c','model_reasoning_effort="low"',prompt]
    session=CodexAgentSession(root,private,model=role['model'],provider=provider,command_factory=factory)
    try:
        command=session.command(False)
        inputs=[dict(type='message',role='user',content='Use ping')]
        for turn in range(1,4):
            directory=private/'turns'/('t%06d'%turn);directory.mkdir(parents=True)
            (directory/'input.json').write_text(json.dumps(dict(command=command,prompt='Use ping')))
            body=dict(model=role['model'],input=inputs,tools=[dict(type='function',name='ping',parameters=dict(type='object',properties={}))])
            req=urllib.request.Request(session.bridge.base_url+'/responses',data=json.dumps(body).encode(),
                                       headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
            with urllib.request.urlopen(req,timeout=10) as response:events=response.read()
            assert b'response.completed' in events and b'function_call' in events
            (directory/'events.jsonl').write_bytes(events);(directory/'stderr.txt').write_text('')
            inputs.extend([dict(type='function_call',name='ping',call_id='call_test',arguments='{}'),
                           dict(type='function_call_output',call_id='call_test',output='ok')])
        for name in ('scheduler.log','trace.json','journal.json','ledger.json','evidence.json','Memory/note','CODEX_HOME/session.json'):
            (root/name).write_text('{}')
        with sqlite3.connect(root/'run.sqlite') as db:
            db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE events (value TEXT)')
            db.execute('INSERT INTO events VALUES (?)',('run completed',));db.commit()
        db.close()
    finally:session.close()


if __name__=='__main__':main()
