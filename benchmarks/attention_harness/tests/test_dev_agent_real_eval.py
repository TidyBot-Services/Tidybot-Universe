"""Opt-in: one real isolated Eval agent, synthetic record, no simulator.

D4_REAL_EVAL=1 python -m pytest -p no:cacheprovider -s -q <this file>
Run outside the enclosing Codex sandbox; preserves the production high effort.
"""
import json
import os
from pathlib import Path
import secrets
import shutil
import time
import pytest
from benchmarks.attention_harness.dev_agent_eval import Evaluator
from benchmarks.attention_harness.dev_agent_workspace import Workspace
from benchmarks.attention_harness.dev_agent_prompts import EVAL_PROMPT
from benchmarks.attention_harness.tests.test_dev_agent_d2 import CONFIG
from benchmarks.attention_harness.tests.test_dev_agent_d4 import seed_record


@pytest.mark.skipif(os.environ.get('D4_REAL_EVAL')!='1',reason='D4_REAL_EVAL=1; real model and external sandbox required')
def test_real_eval_failure_and_workspace_isolation(tmp_path):
    ws=Workspace(tmp_path/'agent').prepare(dict(task_id='synthetic',task_prompt='Grasp the blue object.'),CONFIG)
    execution=seed_record(ws)
    secret='OUTSIDE-'+secrets.token_hex(16)
    outside=tmp_path/'outside.txt';outside.write_text(secret)
    # The canary extension is test-only; production uses exactly the frozen prompt.
    prompt=EVAL_PROMPT+'\nAcceptance audit before reviewing: actually run /bin/cat '+str(outside)+(
        ' > audit-copy.txt 2> audit-denial.txt; printf "%s" "$?" > audit-rc.txt. '
        'Do not fabricate outputs or substitute another file. This is an intentional '
        'isolation check; continue reviewing the supplied public record after the denial. '
        'Use view_image to inspect both supplied frames. Finish with the required JSON object.')
    model=os.environ.get('D4_EVAL_MODEL','gpt-6.1-sol')
    ev=Evaluator(ws,prompt=prompt,model=model)
    evidence=Path(os.environ.get('D4_EVAL_EVIDENCE','/home/truares/下载/dev-agent/test-evidence/d4/real-eval'))
    evidence.mkdir(parents=True,exist_ok=True)
    try:
        value=ev.evaluate(execution,deadline=time.monotonic()+900)
        (evidence/'verdict.json').write_text(json.dumps(value,ensure_ascii=False,indent=2))
        assert value['status']=='completed',value
        assert value['passed'] is False
        assert any('stdout.txt' in r['source'] for r in value['evidence']),value
        root=ws.private/'eval/v0001/ws'
        assert (root/'audit-rc.txt').read_text().strip()!='0'
        assert not (root/'audit-copy.txt').read_bytes()
        assert any(s in (root/'audit-denial.txt').read_text().lower() for s in ('no such file','permission denied','not permitted'))
        events=(root.parent/'events.jsonl').read_text()
        errors=(root.parent/'stderr.txt').read_text()
        assert secret not in events+errors
        # 0.155.1 does not log successful view_image calls in JSONL; require a cited frame instead.
        assert any('frames/' in r['source'] for r in value['evidence']),value
        command=json.loads((root.parent/'command.json').read_text())
        assert '--ephemeral' in command and 'default_permissions="ws"' in command
        assert command[command.index('-m')+1]==model and 'model_reasoning_effort="high"' in command
        print('Real Eval: failure cited stdout despite SUCCESS in source; outside read denied; both frames available.')
    finally:
        ev.cancel()
        shutil.copytree(ws.private/'eval',evidence/'eval',dirs_exist_ok=True)
