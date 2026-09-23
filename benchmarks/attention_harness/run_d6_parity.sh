#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ASPIRE_SIM_ROOT="${ASPIRE_SIM_ROOT:?set ASPIRE_SIM_ROOT to ASPIRE/aspire/sim}"
NATIVE_PYTHON="${TIDYBOT_ATTENTION_PYTHON:-${XDG_CACHE_HOME:-${HOME}/.cache}/tidybot-attention/venv/bin/python}"
LEGACY_PYTHON="${ASPIRE_ROBOSUITE_PYTHON:-$ASPIRE_SIM_ROOT/.venv-robosuite/bin/python3}"
EVIDENCE="$SCRIPT_DIR/protocol/v1/evidence"
EXPORTER="$SCRIPT_DIR/migration/export_evaluator_probes.py"
SEEDS="${D6_SEEDS:-101,102,103,104,105}"

[[ -x "$LEGACY_PYTHON" ]] || { echo "missing legacy Python: $LEGACY_PYTHON" >&2; exit 2; }
[[ -x "$NATIVE_PYTHON" ]] || { echo "missing native Python: $NATIVE_PYTHON" >&2; exit 2; }
mkdir -p "$EVIDENCE"

legacy_revision="$(git -C "$ASPIRE_SIM_ROOT/cap/third_party/robosuite" rev-parse HEAD)"
(
  cd "$ASPIRE_SIM_ROOT"
  MUJOCO_GL=egl TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1 "$LEGACY_PYTHON" "$EXPORTER" \
    --implementation legacy --seeds "$SEEDS" --source-revision "$legacy_revision" \
    --output "$EVIDENCE/legacy_probes.json"
)

cd "$REPO_ROOT"
MUJOCO_GL=egl "$NATIVE_PYTHON" -m \
  benchmarks.attention_harness.migration.export_evaluator_probes \
  --implementation native --seeds "$SEEDS" --source-revision robosuite-pypi-1.5.1 \
  --output "$EVIDENCE/native_probes.json"

"$NATIVE_PYTHON" -m benchmarks.attention_harness.parity \
  "$EVIDENCE/native_probes.json" "$EVIDENCE/legacy_probes.json" \
  --output "$EVIDENCE/parity_report.json"
