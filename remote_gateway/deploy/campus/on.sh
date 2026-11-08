#!/bin/sh
# Keep maintenance in place until the gateway has started and admission is resumed.
set -eu
test "$#" -ge 2 && test "$#" -le 3 || { echo "Usage: $0 CONFIG MAINTENANCE_FILE [UNIT]" >&2; exit 2; }
systemctl --user start "${3:-attentionbench-public-gateway.service}"
"${PYTHON:-python3}" -m remote_gateway --config "$1" resume
rm -f -- "$2"
