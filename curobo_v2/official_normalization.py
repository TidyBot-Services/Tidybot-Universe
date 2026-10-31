"""Narrow, byte-checked portability rules for the official environment."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path


GENERATOR = re.compile(rb'Generator: setuptools \([0-9]+\.[0-9]+\.[0-9]+\)\n')
EMPTY_REQUESTED = b',sha256=47DEQpj8HBSa-_TImW-5JCeuQeRkm5NMpJWZG3hSuFU,0'


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


def normalized(name: str, data: bytes, local_prefix: str, local_library: str,
               author_paths: dict, wheel_hashes: dict) -> tuple[str, str] | None:
    """Return (rule, normalized digest), or reject an inapplicable/invalid rule."""
    rule = rule_for(name)
    if rule == 'record-requested':
        package = Path(name).parent.name.encode()
        line = package + b'/REQUESTED' + EMPTY_REQUESTED
        lines = data.splitlines(keepends=True)
        matches = [i for i, item in enumerate(lines)
                   if item in (line + b'\n', line + b'\r\n')]
        if len(matches) != 1:
            return None
        del lines[matches[0]]
        return rule, sha(b''.join(lines))
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
            wheel_hashes: dict) -> str | None:
    """Return the applied rule only after binding the bytes to the local lock."""
    if not isinstance(local_sha, str) or not isinstance(expected_sha, str) or not path.is_file():
        return None
    data = path.read_bytes()
    if sha(data) != local_sha:
        return None
    result = normalized(name, data, local_prefix, local_library, author_paths, wheel_hashes)
    if result is None:
        return None
    rule, digest = result
    if rule == 'wheel-generator':
        return rule if wheel_hashes.get(name) == digest else None
    return rule if digest == expected_sha else None
