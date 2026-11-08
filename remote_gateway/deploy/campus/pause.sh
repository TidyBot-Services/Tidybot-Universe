#!/bin/sh
# Stop admitting runs while existing runs continue. Pass the gateway config.
set -eu
test "$#" -eq 1 || { echo "Usage: $0 CONFIG" >&2; exit 2; }
"${PYTHON:-python3}" -m remote_gateway --config "$1" pause
