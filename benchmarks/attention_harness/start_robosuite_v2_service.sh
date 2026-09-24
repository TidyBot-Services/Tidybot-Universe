#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${TIDYBOT_ATTENTION_VENV:-${XDG_CACHE_HOME:-${HOME}/.cache}/tidybot-attention/venv}"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "AttentionBench venv missing; run $SCRIPT_DIR/setup_env.sh first" >&2
  exit 1
fi
# The Universe checkout retains a frozen v1 robosuite_sim package. Start the
# separately installed service outside that checkout so Python imports v2.
cd "$VENV_DIR"
exec "$VENV_DIR/bin/python" -m robosuite_sim --enable-sim-gt "$@"
