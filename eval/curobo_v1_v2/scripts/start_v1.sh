#!/usr/bin/env bash
# Launch curobo_service v1 (cuRobo 0.7.8) on port 7000.
#
# Reads the v1 service from /home/truares/文档/curobo_service and runs it under
# the maniskill_v0_7_8 conda env (which has curobo==0.7.8 at
# /home/truares/workspace/curobo-v0.7.8).
#
# Usage:
#   ./start_v1.sh            # foreground
#   ./start_v1.sh --bg       # background, logs -> ../results/v1_service.log

set -euo pipefail

PORT="${CUROBO_PORT_V1:-7050}"   # 7050 instead of 7000 to avoid clobbering the user's prod curobo_service
SERVICE_DIR="/home/truares/文档/curobo_service"
CONDA_ENV="maniskill_v0_7_8"
CONDA_PY="$HOME/miniconda3/envs/$CONDA_ENV/bin/python"

if [ ! -x "$CONDA_PY" ]; then
  echo "[v1] missing python: $CONDA_PY" >&2
  exit 1
fi

# Kill any stale process on this port — silent failures here look like
# a successful start otherwise (per CLAUDE.md guidance).
fuser -k "${PORT}/tcp" 2>/dev/null || true

cd "$SERVICE_DIR"
export CUROBO_HOST=127.0.0.1
export CUROBO_PORT=$PORT
export CUROBO_LOG_LEVEL=info
export CUROBO_DEVICE=${CUROBO_DEVICE:-cuda:0}
export CUROBO_DEFAULT_ENV=v1-default
export PYTHONUNBUFFERED=1

LOG="${LOG_FILE:-/home/truares/文档/Tidybot-Universe/eval/curobo_v1_v2/results/v1_service.log}"
mkdir -p "$(dirname "$LOG")"

if [ "${1:-}" = "--bg" ]; then
  echo "[v1] starting in background -> $LOG (port $PORT)"
  nohup "$CONDA_PY" -m curobo_service > "$LOG" 2>&1 &
  echo "[v1] pid=$!"
else
  echo "[v1] starting in foreground (port $PORT)"
  exec "$CONDA_PY" -m curobo_service
fi
