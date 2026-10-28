#!/usr/bin/env bash
# Sequential independent seeds. No service, simulator motion, or key sourcing.
set -euo pipefail
if (( $# < 3 || $# > 5 )); then
  echo "Usage: $0 SEEDS PROBE_DIR OUT_DIR [PLANNER_SOURCE] [LIMIT]" >&2
  echo 'SEEDS: e.g. 110 or 101-125 or 101,105,110; M11_PY selects native Python.' >&2
  exit 2
fi
m11_script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
m11_source=${4:-$(cd -- "$m11_script_dir/../.." && pwd)}
m11_python=${M11_PY:-python3}
m11_limit=${5:-20}
# Validate the entire seed list before creating any outputs.
m11_seeds=$(PYTHONPATH="$m11_source${PYTHONPATH:+:$PYTHONPATH}" "$m11_python" -B -c \
  'import sys; from scripts.m11_cabinet_probe import parse_seeds; print(*parse_seeds(sys.argv[1]))' "$1")
mkdir -p -- "$3"
for m11_seed in $m11_seeds; do
  if [[ -e "$3/seed$m11_seed" || -e "$3/seed$m11_seed.log" ]]; then
    echo "Refusing to overwrite seed$m11_seed output/log" >&2
    exit 2
  fi
  "$m11_python" -B "$m11_script_dir/reach_sweep.py" \
    --probe "$2/seed$m11_seed/probe.json" --out "$3/seed$m11_seed" \
    --planner-source "$m11_source" --limit "$m11_limit" \
    >"$3/seed$m11_seed.log" 2>&1
done
