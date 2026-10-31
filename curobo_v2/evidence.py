"""Preserve the exact runtime lock and mutable project source for each owner."""
import hashlib
import json
from pathlib import Path
import zipfile
from .identity import IdentityError,digest


def preserve_runtime(lock_path,lock,directory):
    directory=Path(directory);raw=Path(lock_path).read_bytes()
    if json.loads(raw)!={k: v for k, v in lock.items() if k != 'trust_source'}:raise IdentityError('Runtime lock changed before evidence capture')
    (directory/'runtime-lock.json').write_bytes(raw)
    root=Path(lock['project_root'])
    archive=directory/'runtime-project-source.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED)as output:
        for name,expected in sorted(lock['files'].items()):
            path=Path(name)
            if path.is_relative_to(root)and path.suffix in {'.py','.yml','.yaml','.json','.sha256'}:
                data=path.read_bytes()
                if hashlib.sha256(data).hexdigest()!=expected:
                    raise IdentityError('Source changed during runtime evidence capture: '+name)
                output.writestr(str(path.relative_to(root)),data)
    labels = ({'trust_source': 'local-dev-registry', 'formal_eligible': False,
               'deployment_kind': 'dev-local'} if lock.get('trust_source') == 'local-dev-registry' else {})
    (directory/'runtime-evidence.json').write_text(json.dumps(dict(
        lock_sha256=hashlib.sha256(raw).hexdigest(),project_source_sha256=digest(archive),
        project_root=str(root),library_revision=lock['library_revision'],
        assets='Complete immutable assets and dependencies bound by runtime-lock.json; project Python/config source archived',
        **labels),indent=2)+'\n')
