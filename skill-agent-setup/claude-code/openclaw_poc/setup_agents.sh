#!/usr/bin/env bash
# Create the two OpenClaw agents needed by agent_orchestrator.py when HARNESS=openclaw.
#
# Usage:
#   ./setup_agents.sh                            # use defaults (ollama for both)
#   DEV_MODEL=google/gemini-2.5-flash ./setup_agents.sh
#   EVAL_MODEL=google/gemini-2.5-flash ./setup_agents.sh
#
# Override via env:
#   DEV_AGENT_ID   (default: tidybot-dev)
#   EVAL_AGENT_ID  (default: tidybot-evaluator)
#   DEV_MODEL      (default: ollama/llama3.1:8b-ctx32k)
#   EVAL_MODEL     (default: ollama/llama3.1:8b-ctx32k)
#   WORKSPACE      (default: ./claude-code abs path, one dir up from this script)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="${WORKSPACE:-$(cd "$SCRIPT_DIR/.." && pwd)}"

DEV_AGENT_ID="${DEV_AGENT_ID:-tidybot-dev}"
EVAL_AGENT_ID="${EVAL_AGENT_ID:-tidybot-evaluator}"
DEV_MODEL="${DEV_MODEL:-ollama/llama3.1:8b-ctx32k}"
EVAL_MODEL="${EVAL_MODEL:-ollama/llama3.1:8b-ctx32k}"

echo "==> OpenClaw setup for orchestrator"
echo "   workspace:    $WORKSPACE"
echo "   dev agent:    $DEV_AGENT_ID  (model: $DEV_MODEL)"
echo "   eval agent:   $EVAL_AGENT_ID (model: $EVAL_MODEL)"

if ! command -v openclaw >/dev/null 2>&1; then
    echo "ERROR: openclaw CLI not found in PATH" >&2
    exit 1
fi

existing_json=$(openclaw agents list 2>/dev/null || true)

create_agent() {
    local id="$1"; local model="$2"
    if echo "$existing_json" | grep -q "^- $id\b"; then
        echo "   [skip] $id already exists"
        return
    fi
    echo "   [add]  $id"
    openclaw agents add "$id" \
        --workspace "$WORKSPACE" \
        --model "$model" \
        --non-interactive \
        --json >/dev/null
}

create_agent "$DEV_AGENT_ID" "$DEV_MODEL"
create_agent "$EVAL_AGENT_ID" "$EVAL_MODEL"

# Minimal AGENTS.md for each — orchestrator prepends full system prompt via
# _get_system_prompt() on first turn, so these are just lightweight defaults.
write_agents_md() {
    local id="$1"; local role="$2"
    local f="$HOME/.openclaw/agents/$id/AGENTS.md"
    if [[ -f "$f" ]]; then
        echo "   [skip] $f exists"
        return
    fi
    mkdir -p "$(dirname "$f")"
    cat > "$f" <<EOF
# $id

Role: **$role** for TidyBot Universe robot skill development.

You are invoked by agent_orchestrator.py (Python subprocess driver).
The first user message on a new session contains the full role-specific
system prompt (from \`_get_system_prompt()\` in agent_orchestrator.py) plus
the task brief.

For follow-up messages (session resume / inject_hint), the orchestrator
sends just the delta — the full context lives in this session's transcript.

Tools: read / write / edit / exec / process.
Workspace: $WORKSPACE.
EOF
    echo "   [write] $f"
}
write_agents_md "$DEV_AGENT_ID"  "dev agent"
write_agents_md "$EVAL_AGENT_ID" "evaluator agent"

echo "==> Done"
echo ""
echo "Verify:"
echo "   openclaw agents list"
echo ""
echo "Run orchestrator with OpenClaw backend:"
echo "   HARNESS=openclaw python3 agent_orchestrator.py --graph graphs/<name>"
