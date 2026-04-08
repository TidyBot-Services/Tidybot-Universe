#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Defaults
SERVER_IP=""
USERNAME=""
SERVICE_DIR=""
LOCAL_PORT=8090
POLL_INTERVAL=30

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --server-ip) SERVER_IP="$2"; shift 2 ;;
        --username) USERNAME="$2"; shift 2 ;;
        --service-dir) SERVICE_DIR="$2"; shift 2 ;;
        --port) LOCAL_PORT="$2"; shift 2 ;;
        --poll-interval) POLL_INTERVAL="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: setup.sh [--server-ip IP] [--username USER] [--service-dir DIR] [--port PORT]"
            echo ""
            echo "Interactive setup for remote service server access."
            echo "If arguments are not provided, you will be prompted."
            exit 0 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

echo "============================================"
echo "  Service Server Setup"
echo "============================================"
echo ""

# [1/5] Get server details
echo "[1/5] Server configuration"
if [[ -z "$SERVER_IP" ]]; then
    read -p "  Remote server IP: " SERVER_IP
fi
if [[ -z "$USERNAME" ]]; then
    read -p "  SSH username: " USERNAME
fi
if [[ -z "$SERVICE_DIR" ]]; then
    read -p "  Service directory on server: " SERVICE_DIR
fi
echo "  Local doc server port: $LOCAL_PORT"
echo ""

# [2/5] Test SSH connectivity
echo "[2/5] Testing SSH connection to $USERNAME@$SERVER_IP..."
if ssh -o ConnectTimeout=5 -o BatchMode=yes "$USERNAME@$SERVER_IP" "echo ok" &>/dev/null; then
    echo "  SSH connection OK"
else
    echo "  SSH connection failed. Setting up key-based auth..."
    ssh-copy-id "$USERNAME@$SERVER_IP"
    # Retry
    if ssh -o ConnectTimeout=5 -o BatchMode=yes "$USERNAME@$SERVER_IP" "echo ok" &>/dev/null; then
        echo "  SSH connection OK after key setup"
    else
        echo "  ERROR: Still cannot connect. Check IP/username and try again."
        exit 1
    fi
fi
echo ""

# [3/5] Verify service directory
echo "[3/5] Verifying service directory: $SERVICE_DIR"
if ssh -o ConnectTimeout=5 "$USERNAME@$SERVER_IP" "test -d '$SERVICE_DIR'" &>/dev/null; then
    # Count subdirectories
    COUNT=$(ssh "$USERNAME@$SERVER_IP" "ls -1d '$SERVICE_DIR'/*/ 2>/dev/null | wc -l")
    echo "  Directory exists with $COUNT subdirectories"
else
    echo "  ERROR: Directory '$SERVICE_DIR' does not exist on $SERVER_IP"
    exit 1
fi
echo ""

# [4/5] Write config
echo "[4/5] Writing config to $SCRIPT_DIR/config.json"
cat > "$SCRIPT_DIR/config.json" <<EOF
{
    "server_ip": "$SERVER_IP",
    "username": "$USERNAME",
    "service_dir": "$SERVICE_DIR",
    "local_port": $LOCAL_PORT,
    "poll_interval_seconds": $POLL_INTERVAL
}
EOF
echo "  Config saved"
echo ""

# [5/5] Run initial scan
echo "[5/5] Running initial service scan..."
python3 "$SCRIPT_DIR/service_scanner.py" --config "$SCRIPT_DIR/config.json" --scan-once
echo ""

echo "============================================"
echo "  Setup complete!"
echo ""
echo "  Start the doc server:"
echo "    cd $SCRIPT_DIR"
echo "    python3 service_scanner.py"
echo ""
echo "  Then query services:"
echo "    curl http://localhost:$LOCAL_PORT/services"
echo "    curl http://localhost:$LOCAL_PORT/docs"
echo "============================================"
