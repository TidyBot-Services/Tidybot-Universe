#!/usr/bin/env bash
# Tidybot Universe — client setup (no servers, no sim, no hardware)
#
# Sets up a lightweight environment for submitting code to a remote agent
# server + running the orchestrator and dashboard locally. No sim, no
# agent_server, no hardware services are installed.
#
# Usage:
#   bash setup-client.sh ENV_NAME [WORKSPACE_DIR]
#
# Examples:
#   bash setup-client.sh xbot-client ~/xbot
#   bash setup-client.sh xbot-client            # workspace = ./tidybot_uni
#
# Or download and run directly:
#   curl -fsSL https://raw.githubusercontent.com/TidyBot-Services/Tidybot-Universe/master/setup-client.sh | bash -s -- ENV_NAME [WORKSPACE_DIR]
#
set -euo pipefail

# ── Args ──────────────────────────────────────────────────────────────────────

if [ $# -eq 0 ]; then
  echo "Error: conda env name required." >&2
  echo "Usage: bash setup-client.sh ENV_NAME [WORKSPACE_DIR]" >&2
  echo "  ENV_NAME       Name for the new conda environment" >&2
  echo "  WORKSPACE_DIR  Where to clone repos (default: ./tidybot_uni)" >&2
  exit 1
fi

ENV_NAME="$1"
WORKSPACE="${2:-./tidybot_uni}"
WORKSPACE="$(mkdir -p "$WORKSPACE" && cd "$WORKSPACE" && pwd)"

ORG="https://github.com/TidyBot-Services"

# Detect conda base (miniforge3 or miniconda3)
if [ -d "$HOME/miniforge3" ]; then
  CONDA_BASE="$HOME/miniforge3"
elif [ -d "$HOME/miniconda3" ]; then
  CONDA_BASE="$HOME/miniconda3"
else
  echo "Error: Neither miniforge3 nor miniconda3 found in \$HOME" >&2
  exit 1
fi
PIP="$CONDA_BASE/envs/$ENV_NAME/bin/pip"

echo "==> Workspace: $WORKSPACE"
echo "==> Conda env: $ENV_NAME"
echo "==> Conda:     $CONDA_BASE"
echo ""

# ── Clone repos ───────────────────────────────────────────────────────────────

clone() {
  local repo="$1" dest="$2"
  if [ -d "$dest/.git" ]; then
    echo "    skip $dest (already cloned)"
  else
    echo "    clone $repo → $dest"
    git clone --depth 1 -q "$ORG/$repo.git" "$dest"
  fi
}

echo "==> Cloning client repos ..."
clone Tidybot-Universe                     "$WORKSPACE/Tidybot-Universe"
clone TidyBot-Services.github.io           "$WORKSPACE/TidyBot-Services.github.io"

# ── Conda env ─────────────────────────────────────────────────────────────────

echo "==> Creating conda env '$ENV_NAME' (Python 3.11) ..."
conda create -n "$ENV_NAME" -y python=3.11 -c conda-forge

# ── Pip: lightweight client packages ─────────────────────────────────────────

echo "==> Installing client pip packages ..."
$PIP install \
  "websockets>=16.0" \
  "httpx>=0.28.0" \
  "requests>=2.32" \
  "pyyaml>=6.0" \
  "rich>=14.3" \
  claude-agent-sdk

# ── Workspace helpers ────────────────────────────────────────────────────────

# Create a convenience CLAUDE.md at workspace root if missing
if [ ! -f "$WORKSPACE/CLAUDE.md" ]; then
  cat > "$WORKSPACE/CLAUDE.md" << 'MDEOF'
# Tidybot Uni (Client)

Client-only workspace — no local servers. Connect to a remote agent server.

## Quick Start

```bash
# 1. Dashboard (port 8070)
cd TidyBot-Services.github.io && python3 -m http.server 8070 &

# 2. Orchestrator (port 8765/8766) — point at remote agent server
cd Tidybot-Universe/skill-agent-setup/claude-code && \
  AGENT_SERVER=http://<ROBOT_IP>:8080 \
  python3 agent_orchestrator.py --graph graphs/<graph-name>

# 3. Submit code to remote server
curl -X POST http://<ROBOT_IP>:8080/code/submit \
  -H "Content-Type: application/json" \
  -d '{"code": "from robot_sdk import arm; print(arm.get_state())"}'
```
MDEOF
  echo "==> Created $WORKSPACE/CLAUDE.md"
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "============================================"
echo "  Client setup complete!"
echo "============================================"
echo ""
echo "Start Claude Code (skill planner):"
echo ""
echo "  conda activate $ENV_NAME"
echo "  cd $WORKSPACE/Tidybot-Universe/skill-agent-setup/claude-code"
echo "  claude"
echo ""
echo "Connect to a remote agent server:"
echo ""
echo "  # Dashboard"
echo "  cd $WORKSPACE/TidyBot-Services.github.io"
echo "  python3 -m http.server 8070 &"
echo ""
echo "  # Orchestrator"
echo "  cd $WORKSPACE/Tidybot-Universe/skill-agent-setup/claude-code"
echo "  AGENT_SERVER=http://<ROBOT_IP>:8080 python3 agent_orchestrator.py --graph graphs/<graph-name>"
echo ""
echo "  # Submit code to remote server"
echo "  python3 submit_and_wait.py /tmp/test.py --agent-server http://<ROBOT_IP>:8080 --holder claude-code --no-eval"
echo ""
echo "============================================"
