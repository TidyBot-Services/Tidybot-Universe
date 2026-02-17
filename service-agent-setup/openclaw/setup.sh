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
mkdir -p ~/.openclaw/workspace/docs
cp "$SCRIPT_DIR/workspace/MISSION.md" ~/.openclaw/workspace/
cp "$SCRIPT_DIR/workspace/HEARTBEAT.md" ~/.openclaw/workspace/
cp -r "$SCRIPT_DIR/workspace/docs/"* ~/.openclaw/workspace/docs/
echo "  Copied MISSION.md, HEARTBEAT.md, docs/"

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

# Patch SOUL.md vibe for service agent personality
echo "Patching SOUL.md..."
python3 << 'PATCHEOF'
import os, sys

path = os.path.expanduser("~/.openclaw/workspace/SOUL.md")
if not os.path.exists(path):
    print("  Warning: SOUL.md not found, skipping patch")
    sys.exit(0)

with open(path, "r") as f:
    content = f.read()

if "backend service agent" in content:
    print("  SOUL.md already patched, skipping")
    sys.exit(0)

old_vibe = "Be the assistant you'd actually want to talk to. Concise when needed, thorough when it matters. Not a corporate drone. Not a sycophant. Just... good."
new_vibe = "You're a backend service agent. Concise, efficient, and focused on execution. No fluff. Just results. Build services, deploy them, keep them running."

content = content.replace(old_vibe, new_vibe)

with open(path, "w") as f:
    f.write(content)

print("  Patched SOUL.md (service agent vibe)")
PATCHEOF

# Create the wishlist-monitor cron job
echo "Setting up wishlist-monitor cron job..."
echo "  The agent will create the cron job on first session (requires OpenClaw gateway running)"
echo "  To create manually: openclaw cron add --name wishlist-monitor ..."

# Restart gateway to pick up changes
echo "Restarting gateway..."
openclaw gateway restart 2>/dev/null || openclaw gateway start 2>/dev/null || echo "  Note: Gateway not running. Start with: openclaw gateway start"

echo
echo "=== Setup Complete ==="
echo "Open a chat or run: openclaw dashboard"
echo
echo "The agent will:"
echo "  1. Introduce itself and set up identity"
echo "  2. Create the wishlist-monitor cron job (polls hourly)"
echo "  3. Automatically build services when new wishlist items appear"
