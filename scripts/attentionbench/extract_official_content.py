"""Extract portable content inventories from the author's r12 evidence.

This is an offline, deterministic extraction tool. It does not bless a new
installation or change the approved path exception list.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex

from curobo_v2.official_normalization import without_generator, normalized_record


AUTHOR_LOCK = (Path('/home') / 'truares' / 'Documents' / 'Codex' /
               '2026-10-11/goal-curobov2-nvidia-service-1-service/outputs/revisions/r12/runtime-lock.json')
DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / 'curobo_v2/official'


def relative_files(files, root):
    root = Path(root)
    return {str(Path(name).relative_to(root)): sha for name, sha in sorted(files.items())
            if Path(name).is_relative_to(root) and Path(name) != root}


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n')


def clone_origin(history: Path) -> str:
    origins = []
    for line in history.read_text().splitlines():
        if not line.startswith('# cmd: '):
            continue
        command = shlex.split(line.removeprefix('# cmd: '))
        if '--clone' not in command:
            continue
        if (len(command) < 2 or Path(command[0]).name != 'conda' or command[1] != 'create'
                or command.count('--clone') != 1 or command.index('--clone') + 1 >= len(command)
                or not Path(command[command.index('--clone') + 1]).is_absolute()):
            raise ValueError('Malformed conda clone line in ' + str(history))
        origins.append(command[command.index('--clone') + 1])
    if len(origins) != 1:
        raise ValueError('Expected exactly one conda clone origin in ' + str(history))
    return origins[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, default=AUTHOR_LOCK)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    raw = args.lock.read_bytes()
    if args.lock == AUTHOR_LOCK and hashlib.sha256(raw).hexdigest() != '207d7e82a716cd8dd71a1f6ba56fe3843edf365a913065ab937a24238128399c':
        raise ValueError('Author r12 lock digest changed')
    lock = json.loads(raw)
    author_prefix = Path(lock['prefix'])
    origin_prefix = clone_origin(author_prefix / 'conda-meta/history')
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
    wheels = {}
    records = {}
    for name, expected in prefix.items():
        if name.endswith('/RECORD'):
            data = (author_prefix / name).read_bytes()
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError('Author RECORD differs from r12 lock: ' + name)
            normalized = normalized_record(data, Path(name).parent.name)
            if normalized is None:
                raise ValueError('Malformed author RECORD: ' + name)
            records[name] = hashlib.sha256(normalized).hexdigest()
        if name.endswith('/WHEEL'):
            data = (author_prefix / name).read_bytes()
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError('Author WHEEL differs from r12 lock: ' + name)
            stripped = without_generator(data)
            wheels[name] = hashlib.sha256(stripped if stripped is not None else data).hexdigest()
    write(out / 'r12-wheel-normalized.json', wheels)
    write(out / 'r12-record-normalized.json', records)
    binary = (author_prefix / 'bin/python3.11').read_bytes()
    if hashlib.sha256(binary).hexdigest() != prefix['bin/python3.11']:
        raise ValueError('Author python binary differs from r12 lock')
    # The author environment was cloned from this conda installation. Its ELF
    # prefix slot retains that original path, not the clone's current prefix.
    slot = binary[3390864:3390864 + 256]
    embedded = slot.split(b'\0', 1)[0]
    if (not embedded.startswith(b'/home/') or len(embedded) >= 256
            or slot != embedded + b'\0' * (256 - len(embedded))):
        raise ValueError('Unexpected author conda binary prefix slot')
    existing_paths = json.loads((out / 'author-paths.json').read_bytes())
    if not isinstance(existing_paths.get('script_prefix'), str):
        raise ValueError('Original RECORD script prefix must be established separately')
    write(out / 'author-paths.json', {'prefix': lock['prefix'], 'library_root': lock['library_root'],
                                    'binary_prefix': embedded.decode('utf-8'), 'binary_prefix_slot': 256,
                                    'script_prefix': existing_paths['script_prefix'],
                                    'origin_prefix': origin_prefix})
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
