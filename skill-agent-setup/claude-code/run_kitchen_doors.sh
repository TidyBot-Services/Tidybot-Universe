#!/usr/bin/env bash
# Batch runner: runs all 7 kitchen-doors skills sequentially.
# For each skill: restart sim with correct task_env, start orchestrator, run xbot-start,
# wait for skill to reach "review" or "done", then move to next.
#
# Usage: bash run_kitchen_doors.sh
#
# Prerequisites: agent_server running on :8080, dashboard on :8070

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GRAPHS_DIR="$SCRIPT_DIR/graphs/kitchen-doors"
SIM_DIR="$HOME/文档/maniskill-tidyverse"
ORCH_DIR="$SCRIPT_DIR"
ORCH_HTTP_PORT=8766

# Ordered list of skills (you can reorder or comment out to skip)
SKILLS=(
  open-single-door
  open-double-door
  open-door
  close-single-door
  close-double-door
  close-door
  manipulate-door
)

SIM_PID=""
ORCH_PID=""

cleanup() {
  echo "[batch] Cleaning up..."
  [ -n "$ORCH_PID" ] && kill "$ORCH_PID" 2>/dev/null && echo "[batch] Killed orchestrator $ORCH_PID"
  [ -n "$SIM_PID" ] && kill "$SIM_PID" 2>/dev/null && echo "[batch] Killed sim $SIM_PID"
  wait 2>/dev/null
}
trap cleanup EXIT

start_sim() {
  local task_env="$1"
  echo "[batch] Starting sim with task=$task_env ..."

  # Kill existing sim
  if [ -n "$SIM_PID" ]; then
    kill "$SIM_PID" 2>/dev/null || true
    wait "$SIM_PID" 2>/dev/null || true
    SIM_PID=""
    sleep 2
  fi

  # Also kill any lingering maniskill_server
  pkill -f "maniskill_server" 2>/dev/null || true
  sleep 2

  cd "$SIM_DIR"
  conda run -n maniskill \
    DISPLAY="${DISPLAY:-:1}" PYTHONUNBUFFERED=1 \
    python3 -m maniskill_server --task "$task_env" \
    > /tmp/sim_${task_env}.log 2>&1 &
  SIM_PID=$!
  echo "[batch] Sim PID=$SIM_PID, log=/tmp/sim_${task_env}.log"

  # Wait for sim to be ready (port 5555)
  echo "[batch] Waiting for sim port 5555..."
  for i in $(seq 1 60); do
    if python3 -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('localhost',5555)); s.close()" 2>/dev/null; then
      echo "[batch] Sim ready after ${i}s"
      return 0
    fi
    sleep 1
  done
  echo "[batch] ERROR: sim did not start within 60s"
  return 1
}

start_orchestrator() {
  local graph_dir="$1"
  echo "[batch] Starting orchestrator for $graph_dir ..."

  # Kill existing orchestrator
  if [ -n "$ORCH_PID" ]; then
    kill "$ORCH_PID" 2>/dev/null || true
    wait "$ORCH_PID" 2>/dev/null || true
    ORCH_PID=""
    sleep 1
  fi

  cd "$ORCH_DIR"
  env -u ANTHROPIC_API_KEY \
    PYTHONPATH="$HOME/.local/lib/python3.10/site-packages:${PYTHONPATH:-}" \
    PYTHONUNBUFFERED=1 \
    python3 agent_orchestrator.py --graph "$graph_dir" \
    > /tmp/orch_$(basename "$graph_dir").log 2>&1 &
  ORCH_PID=$!
  echo "[batch] Orchestrator PID=$ORCH_PID, log=/tmp/orch_$(basename "$graph_dir").log"

  # Wait for HTTP port
  echo "[batch] Waiting for orchestrator port $ORCH_HTTP_PORT..."
  for i in $(seq 1 30); do
    if curl -s "http://localhost:$ORCH_HTTP_PORT/entries" >/dev/null 2>&1; then
      echo "[batch] Orchestrator ready after ${i}s"
      return 0
    fi
    sleep 1
  done
  echo "[batch] ERROR: orchestrator did not start within 30s"
  return 1
}

wait_for_skill() {
  local skill="$1"
  local timeout="${2:-1800}"  # default 30 min
  echo "[batch] Waiting for '$skill' to reach review/done (timeout=${timeout}s)..."

  local start_time=$(date +%s)
  while true; do
    local elapsed=$(( $(date +%s) - start_time ))
    if [ "$elapsed" -ge "$timeout" ]; then
      echo "[batch] TIMEOUT: '$skill' did not complete within ${timeout}s"
      return 1
    fi

    local status
    status=$(curl -s "http://localhost:$ORCH_HTTP_PORT/entries" 2>/dev/null \
      | python3 -c "import sys,json; entries=json.load(sys.stdin); print(next((e['status'] for e in entries if e['name']=='$skill'), 'unknown'))" 2>/dev/null) || status="error"

    case "$status" in
      review|done)
        echo "[batch] '$skill' reached status='$status' after ${elapsed}s"
        return 0
        ;;
      error|failed)
        echo "[batch] '$skill' FAILED (status='$status') after ${elapsed}s"
        return 1
        ;;
      *)
        # Print progress every 30s
        if [ $((elapsed % 30)) -eq 0 ] && [ "$elapsed" -gt 0 ]; then
          echo "[batch] '$skill' status='$status' (${elapsed}s elapsed)"
        fi
        sleep 5
        ;;
    esac
  done
}

# --- Main ---
echo "=========================================="
echo "[batch] Kitchen Doors Batch Runner"
echo "[batch] Skills: ${SKILLS[*]}"
echo "=========================================="

for skill in "${SKILLS[@]}"; do
  graph_dir="$GRAPHS_DIR/$skill"
  if [ ! -d "$graph_dir" ]; then
    echo "[batch] SKIP: $graph_dir not found"
    continue
  fi

  # Read task_env from graph.json
  task_env=$(python3 -c "import json; d=json.load(open('$graph_dir/graph.json')); print(d.get('task_env',''))")
  if [ -z "$task_env" ]; then
    echo "[batch] SKIP: no task_env in $graph_dir/graph.json"
    continue
  fi

  echo ""
  echo "=========================================="
  echo "[batch] SKILL: $skill  TASK: $task_env"
  echo "=========================================="

  # Ensure graph.json status is "planned"
  python3 -c "
import json
f='$graph_dir/graph.json'
d=json.load(open(f))
for e in (d.get('entries') or d):
    if isinstance(e, dict) and e.get('name')=='$skill':
        e['status']='planned'
        e['agent_id']=None
        e['agent_log']=[]
json.dump(d, open(f,'w'), indent=2)
print('[batch] Reset $skill to planned')
"

  # 1. Start sim with correct task
  start_sim "$task_env" || { echo "[batch] FAILED to start sim for $skill"; continue; }

  # 2. Start orchestrator
  start_orchestrator "$graph_dir" || { echo "[batch] FAILED to start orchestrator for $skill"; continue; }

  # 3. Trigger xbot-start
  sleep 2
  echo "[batch] Triggering xbot-start..."
  result=$(curl -s -X POST "http://localhost:$ORCH_HTTP_PORT/xbot-start")
  echo "[batch] xbot-start response: $result"

  # 4. Wait for completion
  wait_for_skill "$skill" 1800 || echo "[batch] WARNING: $skill did not complete successfully"

  echo "[batch] === $skill DONE ==="
done

echo ""
echo "=========================================="
echo "[batch] All skills processed!"
echo "=========================================="
