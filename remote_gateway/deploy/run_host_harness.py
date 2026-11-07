#!/usr/bin/env python3
"""Start the public harness in an installed workspace, on a run host: its own data, no Memory, loopback only.

Usage: run_host_harness.py WORKSPACE DATA [PORT] [--key-stdin]. DATA holds gateway.secret, read through a file descriptor
so it never appears in an environment or a command line. The lab key comes from ~/.litellm-key, or with --key-stdin from
the first line of standard input, which keeps it off this host's disk.
"""
import os
from pathlib import Path
import sys


def main(workspace, data, port='8860', key=None):
    ws, data = Path(workspace).resolve(), Path(data).resolve()
    sys.path.insert(0, str(ws/'repos/attentionbench/scripts/attentionbench'))
    import hostenv
    secret = data/'gateway.secret'
    if secret.stat().st_mode & 0o077:raise SystemExit(f'{secret} must be readable by its owner only')
    env = hostenv.clean_env(ws)
    env.update(ATTENTIONBENCH_WORKSPACE=str(ws), PYTHONPATH=str(ws/'repos/attentionbench'), PYTHONDONTWRITEBYTECODE='1',
               ATTENTION_ALLOW_CUSTOM_PROVIDER_URL='1')
    if key:env['PARCC_API_KEY'] = key
    fd = os.open(secret, os.O_RDONLY)
    if fd != 3:
        os.dup2(fd, 3);os.close(fd)
    os.set_inheritable(3, True)
    os.chdir(ws/'repos/attentionbench')
    python = str(ws/'envs/ui/bin/python')
    os.execve(python, [python, '-B', '-u', '-m', 'benchmarks.attention_harness.ui_server', '--store', str(data/'attention.sqlite3'),
                       '--artifact-root', str(data/'artifacts'), '--dev-v2-root', str(data/'runs'), '--dev-v2-per-run-budget',
                       '--port', port, '--public-gateway-mode', '--gateway-secret-fd', '3'], env)


if __name__ == '__main__':
    given = [value for value in sys.argv[1:] if value != '--key-stdin']
    if len(given) not in (2, 3):raise SystemExit(__doc__)
    main(*given, **({'key': sys.stdin.readline().strip()} if '--key-stdin' in sys.argv else {}))
