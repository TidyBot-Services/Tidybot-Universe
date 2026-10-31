"""Narrow, byte-checked portability rules for the official environment."""
from __future__ import annotations

import hashlib
import base64
import csv
import re
from pathlib import Path


GENERATOR = re.compile(rb'Generator: setuptools \([0-9]+\.[0-9]+\.[0-9]+\)\n')
EMPTY_REQUESTED = b',sha256=47DEQpj8HBSa-_TImW-5JCeuQeRkm5NMpJWZG3hSuFU,0'
RECORD_HASH = re.compile(rb'sha256=[A-Za-z0-9_-]{43}')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rule_for(name: str) -> str | None:
    path = Path(name)
    if len(path.parts) < 4 or path.parts[0] != 'lib' or not path.parts[1].startswith('python3.') or path.parts[2] != 'site-packages':
        return 'binary-prefix' if name == 'bin/python3.11' else None
    if len(path.parts) == 5 and path.parts[3].endswith('.dist-info'):
        if path.name == 'RECORD':
            return 'record-requested'
        if path.name == 'WHEEL':
            return 'wheel-generator'
        if path.name == 'direct_url.json' and path.parts[3].startswith('nvidia_curobo-'):
            return 'text-path'
    if (len(path.parts) == 4 and path.name.startswith('__editable__')
            and 'nvidia_curobo' in path.name and path.name.endswith('_finder.py')):
        return 'text-path'
    return None


def without_generator(data: bytes) -> bytes | None:
    lines = data.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines) if GENERATOR.fullmatch(line)]
    if len(matches) != 1:
        return None
    del lines[matches[0]]
    return b''.join(lines)


def record_path(line: bytes) -> str | None:
    try:
        row = next(csv.reader([line.decode('utf-8')], strict=True))
    except (UnicodeError, csv.Error):
        return None
    return row[0] if len(row) == 3 and row[0] else None


def normalized_record(data: bytes, package: str, local_prefix: str | None = None,
                      author_prefix: str | None = None,
                      script_root: Path | None = None,
                      author_shebangs: dict | None = None) -> bytes | None:
    """Strip only cache rows and this package's empty REQUESTED row.

    When prefixes are supplied, bind every changed bin row to the actual local
    script bytes before substituting the digest of its relocated contents.
    """
    requested = (package + '/REQUESTED').encode() + EMPTY_REQUESTED
    output = []
    requested_count = 0
    for line in data.splitlines(keepends=True):
        body = line.removesuffix(b'\n').removesuffix(b'\r')
        path = record_path(body)
        if path is None:
            return None
        if body == requested:
            requested_count += 1
            continue
        if path.endswith('.pyc'):
            continue
        if path.startswith('../../../bin/') and local_prefix is not None:
            script = path.removeprefix('../../../bin/')
            if (not script or '/' in script or script in ('.', '..')
                    or not isinstance(author_prefix, str) or not author_prefix.startswith('/')
                    or not isinstance(local_prefix, str) or not local_prefix.startswith('/')):
                return None
            fields = body.rsplit(b',', 2)
            if len(fields) != 3:
                return None
            raw_path, recorded_hash, recorded_size = fields
            if (raw_path != path.encode() or not RECORD_HASH.fullmatch(recorded_hash)
                    or not recorded_size.isdigit()):
                return None
            if script_root is None:
                return None
            target = script_root / 'bin' / script
            if not target.is_file() or target.is_symlink():
                return None
            content = target.read_bytes()
            digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b'=')
            if recorded_hash != b'sha256=' + digest or int(recorded_size) != len(content):
                return None
            old_line, sep, rest = content.partition(b'\n')
            author_line = (author_shebangs or {}).get(script)
            if (not sep or not old_line.startswith(b'#!' + local_prefix.encode() + b'/bin/python')
                    or old_line not in (b'#!' + local_prefix.encode() + b'/bin/python',
                                        b'#!' + local_prefix.encode() + b'/bin/python3.11')
                    or not isinstance(author_line, str)
                    or not author_line.startswith('#!' + author_prefix + '/bin/python')
                    or '\n' in author_line or '\r' in author_line):
                return None
            relocated = author_line.encode() + sep + rest
            digest = base64.urlsafe_b64encode(hashlib.sha256(relocated).digest()).rstrip(b'=')
            body = raw_path + b',sha256=' + digest + b',' + str(len(relocated)).encode()
            line = body + (b'\r\n' if line.endswith(b'\r\n') else b'\n')
        output.append(line)
    if local_prefix is not None and requested_count != 1:
        return None
    return b''.join(output)


def normalized(name: str, data: bytes, local_prefix: str, local_library: str,
               author_paths: dict, wheel_hashes: dict,
               record_hashes: dict | None = None,
               script_root: Path | None = None,
               author_shebangs: dict | None = None) -> tuple[str, str] | None:
    """Return (rule, normalized digest), or reject an inapplicable/invalid rule."""
    rule = rule_for(name)
    if rule == 'record-requested':
        result = normalized_record(data, Path(name).parent.name, local_prefix,
                                   author_paths.get('script_prefix'), script_root,
                                   author_shebangs)
        return (rule, sha(result)) if result is not None else None
    if rule == 'wheel-generator':
        without = without_generator(data)
        if without is None or wheel_hashes.get(name) != sha(without):
            return None
        return rule, sha(without)
    if rule == 'text-path':
        pairs = ((local_prefix, author_paths['prefix']),
                 (local_library, author_paths['library_root']))
        if any(not isinstance(a, str) or not isinstance(b, str) or not a or not b for a, b in pairs):
            return None
        changed = False
        for old, new in pairs:
            old_bytes = old.encode()
            if old_bytes in data:
                data = data.replace(old_bytes, new.encode())
                changed = True
        return (rule, sha(data)) if changed else None
    if rule == 'binary-prefix':
        old = local_prefix.encode()
        new = author_paths['binary_prefix'].encode()
        size = author_paths['binary_prefix_slot']
        if (not old.startswith(b'/') or not new.startswith(b'/') or not isinstance(size, int)
                or size <= max(len(old), len(new))):
            return None
        local_slot = old + b'\0' * (size - len(old))
        if data.count(local_slot) != 1:
            return None
        return rule, sha(data.replace(local_slot, new + b'\0' * (size - len(new)), 1))
    return None


def matches(name: str, path: Path, local_sha: str, expected_sha: str,
            local_prefix: str, local_library: str, author_paths: dict,
            wheel_hashes: dict, record_hashes: dict | None = None,
            author_shebangs: dict | None = None) -> str | None:
    """Return the applied rule only after binding the bytes to the local lock."""
    if not isinstance(local_sha, str) or not isinstance(expected_sha, str) or not path.is_file():
        return None
    data = path.read_bytes()
    if sha(data) != local_sha:
        return None
    parts = Path(name).parts
    if not parts or len(path.parents) < len(parts):
        return None
    root = path.parents[len(parts) - 1]
    result = normalized(name, data, local_prefix, local_library, author_paths,
                        wheel_hashes, record_hashes, root, author_shebangs)
    if result is None:
        return None
    rule, digest = result
    if rule == 'wheel-generator':
        return rule if wheel_hashes.get(name) == digest else None
    wanted = (record_hashes or {}).get(name, expected_sha) if rule == 'record-requested' else expected_sha
    return rule if digest == wanted else None
