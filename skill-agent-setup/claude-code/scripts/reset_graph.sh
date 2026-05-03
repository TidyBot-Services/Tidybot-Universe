#!/bin/bash
# Reset a graph for a fresh dev iteration WITHOUT losing history.
#
# Usage:  ./scripts/reset_graph.sh <graph-name>
# Example: ./scripts/reset_graph.sh counter-to-sink
#
# What it does:
#   1. Archive (not delete) graphs/<name>/agent_sessions.jsonl
#      → agent_sessions.jsonl.archive_<timestamp>
#      orch only writes this file on session end, so deleting it loses
#      every prior dev/eval session record. Archive preserves history
#      for later forensics + dashboard inspection.
#   2. Delete graphs/<name>/skills/  (dev-written code; throwaway)
#   3. Reset graph.json status to "planned" + strip in-flight fields
#      (agent_id, session_id, trial_images, target_trial_images, updated_at)
#   4. Archive openclaw subprocess transcripts in
#      ~/.openclaw/agents/{tidybot-dev,tidybot-dev-default,tidybot-evaluator}/sessions/

set -euo pipefail

GRAPH="${1:?usage: $0 <graph-name>}"
WORKSPACE="$(cd "$(dirname "$0")/.." && pwd)"
GRAPH_DIR="$WORKSPACE/graphs/$GRAPH"
NOW=$(date +%Y%m%d_%H%M%S)

if [ ! -d "$GRAPH_DIR" ]; then
    echo "ERROR: $GRAPH_DIR does not exist" >&2
    exit 1
fi

# 1. Archive agent_sessions.jsonl instead of rm — preserves dev/eval history.
if [ -f "$GRAPH_DIR/agent_sessions.jsonl" ]; then
    mv "$GRAPH_DIR/agent_sessions.jsonl" \
       "$GRAPH_DIR/agent_sessions.jsonl.archive_${NOW}"
    echo "  archived agent_sessions.jsonl → .archive_${NOW}"
fi

# 2. Delete skills/ — dev's written code, throwaway between iterations.
if [ -d "$GRAPH_DIR/skills" ]; then
    rm -rf "$GRAPH_DIR/skills"
    echo "  deleted skills/"
fi

# 3. Reset graph.json
python3 - <<PYEOF
import json
p = "$GRAPH_DIR/graph.json"
g = json.load(open(p))
e = g["entries"][0]
e["status"] = "planned"
for k in list(e.keys()):
    if k not in ("name", "description", "dependencies", "status"):
        del e[k]
json.dump(g, open(p, "w"), indent=2)
print(f"  graph.json reset, entries[0].status=planned")
PYEOF

# 4. Archive openclaw subprocess transcripts (separate from orch's history)
for AGENT in tidybot-dev tidybot-dev-default tidybot-evaluator; do
    SDIR="$HOME/.openclaw/agents/$AGENT/sessions"
    if [ -d "$SDIR" ]; then
        ARCHIVE_DIR="$SDIR/_archive_${NOW}_reset"
        mkdir -p "$ARCHIVE_DIR"
        mv "$SDIR"/*.jsonl "$ARCHIVE_DIR/" 2>/dev/null || true
        mv "$SDIR"/sessions.json "$ARCHIVE_DIR/" 2>/dev/null || true
        mv "$SDIR"/*.checkpoint.* "$ARCHIVE_DIR/" 2>/dev/null || true
        # If archive is empty, clean up
        rmdir "$ARCHIVE_DIR" 2>/dev/null || true
    fi
done
echo "  archived openclaw sessions (tidybot-dev / tidybot-dev-default / tidybot-evaluator)"

echo "OK reset $GRAPH at $NOW"
