"""python -m remote_gateway --config FILE {serve,secret,invite,revoke-invite,revoke-user,invites,pause,resume}"""
import argparse
import os
from pathlib import Path
import secrets
import threading

from .config import load
from .store import Store


def main():
    parser = argparse.ArgumentParser(prog='remote_gateway', description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('serve', help='run the gateway')
    commands.add_parser('secret', help='create the secret shared with the harness, once')
    invite = commands.add_parser('invite', help='print a new invite code, once')
    invite.add_argument('--days', type=int, default=14)
    commands.add_parser('invites', help='list invites, without their codes')
    for name, field in (('revoke-invite', 'invite'), ('revoke-user', 'user')):commands.add_parser(name).add_argument(field)
    commands.add_parser('pause', help='refuse new runs; running ones continue')
    commands.add_parser('resume', help='accept new runs again')
    args = parser.parse_args()
    config = load(args.config)
    config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.command == 'serve':
        from .server import create_server
        server = create_server(config)
        threading.Thread(target=server.gateway.loop, args=(threading.Event(),), daemon=True).start()
        print(f'gateway on http://{config.listen_host}:{config.listen_port}/', flush=True)
        server.serve_forever()
    elif args.command == 'secret':
        descriptor = os.open(config.secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # Never overwrites: both processes share it.
        with os.fdopen(descriptor, 'w') as stream:stream.write(secrets.token_urlsafe(48))
    elif args.command == 'pause':
        config.pause_file.touch()
    elif args.command == 'resume':
        config.pause_file.unlink(missing_ok=True)
    else:
        store = Store(config.database)
        if args.command == 'invite':
            invite_id, code = store.create_invite(days=args.days)
            print(f'{invite_id} {code}')
        elif args.command == 'invites':
            for row in store.invites():print(row)
        elif args.command == 'revoke-invite':
            print('revoked' if store.revoke_invite(args.invite) else 'no such invite')
        elif args.command == 'revoke-user':
            store.revoke_user(args.user)


if __name__ == '__main__':
    main()
