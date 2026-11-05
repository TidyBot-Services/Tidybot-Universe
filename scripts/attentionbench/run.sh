#!/usr/bin/env bash
set -euo pipefail
here=$(dirname "${BASH_SOURCE[0]}")
. "$here/python.sh"
attentionbench_python "$here/run.py" "$@"
