#!/bin/bash
# Single-orch batch driver: launch ONE orchestrator with the full N-task
# unified graph and rotate its active targets between batches. The
# dashboard sees all N hexes throughout the run; only the 2 currently
# bound to live sims actually iterate at any moment.
#
# Per-task limits (env-overridable):
#   MAX_ITERS_PER_SKILL=35       — dev + eval invocations combined
#   PER_TASK_WALLTIME_S=3600     — 60 min hard cap per task
#   BATCH_HARD_TIMEOUT_S=7200    — 2 hr hard cap per batch (safety net)
#   GRAPH_DIR=graphs/unified-single-stage  — full-task graph the orch loads
#
# Usage:
#   ./run_all_tasks.sh tasks.txt [results.csv]

set -uo pipefail

TASKS_FILE="${1:?usage: $0 tasks.txt [results.csv]}"
RESULTS="${2:-results.csv}"

ROOT="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE="$(cd "$ROOT/../.." && pwd)"
CONDA_ENV="${CONDA_ENV:-maniskill}"
HARNESS="${HARNESS:-openclaw}"
GRAPH_DIR="${GRAPH_DIR:-graphs/unified-single-stage}"
MAX_ITERS_PER_SKILL="${MAX_ITERS_PER_SKILL:-35}"
PER_TASK_WALLTIME_S="${PER_TASK_WALLTIME_S:-3600}"
BATCH_HARD_TIMEOUT_S="${BATCH_HARD_TIMEOUT_S:-7200}"
POLL_INTERVAL_S="${POLL_INTERVAL_S:-30}"
# Don't gate spawn on dep status — graph deps are still kept (dashboard draws
# the layered tree), but every entry is independently dispatched to whichever
# sim has the matching task_env. Override to 0 to restore strict deps.
IGNORE_DEPS_FOR_SPAWN="${IGNORE_DEPS_FOR_SPAWN:-1}"

# Number of parallel sim/agent slots per batch. Default 2 (backward compat).
# Each additional slot uses port-offset = SLOT * 100. Slot 0 → 5500/8080,
# slot 1 → 5600/8180, slot 2 → 5700/8280, slot 3 → 5800/8380. With 3+ slots
# you typically need a beefier GPU (per memory: 2 sims = ~13/16 GB). For
# multi-host runs, see post_targets — the URLs in the /targets payload can
# point at remote agent_servers as long as orch can reach them over network.
TARGETS_PER_BATCH="${TARGETS_PER_BATCH:-2}"

# AGENT_HOST_PREFIX builds per-slot URLs. Override to "http://otherhost"
# (no trailing port) to push agent_servers to a different machine. Sims still
# launch locally — for fully remote sims, override launch_sim/launch_agent
# below or run those externally and just POST /targets.
AGENT_HOST_PREFIX="${AGENT_HOST_PREFIX:-http://localhost}"
SIM_HOST_PREFIX="${SIM_HOST_PREFIX:-http://localhost}"

if [ ! -f "$TASKS_FILE" ]; then
  echo "tasks file not found: $TASKS_FILE" >&2
  exit 1
fi

if [ ! -f "$RESULTS" ]; then
  echo "task_env,skill,status,fail_reason,iter_count,wall_time_s,last_feedback" > "$RESULTS"
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Build space-separated list of "PORT/tcp" tokens for fuser kill, covering
# all $TARGETS_PER_BATCH slots. Per-slot port set:
#   sim:  5500/5555/5556/5557/5570/5571/5580/50000  (each + SLOT*100)
#   agent_server: 8080 (+ SLOT*100)
build_fuser_port_list() {
  local N="$1"
  local out=""
  for ((s = 0; s < N; s++)); do
    local off=$((s * 100))
    for p in 5500 5555 5556 5557 5570 5571 5580 50000; do
      out+="$((p + off))/tcp "
    done
    out+="$((8080 + off))/tcp "
  done
  echo "$out"
}

# List of openclaw agent IDs to archive between batches. Includes per-slot
# names for slots 0..N-1. The base names (tidybot-dev, tidybot-evaluator)
# are also archived for safety.
build_agent_list_to_archive() {
  local N="$1"
  local out="tidybot-dev tidybot-evaluator"
  for ((s = 0; s < N; s++)); do
    out+=" tidybot-dev-sim-$s tidybot-evaluator-sim-$s"
  done
  echo "$out"
}

launch_sim() {
  local SLOT="$1" TASK_ENV="$2"
  local OFFSET=$((SLOT * 100))
  cd "$WORKSPACE/sims/maniskill" && \
    conda run -n "$CONDA_ENV" --no-capture-output \
    env LD_PRELOAD="$HOME/miniconda3/envs/$CONDA_ENV/lib/libstdc++.so.6" \
    PYTHONUNBUFFERED=1 \
    python3 -m maniskill_server --task "$TASK_ENV" --port-offset "$OFFSET" \
    > "/tmp/run_sim${SLOT}.log" 2>&1 &
  cd - >/dev/null
}

launch_agent() {
  local SLOT="$1"
  local OFFSET=$((SLOT * 100))
  # Per-slot LOG_DIR so sim-0's and sim-1's executions don't share the same
  # logs/code_executions/ directory. agent_server appends `/code_executions`
  # to LOG_DIR, so LOG_DIR is the parent — final path becomes
  # $WORKSPACE/logs_env<SLOT>/code_executions/<exec-id>/
  local SLOT_LOG_DIR="$WORKSPACE/logs_env${SLOT}"
  mkdir -p "$SLOT_LOG_DIR/code_executions"
  cd "$WORKSPACE/agent_server" && \
    conda run -n "$CONDA_ENV" --no-capture-output \
    env LD_PRELOAD="$HOME/miniconda3/envs/$CONDA_ENV/lib/libstdc++.so.6" \
    LOG_DIR="$SLOT_LOG_DIR" \
    PYTHONUNBUFFERED=1 \
    python3 server.py --port-offset "$OFFSET" --no-service-manager \
    > "/tmp/run_agent${SLOT}.log" 2>&1 &
  cd - >/dev/null
}

launch_orch_once() {
  cd "$ROOT" && \
    "$HOME/bin/with-litellm.sh" conda run -n "$CONDA_ENV" --no-capture-output \
    env -u ANTHROPIC_API_KEY \
    LD_PRELOAD="$HOME/miniconda3/envs/$CONDA_ENV/lib/libstdc++.so.6" \
    MAX_ITERS_PER_SKILL="$MAX_ITERS_PER_SKILL" \
    IGNORE_DEPS_FOR_SPAWN="$IGNORE_DEPS_FOR_SPAWN" \
    PYTHONUNBUFFERED=1 \
    python3 agent_orchestrator.py \
    --graph "$GRAPH_DIR" --harness "$HARNESS" --autonomous \
    > "/tmp/run_orch.log" 2>&1 &
}

wait_for_port() {
  local PORT="$1" LIMIT="${2:-90}"
  local i=0
  while ! nc -z localhost "$PORT" >/dev/null 2>&1; do
    sleep 2; i=$((i + 2))
    if [ "$i" -ge "$LIMIT" ]; then return 1; fi
  done
}

teardown_sims_and_agents() {
  # Per-slot port list (extended for $TARGETS_PER_BATCH slots).
  fuser -k $(build_fuser_port_list "$TARGETS_PER_BATCH") 2>/dev/null || true
  # Also kill openclaw-agent processes from the previous batch. Their
  # session-file locks would otherwise survive into the next batch and
  # block the new dev/eval spawn for the same per-target agent (10s
  # timeout, then FailoverError "session file locked"). Plus drop any
  # stale .lock files left behind so the new openclaw can start cleanly.
  pkill -KILL -x openclaw-agent 2>/dev/null || true
  pkill -KILL -x openclaw 2>/dev/null || true
  rm -f /home/truares/.openclaw/agents/tidybot-{dev,evaluator}*/sessions/*.lock 2>/dev/null || true

  # Archive each batch's session JSONLs so the NEXT batch's dev/eval starts
  # with a fresh session — no context bleed from prior skill's history.
  # 2026-05-01 v15 incident: microwave-press-button dev resumed sim-1's session
  # which had close-drawer + close-single-door history; when sim-1 was briefly
  # unreachable the dev "decided" to launch its own maniskill_server with the
  # WRONG task name (Open-Single-Door, leaked from batch 2). Per-batch archive
  # eliminates that bleed entirely — each batch sees only its own task.
  local ARCHIVE_TS
  ARCHIVE_TS=$(date +%Y%m%d_%H%M%S)
  for AGENT in $(build_agent_list_to_archive "$TARGETS_PER_BATCH"); do
    local D=/home/truares/.openclaw/agents/$AGENT/sessions
    if [ -d "$D" ]; then
      mkdir -p "$D/_archive_${ARCHIVE_TS}"
      mv "$D"/*.jsonl "$D/_archive_${ARCHIVE_TS}/" 2>/dev/null || true
    fi
  done

  # Kill orphan maniskill_server / agent_server processes left behind by dev
  # agents that violated the IRON RULE and launched their own subprocesses.
  # PPID=1 (init) or PPID=1024 (systemd-user) means orphan — driver can't
  # reach them via process tree. We match by binary + cmdline.
  for PID in $(pgrep -f "python.*-m maniskill_server" 2>/dev/null); do
    PPID_OF=$(ps -o ppid= -p "$PID" 2>/dev/null | tr -d ' ')
    if [ "$PPID_OF" = "1" ] || [ "$PPID_OF" = "1024" ]; then
      kill -9 "$PID" 2>/dev/null || true
      echo "[teardown] killed orphan maniskill_server pid=$PID (ppid=$PPID_OF)"
    fi
  done

  sleep 4
}

post_targets() {
  # Each arg is a task_env (or empty string for a missing slot).
  # Slot N maps to: sim_api=$SIM_HOST_PREFIX:$((5500+N*100)),
  #                 agent_server=$AGENT_HOST_PREFIX:$((8080+N*100)).
  # Slot 0 is marked primary=True (drives global AGENT_SERVER fallback).
  local PAYLOAD
  PAYLOAD=$(python3 -c "
import json, sys, os
agent_host = os.environ.get('AGENT_HOST_PREFIX', 'http://localhost')
sim_host   = os.environ.get('SIM_HOST_PREFIX',   'http://localhost')
tasks = sys.argv[1:]
targets = []
for slot, task in enumerate(tasks):
    if not task:
        continue
    targets.append({
        'name':         f'sim-{slot}',
        'primary':      slot == 0,
        'agent_server': f'{agent_host}:{8080 + slot * 100}',
        'sim_api':      f'{sim_host}:{5500 + slot * 100}',
        'task_env':     task,
    })
print(json.dumps({'targets': targets}))
" "$@")
  curl -sX POST http://localhost:8766/targets \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" >/dev/null
}

emit_csv_row() {
  local JSON="$1"
  echo "$JSON" | python3 -c "
import json,sys,csv
e = json.load(sys.stdin)
fb = (e.get('last_feedback') or '').replace('\n', ' ').replace('\r', ' ')[:500]
row = [
    e.get('task_env',''),
    e.get('name',''),
    e.get('status',''),
    e.get('fail_reason',''),
    e.get('iter_count', 0),
    e.get('wall_time_s', 0),
    fb,
]
csv.writer(sys.stdout).writerow(row)
" >> "$RESULTS"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
mapfile -t TASKS < <(grep -v '^[[:space:]]*#\|^[[:space:]]*$' "$TASKS_FILE")
TOTAL_TASKS=${#TASKS[@]}
echo "===================================================================="
echo "run_all_tasks.sh (single-orch + target rotation)"
echo "  graph dir:    $GRAPH_DIR"
echo "  tasks file:   $TASKS_FILE  ($TOTAL_TASKS tasks)"
echo "  results:      $RESULTS"
echo "  iter cap:     $MAX_ITERS_PER_SKILL"
echo "  walltime cap: ${PER_TASK_WALLTIME_S}s per task"
echo "  ignore deps:  $IGNORE_DEPS_FOR_SPAWN  (1 = scheduler doesn't gate on deps; dashboard still draws them)"
echo "  parallelism:  $TARGETS_PER_BATCH slots per batch (sim ports $((5500))..$((5500 + (TARGETS_PER_BATCH-1)*100)), agent ports $((8080))..$((8080 + (TARGETS_PER_BATCH-1)*100)))"
echo "===================================================================="

# 1) One-shot full teardown of any prior process.
#    Includes scan for ORPHANED maniskill_server / agent_server processes
#    re-parented to systemd-user (PPID=1024). These can squat on bridge
#    ports and cause "Address already in use" on next launch.
echo "[init] tearing down stale processes…"
ZOMBIE_PIDS=$(pgrep -f 'python3 -m (tidybot_uni\.sims\.maniskill\.maniskill_server|maniskill_server)' 2>/dev/null \
              | xargs -I{} sh -c 'ppid=$(ps -o ppid= -p {} 2>/dev/null | tr -d " "); [ "$ppid" = "1" ] || [ "$ppid" = "1024" ] && echo {}' \
              2>/dev/null)
if [ -n "$ZOMBIE_PIDS" ]; then
  echo "[init] killing orphaned maniskill_server PIDs: $ZOMBIE_PIDS"
  kill -9 $ZOMBIE_PIDS 2>/dev/null || true
fi
fuser -k $(build_fuser_port_list "$TARGETS_PER_BATCH") 8765/tcp 8766/tcp 2>/dev/null || true
pkill -KILL -x openclaw-agent 2>/dev/null || true
pkill -KILL -x openclaw 2>/dev/null || true
rm -f /home/truares/.openclaw/agents/tidybot-{dev,evaluator}*/sessions/*.lock 2>/dev/null || true

# Reset graph.json statuses for tasks listed in $TASKS_FILE so a previously
# completed task gets re-evaluated under the new code. Without this, a task
# that hit `done` in a prior run would be skipped (orch's auto-spawn won't
# touch entries already in "done" / "confirmed_done"), leaving the matching
# sim idle for the whole batch. Set RESET_GRAPH_STATUS_ON_START=0 to keep
# previous statuses (resume mode).
RESET_GRAPH_STATUS_ON_START="${RESET_GRAPH_STATUS_ON_START:-1}"
if [ "$RESET_GRAPH_STATUS_ON_START" = "1" ]; then
  python3 - "$GRAPH_DIR/graph.json" "$TASKS_FILE" <<'RESETPY' || true
import json, sys
gp, tf = sys.argv[1], sys.argv[2]
tasks = set()
for line in open(tf):
    s = line.strip()
    if s and not s.startswith('#'):
        tasks.add(s)
g = json.load(open(gp))
n = 0
for e in g.get('entries', []):
    if e.get('task_env') in tasks:
        e['status'] = 'planned'
        e['trial_images'] = []
        e['target_trial_images'] = {}
        e.pop('fail_reason', None)
        e['agent_id'] = None
        e['session_id'] = ''
        n += 1
json.dump(g, open(gp, 'w'), indent=2)
print(f"[init] reset {n} graph entries (matching tasks file) to 'planned'")
RESETPY
fi

# Archive openclaw dev/eval sessions before each run. Without this, openclaw
# resumes from the prior run's session_id and the dev inherits stale context
# (failed sim restarts, "could not parse" cycles, etc.) — confusing the new
# attempt and wasting iters fighting yesterday's bugs.
ARCHIVE_TS=$(date +%Y%m%d_%H%M%S)
echo "[init] archiving openclaw sessions → _archive_${ARCHIVE_TS}/"
# Always archive base agents + per-slot agents covering up to 4 slots
# (max sensibly supported on a single host; multi-host can extend).
for AGENT in tidybot-dev tidybot-evaluator \
             tidybot-dev-sim-0 tidybot-dev-sim-1 tidybot-dev-sim-2 tidybot-dev-sim-3 \
             tidybot-evaluator-sim-0 tidybot-evaluator-sim-1 tidybot-evaluator-sim-2 tidybot-evaluator-sim-3; do
  D=/home/truares/.openclaw/agents/$AGENT/sessions
  if [ -d "$D" ]; then
    AD=$D/_archive_$ARCHIVE_TS
    mkdir -p "$AD" 2>/dev/null
    mv "$D"/*.jsonl "$AD/" 2>/dev/null || true
    mv "$D"/sessions.json "$AD/" 2>/dev/null || true
  fi
done
sleep 4

# 2) Launch the SINGLE orch (full graph). It comes up with whatever
#    targets the graph file has (we'll overwrite them per-batch).
echo "[init] launching orchestrator…"
launch_orch_once
if ! wait_for_port 8766 60; then
  echo "[init] orch failed to come up on :8766; aborting" >&2
  exit 1
fi
echo "[init] orch up on :8766"

# Rotate through batches of $TARGETS_PER_BATCH tasks
BATCH_IDX=0
TOTAL_BATCHES=$(( (TOTAL_TASKS + TARGETS_PER_BATCH - 1) / TARGETS_PER_BATCH ))
for ((i = 0; i < TOTAL_TASKS; i += TARGETS_PER_BATCH)); do
  BATCH_IDX=$((BATCH_IDX + 1))

  # Pull this batch's tasks into BATCH_TASKS array (length up to TARGETS_PER_BATCH).
  # Empty string represents a missing slot when TOTAL_TASKS is not a multiple
  # of TARGETS_PER_BATCH (last batch may be partial).
  declare -a BATCH_TASKS=()
  for ((s = 0; s < TARGETS_PER_BATCH; s++)); do
    BATCH_TASKS[$s]="${TASKS[$((i + s))]:-}"
  done

  # Pretty-print batch header: e.g. "task_a | task_b | task_c"
  HDR=""
  for ((s = 0; s < TARGETS_PER_BATCH; s++)); do
    if [ -n "${BATCH_TASKS[$s]}" ]; then
      HDR+="${BATCH_TASKS[$s]}"
    else
      HDR+="(empty)"
    fi
    if [ "$s" -lt "$((TARGETS_PER_BATCH - 1))" ]; then HDR+=" | "; fi
  done

  echo
  echo "── batch $BATCH_IDX/$TOTAL_BATCHES: $HDR ──"

  # 3) Stop any prior batch's sims/agents so we can reuse fixed slot ports
  teardown_sims_and_agents

  # 4) Launch this batch's sims in parallel, wait for ports
  echo "  [$BATCH_IDX] launching sims…"
  for ((s = 0; s < TARGETS_PER_BATCH; s++)); do
    [ -n "${BATCH_TASKS[$s]}" ] && launch_sim "$s" "${BATCH_TASKS[$s]}"
  done

  # Wait for each non-empty slot's sim ports. If slot 0 fails, abort batch.
  # If slot N>0 fails, mark that task sim_dead and continue with reduced fleet.
  SIM_OK_COUNT=0
  for ((s = 0; s < TARGETS_PER_BATCH; s++)); do
    [ -z "${BATCH_TASKS[$s]}" ] && continue
    SIM_PORT=$((5500 + s * 100))
    SIM_ZMQ=$((5555 + s * 100))
    if wait_for_port "$SIM_PORT" 90 && wait_for_port "$SIM_ZMQ" 90; then
      SIM_OK_COUNT=$((SIM_OK_COUNT + 1))
    else
      if [ "$s" = "0" ]; then
        echo "  [$BATCH_IDX] sim-0 failed to start; marking all batch tasks sim_dead and skipping"
        for ((s2 = 0; s2 < TARGETS_PER_BATCH; s2++)); do
          [ -n "${BATCH_TASKS[$s2]}" ] && echo "${BATCH_TASKS[$s2]},,failed,sim_dead,0,0," >> "$RESULTS"
        done
        continue 2  # skip whole batch
      else
        echo "  [$BATCH_IDX] sim-$s failed to start; ${BATCH_TASKS[$s]} → sim_dead"
        echo "${BATCH_TASKS[$s]},,failed,sim_dead,0,0," >> "$RESULTS"
        BATCH_TASKS[$s]=""  # blank out so post_targets / polling skip it
      fi
    fi
  done

  echo "  [$BATCH_IDX] launching agent_servers…"
  for ((s = 0; s < TARGETS_PER_BATCH; s++)); do
    [ -n "${BATCH_TASKS[$s]}" ] && launch_agent "$s"
  done
  for ((s = 0; s < TARGETS_PER_BATCH; s++)); do
    [ -n "${BATCH_TASKS[$s]}" ] && wait_for_port "$((8080 + s * 100))" 60 || true
  done

  # 5) Tell the live orch about the new active targets
  echo "  [$BATCH_IDX] rotating /targets…"
  post_targets "${BATCH_TASKS[@]}"

  # First batch needs xbot-start; subsequent batches: /targets's auto-spawn
  # already kicks the cycle. Send xbot-start once for safety.
  curl -sX POST http://localhost:8766/xbot-start >/dev/null 2>&1 || true

  echo "  [$BATCH_IDX] polling /summary every ${POLL_INTERVAL_S}s"

  BATCH_START=$(date +%s)
  declare -A TASK_DONE=()
  # Per-slot agent_server health-check counter (Fix #12). 2 consecutive
  # unreachable polls → relaunch agent_server for that slot. Sized to
  # $TARGETS_PER_BATCH; padded with zeros so all indices are valid.
  declare -a UNREACH=()
  for ((s = 0; s < TARGETS_PER_BATCH; s++)); do UNREACH[$s]=0; done
  while true; do
    sleep "$POLL_INTERVAL_S"
    SUMMARY=$(curl -s http://localhost:8766/summary 2>/dev/null || echo '{"entries":[]}')
    ALL_DONE=1

    # Health check: probe each active slot's agent_server. Relaunch on 2-consec miss.
    for ((s = 0; s < TARGETS_PER_BATCH; s++)); do
      [ -z "${BATCH_TASKS[$s]}" ] && continue
      PORT=$((8080 + s * 100))
      if ! curl -sf "http://localhost:${PORT}/state" >/dev/null 2>&1; then
        UNREACH[$s]=$((${UNREACH[$s]} + 1))
        if [ "${UNREACH[$s]}" -ge 2 ]; then
          echo "  [$BATCH_IDX] agent_server-$s (port $PORT) unreachable for ${UNREACH[$s]} polls — relaunching"
          launch_agent "$s"
          wait_for_port "$PORT" 60 || echo "  [$BATCH_IDX] agent_server-$s did not come back up in 60s"
          UNREACH[$s]=0
        fi
      else
        UNREACH[$s]=0
      fi
    done

    # Status check: each active task's status, mark done / walltime.
    for ((s = 0; s < TARGETS_PER_BATCH; s++)); do
      T="${BATCH_TASKS[$s]}"
      [ -z "$T" ] && continue
      [ "${TASK_DONE[$T]:-0}" -eq 1 ] && continue
      ENTRY=$(echo "$SUMMARY" | python3 -c "
import json,sys
data = json.load(sys.stdin)
for e in data.get('entries', []):
    if e.get('task_env') == '$T':
        print(json.dumps(e)); break
")
      [ -z "$ENTRY" ] && { ALL_DONE=0; continue; }
      ST=$(echo "$ENTRY" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status',''))")
      WT=$(echo "$ENTRY" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('wall_time_s',0)))")
      ITERS=$(echo "$ENTRY" | python3 -c "import json,sys; print(json.load(sys.stdin).get('iter_count',0))")

      if [[ "$ST" =~ ^(done|failed|review)$ ]]; then
        echo "  [$BATCH_IDX] $T → terminal status=$ST iters=$ITERS wt=${WT}s"
        TASK_DONE[$T]=1
      elif [ "$WT" -gt "$PER_TASK_WALLTIME_S" ]; then
        echo "  [$BATCH_IDX] $T → walltime cap (${WT}s); marking failed_walltime"
        SKILL=$(echo "$ENTRY" | python3 -c "import json,sys; print(json.load(sys.stdin).get('name',''))")
        curl -sX PATCH "http://localhost:8766/entries/$SKILL" \
          -H "Content-Type: application/json" \
          -d '{"status":"failed","fail_reason":"walltime"}' >/dev/null 2>&1 || true
        TASK_DONE[$T]=1
      else
        ALL_DONE=0
      fi
    done

    [ "$ALL_DONE" -eq 1 ] && break

    ELAPSED=$(($(date +%s) - BATCH_START))
    if [ "$ELAPSED" -gt "$BATCH_HARD_TIMEOUT_S" ]; then
      echo "  [$BATCH_IDX] batch hard timeout (${ELAPSED}s); aborting batch"
      break
    fi
  done

  echo "  [$BATCH_IDX] writing results to $RESULTS"
  FINAL_SUMMARY=$(curl -s http://localhost:8766/summary 2>/dev/null || echo '{"entries":[]}')
  for ((s = 0; s < TARGETS_PER_BATCH; s++)); do
    T="${BATCH_TASKS[$s]}"
    [ -z "$T" ] && continue
    ENTRY=$(echo "$FINAL_SUMMARY" | python3 -c "
import json,sys
data = json.load(sys.stdin)
for e in data.get('entries', []):
    if e.get('task_env') == '$T':
        print(json.dumps(e)); break
")
    if [ -n "$ENTRY" ]; then
      emit_csv_row "$ENTRY"
    else
      echo "$T,,failed,no_entry_in_summary,0,0," >> "$RESULTS"
    fi
  done
done

# Final teardown of sims/agents (keep orch alive so dashboard still shows
# the final 32-hex state).
echo
echo "[done] all batches finished. Tearing down sims/agents (orch stays up so the"
echo "       dashboard at http://localhost:8070/local/ keeps showing the result)."
teardown_sims_and_agents

python3 "$ROOT/summarize.py" "$RESULTS"
