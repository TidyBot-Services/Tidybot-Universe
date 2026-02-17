#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Parse flags
FRESH=false
for arg in "$@"; do
    case "$arg" in
        --fresh) FRESH=true ;;
        --help|-h)
            echo "Usage: setup.sh [--fresh]"
            echo ""
            echo "  (default)  Add service agent files to an existing OpenClaw workspace"
            echo "  --fresh    Wipe workspace, sessions, and memory, then set up from scratch"
            exit 0 ;;
    esac
done

echo "=== TidyBot Service Agent — OpenClaw Setup ==="
if $FRESH; then echo "Mode: fresh (clean slate)"; else echo "Mode: integrate (existing workspace)"; fi
echo

# Check if OpenClaw is installed
if ! command -v openclaw &> /dev/null; then
    echo "Installing OpenClaw..."
    curl -fsSL https://openclaw.ai/install.sh | bash
    echo
fi

# Run onboarding if not already done
if [ ! -f ~/.openclaw/openclaw.json ]; then
    echo "Running OpenClaw onboarding..."
    openclaw onboard --install-daemon
    echo
fi

if $FRESH; then
    # Reset workspace and memory for a clean slate (keeps config + auth)
    echo "Resetting workspace and memory..."
    rm -rf ~/.openclaw/workspace/ 2>/dev/null || true
    rm -rf ~/.openclaw/agents/main/sessions/ 2>/dev/null || true
    rm -f ~/.openclaw/memory/main.sqlite 2>/dev/null || true

    # Regenerate default workspace files from OpenClaw templates
    echo "Regenerating default workspace files..."
    TEMPLATE_DIR="$(npm root -g)/openclaw/docs/reference/templates"
    if [ -d "$TEMPLATE_DIR" ]; then
        mkdir -p ~/.openclaw/workspace
        for f in AGENTS.md SOUL.md TOOLS.md IDENTITY.md USER.md BOOTSTRAP.md; do
            if [ -f "$TEMPLATE_DIR/$f" ]; then
                # Strip YAML front matter (--- ... ---) like OpenClaw's loadTemplate does
                sed '1{/^---$/!b};1,/^---$/d' "$TEMPLATE_DIR/$f" | sed '/./,$!d' > ~/.openclaw/workspace/"$f"
            fi
        done
        echo "  Copied default templates from OpenClaw package"
    else
        echo "  Warning: OpenClaw templates not found at $TEMPLATE_DIR"
        echo "  AGENTS.md may need manual setup"
    fi
fi

# Copy service-agent-specific workspace files
echo "Copying service agent workspace files..."
mkdir -p ~/.openclaw/workspace/docs ~/.openclaw/workspace/skills
cp "$SCRIPT_DIR/workspace/MISSION.md" ~/.openclaw/workspace/
cp "$SCRIPT_DIR/workspace/HEARTBEAT.md" ~/.openclaw/workspace/
cp "$SCRIPT_DIR/workspace/BOOTSTRAP.md" ~/.openclaw/workspace/
cp "$SCRIPT_DIR/workspace/SOUL.md" ~/.openclaw/workspace/
cp "$SCRIPT_DIR/workspace/TOOLS.md" ~/.openclaw/workspace/
cp -r "$SCRIPT_DIR/workspace/docs/"* ~/.openclaw/workspace/docs/
cp -r "$SCRIPT_DIR/workspace/skills/"* ~/.openclaw/workspace/skills/
echo "  Copied MISSION.md, HEARTBEAT.md, BOOTSTRAP.md, SOUL.md, TOOLS.md, docs/, skills/"

# Patch AGENTS.md with service agent additions
echo "Patching AGENTS.md with service agent additions..."
python3 << 'PATCHEOF'
import os, sys

path = os.path.expanduser("~/.openclaw/workspace/AGENTS.md")
if not os.path.exists(path):
    print("  Warning: AGENTS.md not found, skipping patch")
    sys.exit(0)

# Skip if already patched
with open(path, "r") as f:
    content = f.read()

if "Read `MISSION.md`" in content:
    print("  AGENTS.md already patched, skipping")
    sys.exit(0)

# Patch: Insert service agent checklist items after "2. Read `USER.md`", renumber subsequent items
checklist_insert = (
    '3. Read `MISSION.md` — this is your mission: build and maintain backend ML services for the TidyBot ecosystem\n'
    '4. Read `docs/CLIENT_SDK_SPEC.md` — all client SDKs must follow this spec\n'
)

content = content.replace(
    "2. Read `USER.md` — this is who you're helping\n3. Read `memory/",
    "2. Read `USER.md` — this is who you're helping\n" + checklist_insert + "5. Read `memory/"
)
content = content.replace(
    "4. **If in MAIN SESSION** (direct chat with your human): Also read `MEMORY.md`",
    "6. **If in MAIN SESSION** (direct chat with your human): Also read `MEMORY.md`"
)

with open(path, "w") as f:
    f.write(content)

print("  Patched AGENTS.md (added service agent session checklist items)")
PATCHEOF

# Configure skills directory
echo "Configuring skills directory..."
openclaw config set skills.load.extraDirs '["~/.openclaw/workspace/skills"]' 2>/dev/null || {
    echo "  Note: Could not auto-set config. Please add manually:"
    echo '  "skills": { "load": { "extraDirs": ["~/.openclaw/workspace/skills"] } }'
}

# Create the wishlist-monitor cron job
echo "Setting up wishlist-monitor cron job..."

CRON_MESSAGE='Check TidyBot-Services/services_wishlist for new service requests.

STEP 1 — FETCH: Use `gh api` (NOT web_fetch) to get wishlist.json:
  gh api repos/TidyBot-Services/services_wishlist/contents/wishlist.json --jq '"'"'.content'"'"' | base64 -d

STEP 2 — COMPARE: Read MEMORY.md for known completed/in-progress services. Compare against wishlist.

STEP 3 — AUTO-BUILD: For ANY new wishlist item with status "pending":
1. Update wishlist.json: set status to "building", assigned to your agent name
2. Use sessions_spawn to kick off a sub-agent build task for EACH new item
3. The sub-agent should: create repo, build FastAPI service + client SDK (following CLIENT_SDK_SPEC.md) + README, install deps, test, push to GitHub, deploy as systemd unit, update catalog.json
4. Use numpy<2 for torch compatibility
5. Assign the next available port (check catalog.json for used ports)

STEP 4 — REPORT: Summarize findings. Only report NEW items or status changes. If nothing changed, say so briefly.'

# Try to create the cron job via CLI (requires gateway running)
if openclaw cron add \
    --name "wishlist-monitor" \
    --every "1h" \
    --session "isolated" \
    --message "$CRON_MESSAGE" \
    --announce \
    --timeout-seconds 300 2>/dev/null; then
    echo "  Created wishlist-monitor cron job (polls every hour)"
else
    echo "  Note: Could not create cron job (gateway may not be running)"
    echo "  The agent will create it on first session, or create manually:"
    echo "  openclaw cron add --name wishlist-monitor --every 1h --session isolated --announce --message '...'"
fi

# Restart gateway to pick up changes
echo "Restarting gateway..."
openclaw gateway restart 2>/dev/null || openclaw gateway start 2>/dev/null || echo "  Note: Gateway not running. Start with: openclaw gateway start"

echo
echo "=== Setup Complete ==="
echo "Open a chat or run: openclaw dashboard"
