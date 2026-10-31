"""Extract portable content inventories from the author's r12 evidence.

This is an offline, deterministic extraction tool. It does not bless a new
installation or change the approved path exception list.
"""
import argparse
import hashlib
import json
from pathlib import Path


AUTHOR_LOCK = (Path('/home') / 'truares' / 'Documents' / 'Codex' /
               '2026-10-11/goal-curobov2-nvidia-service-1-service/outputs/revisions/r12/runtime-lock.json')
DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / 'curobo_v2/official'


def relative_files(files, root):
    root = Path(root)
    return {str(Path(name).relative_to(root)): sha for name, sha in sorted(files.items())
            if Path(name).is_relative_to(root) and Path(name) != root}


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, default=AUTHOR_LOCK)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    raw = args.lock.read_bytes()
    if args.lock == AUTHOR_LOCK and hashlib.sha256(raw).hexdigest() != '207d7e82a716cd8dd71a1f6ba56fe3843edf365a913065ab937a24238128399c':
        raise ValueError('Author r12 lock digest changed')
    lock = json.loads(raw)
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    source = relative_files(lock['files'], lock['library_root'])
    prefix = relative_files(lock['files'], lock['prefix'])
    deps = {name: {'path': str(Path(value['path']).relative_to(lock['prefix'])), 'sha256': value['sha256']}
            for name, value in sorted(lock['dependencies'].items())}
    native_raw = Path(lock['native_assets']['manifest']).read_bytes()
    if hashlib.sha256(native_raw).hexdigest() != lock['native_assets']['manifest_sha256']:
        raise ValueError('Author native asset manifest changed')
    native = json.loads(native_raw)
    assets = {row['relative']: row['sha256'] for row in native['files']}
    if (len(source), len(prefix), len(deps), len(assets)) != (890, 553, 11, 70254):
        raise ValueError('Author r12 inventory counts changed')
    write(out / 'curobo-78fd485-source.json', source)
    write(out / 'r12-distributions.json', lock['distributions'])
    write(out / 'r12-dependencies.json', deps)
    write(out / 'r12-prefix-files.json', prefix)
    encoded = json.dumps(assets, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    if len(encoded) > 5_000_000:
        write(out / 'r12-native-assets.json', {'format': 'canonical-relative-path-sha256-v1',
            'count': len(assets), 'sha256': hashlib.sha256(encoded).hexdigest()})
        print('native assets: canonical relative-path map SHA only; source map', len(encoded), 'bytes')
    else:
        write(out / 'r12-native-assets.json', assets)
        print('native assets: full relative-path map;', len(encoded), 'bytes')
    write(out / 'allowed-path-files.json', [])


if __name__ == '__main__':
    main()
