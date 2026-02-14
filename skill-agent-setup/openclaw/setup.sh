#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Tidybot OpenClaw Setup ==="
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

# Copy Tidybot-specific workspace files (new files only, not modifying defaults)
echo "Copying Tidybot workspace files..."
mkdir -p ~/.openclaw/workspace/skills
cp "$SCRIPT_DIR/workspace/MISSION.md" ~/.openclaw/workspace/
cp "$SCRIPT_DIR/workspace/ROBOT.md" ~/.openclaw/workspace/
cp "$SCRIPT_DIR/workspace/HEARTBEAT.md" ~/.openclaw/workspace/
cp -r "$SCRIPT_DIR/workspace/skills/"* ~/.openclaw/workspace/skills/
echo "  Copied MISSION.md, ROBOT.md, HEARTBEAT.md, skills/"

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
    '3. Read `MISSION.md` — this is your mission and how you fit into the Tidybot Universe\n'
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

# Patch 2: Insert "Tidybot Universe" section before "## Tools"
tidybot_section = (
    "## Tidybot Universe\n"
    "\n"
    "You have access to physical robot hardware over LAN. Read `MISSION.md` for what this means and how you fit in.\n"
    "\n"
)
content = content.replace(
    "## Tools\n",
    tidybot_section + "## Tools\n"
)

with open(path, "w") as f:
    f.write(content)

print("  Patched AGENTS.md (added session checklist items + Tidybot Universe section)")
PATCHEOF

# Add skills.load.extraDirs to config if not present
echo "Configuring skills directory..."
openclaw config set skills.load.extraDirs '["~/.openclaw/workspace/skills"]' 2>/dev/null || {
    echo "  Note: Could not auto-set config. Please add manually:"
    echo '  "skills": { "load": { "extraDirs": ["~/.openclaw/workspace/skills"] } }'
}

# Clear existing sessions for fresh start
echo "Clearing existing sessions..."
rm -rf ~/.openclaw/agents/main/sessions/ 2>/dev/null || true
rm -f ~/.openclaw/memory/main.sqlite 2>/dev/null || true

# Restart gateway
echo "Restarting OpenClaw gateway..."
openclaw gateway restart 2>/dev/null || openclaw gateway start

echo
echo "=== Setup Complete ==="
echo "Open a chat or run: openclaw dashboard"
