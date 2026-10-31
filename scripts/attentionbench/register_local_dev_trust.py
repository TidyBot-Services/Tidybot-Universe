"""Write a protected, dev-only local planner registry after verifying a built chain."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from curobo_v2 import identity


def entry(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('Built lock path must be absolute and unlinked')
    raw = path.read_bytes()
    return {'lock_path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}


def register(official_parent, d05, d06, dev_local_root, build_proof, *, gpu='', driver=''):
    path = identity._local_registry_path()
    if path is None or not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('planner.local_dev_trust path key is required')
    if path.exists() or path.is_symlink():
        raise ValueError('Local registry already exists')
    proof = json.loads(Path(build_proof).read_bytes())
    parent, five, six = entry(official_parent), entry(d05), entry(d06)
    root = Path(dev_local_root)
    if not root.is_absolute() or root.is_symlink():
        raise ValueError('dev-local root must be absolute and unlinked')
    registry = {
        'schema': 'attentionbench.curobo-v2.local-dev-trust.v1',
        'official_commit': identity.OFFICIAL_COMMIT,
        'official_parent': parent,
        'chain': {'d05': five, 'd06': {**six, 'derived_from_sha256': five['sha256'],
                                       'profile': 'r3-20261025'}},
        'dev_local_root': str(root),
        'build_proof': proof,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'machine': {'hostname': socket.gethostname(), 'gpu': gpu, 'driver': driver},
    }
    for part in (five, six):
        lock = json.loads(Path(part['lock_path']).read_bytes())
        if not identity._validate_local_chain(registry, lock, part['sha256'], part['lock_path']):
            raise ValueError('Built chain failed local identity verification')
        identity.verify_files(lock)
    identity.verify_files(json.loads(Path(parent['lock_path']).read_bytes()))
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(registry, sort_keys=True, indent=2) + '\n').encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o444)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(raw)
        path.chmod(0o444)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('official-parent', 'd05', 'd06', 'dev-local-root', 'build-proof'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--gpu', default='')
    parser.add_argument('--driver', default='')
    args = parser.parse_args()
    print(register(args.official_parent, args.d05, args.d06, args.dev_local_root,
                   args.build_proof, gpu=args.gpu, driver=args.driver))


if __name__ == '__main__':
    main()
