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

echo "TidyBot AttentionBench environment ready: $VENV_DIR"
echo "Run tests with: $VENV_DIR/bin/python -m pytest benchmarks/attention_harness/tests"
