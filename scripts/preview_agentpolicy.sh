#!/usr/bin/env bash
# Display-only fake fixtures. Run manually for Claude's browser review.
set -euo pipefail
WORKTREE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
PREVIEW_ROOT=/tmp/agentpolicy-preview
PREVIEW_PYTHON=${PREVIEW_PYTHON:-/home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python}
cd -- "$WORKTREE"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$WORKTREE${PYTHONPATH:+:$PYTHONPATH}"
# The generator writes only this dedicated synthetic directory; it never creates runs.
"$PREVIEW_PYTHON" - "$PREVIEW_ROOT" <<'PY'
import json,sys
from benchmarks.attention_harness.tests.agentpolicy_preview_fixture import generate
for page in generate(sys.argv[1]):
    print(page['label']+': http://127.0.0.1:8779'+page['workspace']+'#exp='+page['experiment_id'])
PY
# Use this worktree's server (8769's external serve_daily.py is not in this checkout).
# Reject every POST at the HTTP boundary, including start/create: no models or sims.
exec "$PREVIEW_PYTHON" -u - "$PREVIEW_ROOT" <<'PY'
import sys
from pathlib import Path
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.daily_dev_v2 import DailyDevV2
from benchmarks.attention_harness import ui_server, home_status
# The ordinary home page probes PARCC on GET. Replace it before server creation,
# so inherited credentials cannot turn a browser refresh into a model call.
class FixtureProbe:
    def snapshot(self,force=False):
        return {'result':{role:{'state':'unknown','detail':'Synthetic preview; provider checks disabled.'}
                          for role in ('glm','qwen')},'checked_at':None,'checking':False}
home_status.ProviderProbe=FixtureProbe
ui_server.read_quota=lambda: {}
root=Path(sys.argv[1])
bridge=DailyDevV2(root/'daily',[],None,per_run_budget=True,memory=None)
server=ui_server.create_server(store=AttentionStore(root/'attention.sqlite3'),artifact_root=root/'artifacts',
                     dev_v2_bridge=bridge,host='127.0.0.1',port=8779)
def read_only(self):
    self._json({'error':'Synthetic preview is read-only; no models or simulation are connected.'},status=403)
server.RequestHandlerClass.do_POST=read_only
print('Agent policy preview: http://127.0.0.1:8779/ui/ (read-only)',flush=True)
try:server.serve_forever()
except KeyboardInterrupt:pass
finally:server.server_close()
PY
