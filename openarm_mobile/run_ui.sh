#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$task_root"
task_python="${ATTENTION_UI_PYTHON:-$(python3 -c 'from benchmarks.attention_harness.workspace_paths import get_str; print(get_str("ui.daily_python"))')}"
task_port="${ATTENTION_UI_PORT:-8796}"
task_store="${ATTENTION_UI_STORE:-outputs/openarm-mobile-ui/attention.sqlite3}"
task_artifacts="${ATTENTION_UI_ARTIFACTS:-outputs/openarm-mobile-ui/runs}"
mkdir -p -- "$(dirname -- "$task_store")" "$task_artifacts"
export PYTHONPATH="$task_root${PYTHONPATH:+:$PYTHONPATH}"
exec "$task_python" -B -u -m benchmarks.attention_harness.ui_server \
  --store "$task_store" --artifact-root "$task_artifacts" \
  --run-catalog openarm_mobile/launch_configs/catalog.json \
  --host 127.0.0.1 --port "$task_port"
