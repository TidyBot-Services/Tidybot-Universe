#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${TIDYBOT_ATTENTION_VENV:-${XDG_CACHE_HOME:-${HOME}/.cache}/tidybot-attention/venv}"
PYTHON_VERSION="${TIDYBOT_ATTENTION_PYTHON:-3.10}"

command -v uv >/dev/null 2>&1 || {
  echo "uv is required: https://docs.astral.sh/uv/" >&2
  exit 1
}

uv venv "$VENV_DIR" --python "$PYTHON_VERSION"
uv pip install --python "$VENV_DIR/bin/python" -r "$SCRIPT_DIR/requirements-robosuite.txt"
uv pip install --python "$VENV_DIR/bin/python" \
  "git+https://github.com/TidyBot-Services/attention_memory_service.git@f1733006a2adc92953658ac95329fabf0aa4b5eb"
uv pip install --python "$VENV_DIR/bin/python" --no-deps \
  "git+https://github.com/TidyBot-Services/robosuite_sim.git@12bc69afe83c8398988be3eee637f91c14e9bf19"

echo "TidyBot AttentionBench environment ready: $VENV_DIR"
echo "Run tests with: $VENV_DIR/bin/python -m pytest benchmarks/attention_harness/tests"
