#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$task_root"
task_python="${ATTENTION_UI_PYTHON:-/home/truares/桌面/attentionbench-week2-clean-venv/bin/python}"
task_port="${ATTENTION_UI_PORT:-8795}"
task_store="${ATTENTION_UI_STORE:-outputs/openarm-ui/attention.sqlite3}"
task_artifacts="${ATTENTION_UI_ARTIFACTS:-outputs/openarm-ui/runs}"
mkdir -p -- "$(dirname -- "$task_store")" "$task_artifacts"
export PYTHONPATH="$task_root${PYTHONPATH:+:$PYTHONPATH}"
task_catalog="$("$task_python" -c 'import os;from openarm_bimanual.entry import resolve_daily_catalog;print(resolve_daily_catalog(override=os.environ.get("ATTENTION_UI_CATALOG")))')"
if [[ ! -f "$task_store" ]]; then
  "$task_python" -c 'import sys;from pathlib import Path;from benchmarks.attention_harness.core.store import AttentionStore;AttentionStore(Path(sys.argv[1])).close()' "$task_store"
fi
exec "$task_python" -B -u -m benchmarks.attention_harness.ui_server \
  --store "$task_store" --artifact-root "$task_artifacts" \
  --run-catalog "$task_catalog" \
  --host 127.0.0.1 --port "$task_port"
