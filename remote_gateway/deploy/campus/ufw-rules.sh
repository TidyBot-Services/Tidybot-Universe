#!/bin/sh
# Print only by default. Host is an IP; ranges must come from SEAS IT, including VPN and Wi-Fi.
# No defaults are guessed, no existing rules are removed, and port 80 is never opened.
set -eu
exec "${PYTHON:-python3}" - "$@" <<'PY'
import ipaddress
import os
import shlex
import subprocess
import sys

args = sys.argv[1:]
apply = bool(args and args[0] == '--apply')
if apply:args.pop(0)
if len(args) < 2:raise SystemExit('Usage: ufw-rules.sh [--apply] HOST_IP CIDR [CIDR ...]')
try:
    host = ipaddress.ip_address(args[0])
    ranges = [ipaddress.ip_network(value) for value in args[1:]]
    if any(net.version != host.version for net in ranges):raise ValueError('host and ranges must use the same IP version')
except ValueError as error:raise SystemExit(str(error))
if apply and os.geteuid() != 0:raise SystemExit('--apply requires root')
for net in ranges:
    command = ['ufw', 'allow', 'from', str(net), 'to', str(host), 'port', '443', 'proto', 'tcp']
    print(shlex.join(command), flush=True)
    if apply:subprocess.run(command, check=True)
PY
