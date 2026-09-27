#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIVERSE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="${TIDYBOT_ATTENTION_VENV:-${XDG_CACHE_HOME:-${HOME}/.cache}/tidybot-attention/venv}"
PYTHON_VERSION="${TIDYBOT_ATTENTION_PYTHON:-3.10}"
MEMORY_SERVICE_SOURCE="${TIDYBOT_MEMORY_SERVICE_SOURCE:-$UNIVERSE_DIR/../attention_memory_service}"

if [[ ! -f "$MEMORY_SERVICE_SOURCE/pyproject.toml" ]]; then
  echo "Memory Service checkout not found: $MEMORY_SERVICE_SOURCE" >&2
  echo "Clone the matching attention_memory_service repo next to Universe or set TIDYBOT_MEMORY_SERVICE_SOURCE." >&2
  exit 1
fi

command -v uv >/dev/null 2>&1 || {
  echo "uv is required: https://docs.astral.sh/uv/" >&2
  exit 1
}

uv venv "$VENV_DIR" --python "$PYTHON_VERSION"
uv pip install --python "$VENV_DIR/bin/python" -r "$SCRIPT_DIR/requirements-robosuite.txt"
uv pip install --python "$VENV_DIR/bin/python" -e "$MEMORY_SERVICE_SOURCE"
"$VENV_DIR/bin/python" -c 'from attention_memory_service.memory_service import MemoryService; from attention_memory_service.memory_service_client import MemoryServiceClient; assert hasattr(MemoryService, "authorize_dev_use") and hasattr(MemoryServiceClient, "authorize_dev_use"), "Memory Service lacks Dev-use evidence API"'
uv pip install --python "$VENV_DIR/bin/python" --no-deps \
  "git+https://github.com/TidyBot-Services/robosuite_sim.git@12bc69afe83c8398988be3eee637f91c14e9bf19"

echo "TidyBot AttentionBench environment ready: $VENV_DIR"
echo "Run tests with: $VENV_DIR/bin/python -m pytest benchmarks/attention_harness/tests"
