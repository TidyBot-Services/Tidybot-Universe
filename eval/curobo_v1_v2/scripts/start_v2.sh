#!/usr/bin/env bash
# Launch curobo_service v2 (cuRobo 0.8.0 / cuRoboV2) on port 7001.
#
# Reads the v2 service from /home/truares/文档/curobo_service_v0_8 and runs it
# under maniskill_v0_8 conda env (which has curobo==0.8.0 at
# /home/truares/workspace/curobo-v0.8). The service exposes the same HTTP
# routes as v1; only planner_core.py differs internally.
#
# Usage:
#   ./start_v2.sh            # foreground
#   ./start_v2.sh --bg       # background, logs -> ../results/v2_service.log

set -euo pipefail

PORT="${CUROBO_PORT_V2:-7051}"   # 7051 to avoid the NX listener squatting on 7001
SERVICE_DIR="/home/truares/文档/curobo_service_v0_8"
CONDA_ENV="maniskill_v0_8"
CONDA_PY="$HOME/miniconda3/envs/$CONDA_ENV/bin/python"

if [ ! -x "$CONDA_PY" ]; then
  echo "[v2] missing python: $CONDA_PY" >&2
  exit 1
fi

fuser -k "${PORT}/tcp" 2>/dev/null || true

cd "$SERVICE_DIR"
export CUROBO_HOST=127.0.0.1
export CUROBO_PORT=$PORT
export CUROBO_LOG_LEVEL=info
export CUROBO_DEVICE=${CUROBO_DEVICE:-cuda:0}
export CUROBO_DEFAULT_ENV=v2-default
export PYTHONUNBUFFERED=1

LOG="${LOG_FILE:-/home/truares/文档/Tidybot-Universe/eval/curobo_v1_v2/results/v2_service.log}"
mkdir -p "$(dirname "$LOG")"

if [ "${1:-}" = "--bg" ]; then
  echo "[v2] starting in background -> $LOG (port $PORT)"
  nohup "$CONDA_PY" -m curobo_service > "$LOG" 2>&1 &
  echo "[v2] pid=$!"
else
  echo "[v2] starting in foreground (port $PORT)"
  exec "$CONDA_PY" -m curobo_service
fi
