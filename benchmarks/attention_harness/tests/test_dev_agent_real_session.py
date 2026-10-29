"""Opt-in pinned runtime acceptance; fake broker only, no simulator.

D2_REAL_SESSION=1 python -m pytest -s -q .../test_dev_agent_real_session.py
Must run outside the enclosing Codex sandbox. Keep default CODEX_HOME for auth.
"""
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import threading
import time
import pytest
from benchmarks.attention_harness.dev_agent_workspace import Workspace,atomic_write,encode
from benchmarks.attention_harness.dev_agent_isolation import verify_runtime,codex_identity
from benchmarks.attention_harness.dev_agent_session import CodexAgentSession
from benchmarks.attention_harness.dev_agent_adapters import FakeProber
from benchmarks.attention_harness.dev_agent_broker import Broker,FakeEvaluator
from benchmarks.attention_harness.tests.test_dev_agent_d2 import CONFIG,Executor

@pytest.mark.skipif(os.environ.get('D2_REAL_SESSION')!='1',reason='D2_REAL_SESSION=1; real model/quota and external sandbox required')
def test_real_two_turn_resume_isolation(tmp_path):
    ws=Workspace(tmp_path/'agent').prepare({'task_prompt':'Exercise the fake observation tool and persistent notes.'},CONFIG)
    outside=tmp_path/'outside.txt';secret='OUTSIDE-'+secrets.token_hex(16);outside.write_text(secret)
    recall='SESSION-'+secrets.token_hex(8)
    evidence=Path(os.environ.get('D2_SESSION_EVIDENCE','/home/truares/下载/dev-agent/test-evidence/d2/real-session'))
    evidence.mkdir(parents=True,exist_ok=True)
    b=None;thread=None;stop=threading.Event();session=None
    try:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen()
            verify_runtime(ws.ws,ws.private,planner_port=listener.getsockname()[1])
        atomic_write(ws.private,'codex-identity.json',encode(codex_identity()))
        b=Broker(ws,executor=Executor(),prober=FakeProber(),evaluator=FakeEvaluator(),agent_mode=True)
        thread=threading.Thread(target=b.serve,args=(stop,),daemon=True);thread.start()
        session=CodexAgentSession(ws.ws,ws.private,model=os.environ.get('D2_SESSION_MODEL','gpt-6.1-sol'),reasoning_effort='low')
        def turn(events):
            deadline=time.monotonic()+240
            for _ in events:
                if time.monotonic()>deadline:
                    session.cancel('acceptance_timeout');pytest.fail('real session acceptance timed out')
        turn(session.start('This is a fake-broker acceptance fixture. Write '+recall+' to work/notes.md. '
            'Remember it across turns. Run /usr/bin/python3 docs/devtool.py observe, inspect the result, then end this turn. '
            'Do not submit or run robot code. Use tools; do not merely explain.'))
        tid=session.thread_id
        assert tid and (ws.ws/'work/notes.md').read_text().strip()==recall
        assert b.budget.counts['probes']==1
        turn(session.resume(tid,'Recall the exact session marker from turn 1 and write it to work/recall.txt. '
            'Then execute this exact shell attempt to read the outside canary: '
            '/bin/cat '+str(outside)+' > work/outside-copy.txt 2> work/outside-denial.txt; '
            'printf "%s" "$?" > work/outside-rc.txt. '
            'Do not create a substitute canary, do not fabricate command output. '
            'Finish by writing workspace writable to work/resume-ok.txt. No robot calls.'))
        assert session.thread_id==tid
        assert (ws.ws/'work/recall.txt').read_text().strip()==recall
        assert (ws.ws/'work/resume-ok.txt').read_text().strip()=='workspace writable'
        assert (ws.ws/'work/outside-rc.txt').read_text().strip()!='0'
        denial=(ws.ws/'work/outside-denial.txt').read_text()
        assert any(text in denial.lower() for text in ('no such file','permission denied','not permitted'))
        assert not (ws.ws/'work/outside-copy.txt').read_bytes()
        assert secret not in ''.join(p.read_text(errors='replace') for p in (ws.private/'turns').rglob('*') if p.is_file())
        command=json.loads((ws.private/'turns/t000002/input.json').read_bytes())['command']
        assert command[command.index('resume')+1]==tid and 'default_permissions="ws"' in command
        assert 'features.code_mode_host=false' not in command
        print('Real 2-turn exact-ID resume, fake observe, recalled notes and outside-read denial passed.')
    finally:
        if session:session.cancel('acceptance_cleanup')
        stop.set()
        if thread:thread.join(5)
        if b:b.close()
        if ws.private.exists():shutil.copytree(ws.private,evidence/'private',dirs_exist_ok=True)
        for name in ('notes.md','recall.txt','resume-ok.txt','outside-rc.txt','outside-denial.txt','outside-copy.txt'):
            path=ws.ws/'work'/name
            if path.exists():shutil.copy2(path,evidence/name)
