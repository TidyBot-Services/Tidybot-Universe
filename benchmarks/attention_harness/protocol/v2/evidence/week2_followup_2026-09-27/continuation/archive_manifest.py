"""Build or verify a path-relative SHA-256 manifest for this archive."""
import hashlib
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
manifest = root / 'archive_manifest.json'
actual = {
    path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(root.rglob('*'))
    if path.is_file() and path != manifest and '__pycache__' not in path.parts
}
if len(sys.argv) > 1 and sys.argv[1] == 'verify':
    expected = json.loads(manifest.read_text())['files']
    assert actual == expected, {'missing': sorted(set(expected) - set(actual)),
                                'extra': sorted(set(actual) - set(expected)),
                                'changed': sorted(p for p in expected.keys() & actual.keys()
                                                  if expected[p] != actual[p])}
    print(json.dumps({'verified': True, 'files': len(actual)}))
else:
    assert not manifest.exists()
    manifest.write_text(json.dumps({'schema_version': 'attentionbench.portable-archive.v1',
                                    'formal_eligible': False, 'files': actual},
                                   indent=2, sort_keys=True) + '\n')
    print(json.dumps({'files': len(actual),
                      'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}))
