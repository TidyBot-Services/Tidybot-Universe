"""Seal only the identity lock's original files; never relocate historical evidence."""
import hashlib,json
from pathlib import Path
from .core.dev_store import DevConflict
from .dev_v2_loop import checked


def source_catalogs(lock_ref,context,executor_root,*,contract=None):
    lock=json.loads(checked(dict(uri=lock_ref.get('uri',lock_ref.get('path')),sha256=lock_ref['sha256'])).read_bytes())
    if lock.get('schema')!='attentionbench.dev-identity-lock.v2' or lock.get('formal_eligible') is not False or not lock.get('source_sets'):raise DevConflict('complete original identity lock required')
    executor=Path(executor_root);files=lock['files'];roots=[]
    for folder,expected in lock['source_sets'].items():
        p=Path(folder)
        if not p.is_absolute() or p.resolve()!=p or not p.is_dir():raise DevConflict('canonical SDK source directory required')
        actual=sorted(str(f) for f in p.rglob('*.py') if '__pycache__' not in f.parts)
        if actual!=expected or any(name not in files for name in expected):raise DevConflict('SDK source file set drift')
        if not p.is_relative_to(executor):roots.append(p)
    for name,expected in files.items():
        p=Path(name)
        if not p.is_absolute() or p.resolve()!=p:raise DevConflict('canonical original source file required: '+name)
        h=hashlib.sha256()
        with p.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
        if h.hexdigest()!=expected:raise DevConflict('source evidence digest drift: '+name)
        if not p.is_relative_to(executor) and not any(p.is_relative_to(r) for r in roots):roots.append(p.parent)
    # A source set stays together; only exact locked files are sealed, even if
    # several sets share a parent. No recursive content or credential discovery.
    roots=sorted({r for r in roots if not any(r!=other and r.is_relative_to(other) for other in roots)})
    catalogs={}
    for root in roots:
        catalog=dict(role='source',root=str(root),context=context,files={str(Path(n).relative_to(root)):s for n,s in files.items() if Path(n).is_relative_to(root)})
        if contract and Path(contract['uri']).is_relative_to(root):
            p=Path(contract['uri'])
            if p.resolve()!=p:raise DevConflict('canonical scene contract required')
            checked(contract)
            name=str(p.relative_to(root))
            if name in catalog['files'] and catalog['files'][name]!=contract['sha256']:raise DevConflict('scene contract differs from identity lock')
            catalog['files'][name]=contract['sha256']
        cid='source-'+hashlib.sha256(json.dumps(catalog,sort_keys=True).encode()).hexdigest()
        catalogs[cid]=catalog
    return catalogs


def covers(catalog,ref):
    p=Path(ref['uri']);root=Path(catalog['root'])
    return p.is_absolute() and p.is_relative_to(root) and catalog['files'].get(str(p.relative_to(root)))==ref['sha256']


def manifest_sources(manifest_ref,context,executor_root,*,contract=None):
    manifest=json.loads(checked(manifest_ref).read_bytes())
    return source_catalogs(manifest['identity_lock'],context,executor_root,contract=contract)
