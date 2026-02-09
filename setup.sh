#!/bin/bash
set -e

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

# Copy workspace files
echo "Copying Tidybot workspace templates..."
mkdir -p ~/.openclaw/workspace
cp -r workspace/* ~/.openclaw/workspace/
echo "  Copied to ~/.openclaw/workspace/"

# Add skills.load.extraDirs to config if not present
echo "Configuring skills directory..."
openclaw config set skills.load.extraDirs '["~/.openclaw/workspace/skills", "~/.openclaw/workspace/collaboration"]' 2>/dev/null || {
    echo "  Note: Could not auto-set config. Please add manually:"
    echo '  "skills": { "load": { "extraDirs": ["~/.openclaw/workspace/skills", "~/.openclaw/workspace/collaboration"] } }'
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
