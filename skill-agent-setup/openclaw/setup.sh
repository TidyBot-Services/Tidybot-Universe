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
            echo "  (default)  Add Tidybot files to an existing OpenClaw workspace"
            echo "  --fresh    Wipe workspace, sessions, and memory, then set up from scratch"
            exit 0 ;;
    esac
done

echo "=== Tidybot OpenClaw Setup ==="
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

# Copy Tidybot-specific workspace files
echo "Copying Tidybot workspace files..."
mkdir -p ~/.openclaw/workspace/skills ~/.openclaw/workspace/docs
cp "$SCRIPT_DIR/workspace/MISSION.md" ~/.openclaw/workspace/
cp "$SCRIPT_DIR/workspace/ROBOT.md" ~/.openclaw/workspace/
cp "$SCRIPT_DIR/workspace/HEARTBEAT.md" ~/.openclaw/workspace/
cp -r "$SCRIPT_DIR/workspace/skills/"* ~/.openclaw/workspace/skills/
cp -r "$SCRIPT_DIR/workspace/docs/"* ~/.openclaw/workspace/docs/
chmod +x ~/.openclaw/workspace/skills/tidybot-bundle
echo "  Copied MISSION.md, ROBOT.md, HEARTBEAT.md, skills/, docs/"

# Patch AGENTS.md with Tidybot additions (instead of replacing the whole file)
echo "Patching AGENTS.md with Tidybot additions..."
python3 << 'PATCHEOF'
import os, sys

path = os.path.expanduser("~/.openclaw/workspace/AGENTS.md")
if not os.path.exists(path):
    print("  Warning: AGENTS.md not found, skipping patch")
    sys.exit(0)

with open(path, "r") as f:
    content = f.read()

# Skip if already patched
if "Read `MISSION.md`" in content:
    print("  AGENTS.md already patched, skipping")
    sys.exit(0)

# Patch 1: Insert Tidybot checklist items (3-5) after "2. Read `USER.md`", renumber 3→6, 4→7
checklist_insert = (
    '3. Read `MISSION.md` — this is your mission in the Tidybot Universe, an open-source platform where humans and AI agents build and share robot skills and services\n'
    '4. Read `ROBOT.md` — this is the robot you can control (Franka Panda arm + mobile base + gripper, reachable via LAN)\n'
    '5. **Read the SDK guide** — `GET http://<ROBOT_IP>:8080/docs/guide/html` — this is how you talk to the robot. Read it before writing any robot code. Every session. No exceptions.\n'
)

content = content.replace(
    "2. Read `USER.md` — this is who you're helping\n3. Read `memory/",
    "2. Read `USER.md` — this is who you're helping\n" + checklist_insert + "6. Read `memory/"
)
content = content.replace(
    "4. **If in MAIN SESSION** (direct chat with your human): Also read `MEMORY.md`",
    "7. **If in MAIN SESSION** (direct chat with your human): Also read `MEMORY.md`"
)

with open(path, "w") as f:
    f.write(content)

print("  Patched AGENTS.md (added Tidybot session checklist items)")
PATCHEOF

# Add skills.load.extraDirs to config if not present
echo "Configuring skills directory..."
openclaw config set skills.load.extraDirs '["~/.openclaw/workspace/skills"]' 2>/dev/null || {
    echo "  Note: Could not auto-set config. Please add manually:"
    echo '  "skills": { "load": { "extraDirs": ["~/.openclaw/workspace/skills"] } }'
}

# Restart gateway to pick up changes
echo "Restarting gateway..."
openclaw gateway restart 2>/dev/null || openclaw gateway start

echo
echo "=== Setup Complete ==="
echo "Open a chat or run: openclaw dashboard"
