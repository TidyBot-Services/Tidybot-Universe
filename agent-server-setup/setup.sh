#!/usr/bin/env bash
# setup.sh — Set up the agent server's service catalog sync.
#
# This script:
#   1. Clones the services wishlist repo (shared catalog)
#   2. Creates the service_clients directory
#   3. Installs sync_catalog.sh and a cron job to keep clients up to date
#   4. Runs the first sync
#
# Usage:
#   ./setup.sh                                           # uses defaults
#   ./setup.sh --agent-server ~/my-agent-server          # custom agent server path
#   ./setup.sh --interval 5                              # sync every 5 minutes

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Defaults ---
AGENT_SERVER_DIR="$HOME/tidybot_uni/agent_server"
WISHLIST_DIR="$HOME/tidybot_uni/backend_wishlist"
INTERVAL=2  # minutes

# --- Parse args ---
while [[ $# -gt 0 ]]; do
    case $1 in
        --agent-server) AGENT_SERVER_DIR="$2"; shift 2 ;;
        --wishlist-dir) WISHLIST_DIR="$2"; shift 2 ;;
        --interval) INTERVAL="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: ./setup.sh [--agent-server DIR] [--wishlist-dir DIR] [--interval MINUTES]"
            echo ""
            echo "Options:"
            echo "  --agent-server DIR    Path to agent server (default: ~/tidybot_uni/agent_server)"
            echo "  --wishlist-dir DIR    Path to wishlist repo (default: ~/tidybot_uni/backend_wishlist)"
            echo "  --interval MINUTES    Cron interval in minutes (default: 2)"
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

SERVICES_DIR="$AGENT_SERVER_DIR/service_clients"
SYNC_SCRIPT="$AGENT_SERVER_DIR/sync_catalog.sh"

echo "=== Agent Server Setup ==="
echo "Agent server:   $AGENT_SERVER_DIR"
echo "Wishlist repo:  $WISHLIST_DIR"
echo "Service clients: $SERVICES_DIR"
echo "Sync interval:  every $INTERVAL minutes"
echo ""

# --- 1. Clone wishlist repo if needed ---
if [ -d "$WISHLIST_DIR/.git" ]; then
    echo "[1/4] Wishlist repo already cloned at $WISHLIST_DIR"
else
    echo "[1/4] Cloning services wishlist..."
    git clone https://github.com/TidyBot-Services/services_wishlist.git "$WISHLIST_DIR"
fi

# --- 2. Create service_clients directory ---
if [ -d "$SERVICES_DIR" ]; then
    echo "[2/4] Service clients directory exists at $SERVICES_DIR"
else
    echo "[2/4] Creating service clients directory..."
    mkdir -p "$SERVICES_DIR"
fi

# --- 3. Install sync_catalog.sh ---
echo "[3/4] Installing sync_catalog.sh to $SYNC_SCRIPT"
cp "$SCRIPT_DIR/sync_catalog.sh" "$SYNC_SCRIPT"
chmod +x "$SYNC_SCRIPT"

# --- 4. Install cron job ---
CRON_CMD="WISHLIST_DIR=$WISHLIST_DIR SERVICES_DIR=$SERVICES_DIR $SYNC_SCRIPT"
CRON_LINE="*/$INTERVAL * * * * $CRON_CMD"
CRON_COMMENT="# Sync service catalog"

# Check if cron job already exists
if crontab -l 2>/dev/null | grep -qF "$SYNC_SCRIPT"; then
    echo "[4/4] Cron job already exists, updating..."
    # Remove old entry and add new one
    (crontab -l 2>/dev/null | grep -vF "sync_catalog" | grep -v "^# Sync service catalog"; echo "$CRON_COMMENT"; echo "$CRON_LINE") | crontab -
else
    echo "[4/4] Installing cron job..."
    (crontab -l 2>/dev/null; echo "$CRON_COMMENT"; echo "$CRON_LINE") | crontab -
fi

echo ""
echo "=== Running first sync ==="
WISHLIST_DIR="$WISHLIST_DIR" SERVICES_DIR="$SERVICES_DIR" "$SYNC_SCRIPT"

echo ""
echo "=== Done ==="
echo "Service catalog will sync every $INTERVAL minutes."
echo "Logs: $HOME/tidybot_uni/sync_catalog.log"
echo ""
echo "To verify: crontab -l"
echo "To check logs: tail -f ~/tidybot_uni/sync_catalog.log"
