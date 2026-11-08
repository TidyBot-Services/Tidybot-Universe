#!/usr/bin/env python3
"""Start the public harness in an installed workspace, on a run host: its own data, no Memory, loopback only.

DATA holds the harness store, artifacts and runs. --secret defaults to DATA/gateway.secret;
the secret is passed through a file descriptor, never in an environment or command line.
Runs time-share the robot using --sim-lock (default DATA/sim.lock), up to --run-slots
(default 3) open at once. The legacy port default is 8860; campus units use 8870.
--key-source chooses home (~/.litellm-key), stdin (first line), credential (systemd's
$CREDENTIALS_DIRECTORY/parcc-key), or none. Only home permits a home-file fallback.
Without --key-source, the legacy home or --key-stdin behavior remains available.
"""
import argparse
import os
from pathlib import Path
import sys


def build_env(workspace, *, key_source=None, key=None):
    ws = Path(workspace).resolve()
    sys.path.insert(0, str(ws/'repos/attentionbench/scripts/attentionbench'))
    import hostenv
    env = hostenv.clean_env(ws)
    env.update(ATTENTIONBENCH_WORKSPACE=str(ws), PYTHONPATH=str(ws/'repos/attentionbench'), PYTHONDONTWRITEBYTECODE='1',
               ATTENTION_ALLOW_CUSTOM_PROVIDER_URL='1')
    source = key_source or ('stdin' if key is not None else 'home')
    if source not in ('home', 'stdin', 'credential', 'none'):
        raise ValueError('invalid key source')
    if source != 'home':
        env.pop('PARCC_API_KEY', None)
        env.pop('LITELLM_KEY', None)
        env['ATTENTION_NO_HOME_LAB_KEY'] = '1'
        if source == 'stdin':
            key = sys.stdin.readline().strip() if key is None else key
        elif source == 'credential':
            directory = os.environ.get('CREDENTIALS_DIRECTORY')
            if not directory:raise SystemExit('credential key source requires CREDENTIALS_DIRECTORY')
            key = (Path(directory)/'parcc-key').read_text().strip()
        else:
            key = None
    # home: as before, the launcher reads no file; the harness itself may fall back to ~/.litellm-key.
    if key:env['PARCC_API_KEY'] = key
    return env


def main(workspace, data, port='8860', key=None, sim_lock=None, run_slots='3', secret=None, key_source=None):
    ws, data = Path(workspace).resolve(), Path(data).resolve()
    secret = Path(secret).resolve() if secret is not None else data/'gateway.secret'
    if secret.stat().st_mode & 0o077:raise SystemExit(f'{secret} must be readable by its owner only')
    env = build_env(ws, key_source=key_source, key=key)
    fd = os.open(secret, os.O_RDONLY)
    if fd != 3:
        os.dup2(fd, 3);os.close(fd)
    os.set_inheritable(3, True)
    os.chdir(ws/'repos/attentionbench')
    python = str(ws/'envs/ui/bin/python')
    os.execve(python, [python, '-B', '-u', '-m', 'benchmarks.attention_harness.ui_server', '--store', str(data/'attention.sqlite3'),
                       '--artifact-root', str(data/'artifacts'), '--dev-v2-root', str(data/'runs'), '--dev-v2-per-run-budget',
                       '--port', port, '--public-gateway-mode', '--gateway-secret-fd', '3',
                       '--sim-lock', str(Path(sim_lock).resolve() if sim_lock else data/'sim.lock'), '--run-slots', str(int(run_slots))], env)


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workspace')
    parser.add_argument('data')
    parser.add_argument('port', nargs='?', default='8860')
    parser.add_argument('--secret', help='gateway secret path (default: DATA/gateway.secret)')
    parser.add_argument('--sim-lock')
    parser.add_argument('--run-slots', default='3')
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument('--key-source', choices=('home', 'stdin', 'credential', 'none'))
    sources.add_argument('--key-stdin', dest='key_source', action='store_const', const='stdin', help='alias for --key-source stdin')
    main(**vars(parser.parse_args(argv)))


if __name__ == '__main__':
    cli()
