#!/bin/sh
# Pause first, then make nginx return 503 before stopping the gateway.
set -eu
test "$#" -ge 2 && test "$#" -le 3 || { echo "Usage: $0 CONFIG MAINTENANCE_FILE [UNIT]" >&2; exit 2; }
"${PYTHON:-python3}" -m remote_gateway --config "$1" pause
touch -- "$2"
systemctl --user stop "${3:-attentionbench-public-gateway.service}"
