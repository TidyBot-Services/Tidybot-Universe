"""Seal only original locked files and explicit link hops; never relocate evidence."""
import hashlib,json,os,stat
from pathlib import Path
from .core.dev_store import DevConflict
from .dev_v2_loop import checked


def locked_chain(path):
    """Return lexical one-hop paths, retaining the historical alias."""
    chain=[]
    while True:
        if (not path.is_absolute() or '..' in path.parts or path.parent.resolve()!=path.parent
                or any(p.is_symlink() for p in path.parents)):
            raise DevConflict('canonical original source parent required: '+str(path))
        if path in chain:raise DevConflict('source link cycle')
        chain.append(path)
        mode=path.lstat().st_mode
        if stat.S_ISREG(mode):return chain
        if not stat.S_ISLNK(mode):raise DevConflict('source chain must end at a regular file')
        if len(chain)>8:raise DevConflict('source chain exceeds eight hops')
        raw=os.readlink(path)
        path=Path(os.path.normpath(raw if os.path.isabs(raw) else str(path.parent/raw)))


def file_digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def verify_source_link(catalogs,cid,name,expected):
    """Check supplied source pins before export/provisioning; service checks again."""
    catalog=catalogs[cid];path=Path(catalog['root'])/name
    paths=locked_chain(path)
    for index,path in enumerate(paths):
        root=Path(catalog['root']);relative=Path(name)
        if (catalog['role']!='source' or not root.is_absolute() or root.resolve()!=root
                or relative.is_absolute() or '..' in relative.parts or root/relative!=path
                or catalog['files'].get(name)!=expected):raise DevConflict('source link target must be sealed at exact digest')
        pin=catalog.get('links',{}).get(name)
        if index==len(paths)-1:
            if pin is not None:raise DevConflict('declared source link must be a symlink')
            break
        if (not isinstance(pin,dict) or set(pin)!={'target','target_catalog','target_name'}
                or pin['target']!=str(paths[index+1]) or name in catalog.get('symlinks',{})):
            raise DevConflict('source link hop differs from pin')
        catalog=catalogs.get(pin['target_catalog'])
        if catalog is None:raise DevConflict('source link target catalog missing')
        name=pin['target_name']
    if file_digest(paths[-1])!=expected:raise DevConflict('source evidence digest drift: '+str(path))


def registration_order(catalogs):
    """Targets first, including after merging several independently built inventories."""
    ordered={};active=set()
    def visit(cid):
        if cid in ordered:return
        if cid in active:raise DevConflict('source catalog dependency cycle')
        if cid not in catalogs:raise DevConflict('source link target catalog missing')
        active.add(cid)
        for pin in catalogs[cid].get('links',{}).values():
            if pin['target_catalog']!=cid:visit(pin['target_catalog'])
        active.remove(cid);ordered[cid]=catalogs[cid]
    for cid in catalogs:visit(cid)
    return ordered


def source_catalogs(lock_ref,context,executor_root,*,contract=None):
    lock=json.loads(checked(dict(uri=lock_ref.get('uri',lock_ref.get('path')),sha256=lock_ref['sha256'])).read_bytes())
    if lock.get('schema')!='attentionbench.dev-identity-lock.v2' or lock.get('formal_eligible') is not False or not lock.get('source_sets'):raise DevConflict('complete original identity lock required')
    executor=Path(executor_root);files=dict(lock['files']);roots=[];link_roots=set();links={}
    for folder,expected in lock['source_sets'].items():
        p=Path(folder)
        if not p.is_absolute() or p.resolve()!=p or not p.is_dir():raise DevConflict('canonical SDK source directory required')
        actual=sorted(str(f) for f in p.rglob('*.py') if '__pycache__' not in f.parts)
        if actual!=expected or any(name not in files for name in expected):raise DevConflict('SDK source file set drift')
        if not p.is_relative_to(executor):roots.append(p)
    for name,expected in lock['files'].items():
        p=Path(name);chain=locked_chain(p)
        if str(p)!=name:raise DevConflict('exact original source file path required: '+name)
        if file_digest(chain[-1])!=expected:raise DevConflict('source evidence digest drift: '+name)
        if len(chain)>1:
            if any(q.is_relative_to(executor) for q in chain):raise DevConflict('source links cannot overlap executor root')
            for q in chain:
                if str(q) in files and files[str(q)]!=expected:raise DevConflict('source link digest differs from identity lock')
                files[str(q)]=expected;link_roots.add(q.parent)
            links.update(zip(chain,chain[1:]))
        elif not p.is_relative_to(executor) and not any(p.is_relative_to(r) for r in roots):roots.append(p.parent)
    # Keep complete SDK sets together and each link-chain directory explicit.
    roots=sorted({r for r in roots if not any(r!=other and r.is_relative_to(other) for other in roots)}|link_roots)
    by_root={}
    for root in roots:
        catalog=dict(role='source',root=str(root),context=context,files={str(Path(n).relative_to(root)):s for n,s in files.items() if Path(n).is_relative_to(root)})
        if contract and Path(contract['uri']).is_relative_to(root):
            p=Path(contract['uri'])
            if p.resolve()!=p:raise DevConflict('canonical scene contract required')
            checked(contract)
            name=str(p.relative_to(root))
            if name in catalog['files'] and catalog['files'][name]!=contract['sha256']:raise DevConflict('scene contract differs from identity lock')
            catalog['files'][name]=contract['sha256']
        pins={str(p.relative_to(root)):dict(target=str(target),target_root=str(target.parent),target_name=target.name) for p,target in links.items() if p.is_relative_to(root)}
        if pins:catalog['links']=pins
        by_root[root]=catalog
    # Hash symbolic destination roots first: self-directory links cannot hash an
    # ID containing itself. The inventory digest binds every dependent payload.
    inventory=hashlib.sha256(json.dumps(list(by_root.values()),sort_keys=True).encode()).hexdigest()
    ids={root:'source-'+hashlib.sha256(json.dumps(dict(catalog=catalog,inventory=inventory) if catalog.get('links') else catalog,sort_keys=True).encode()).hexdigest() for root,catalog in by_root.items()}
    for catalog in by_root.values():
        for pin in catalog.get('links',{}).values():pin['target_catalog']=ids[Path(pin.pop('target_root'))]
    catalogs=registration_order({ids[root]:catalog for root,catalog in by_root.items()})
    for cid,catalog in catalogs.items():
        for name in catalog.get('links',{}):verify_source_link(catalogs,cid,name,catalog['files'][name])
    return catalogs


def covers(catalog,ref):
    p=Path(ref['uri']);root=Path(catalog['root'])
    return p.is_absolute() and p.is_relative_to(root) and catalog['files'].get(str(p.relative_to(root)))==ref['sha256']


def manifest_sources(manifest_ref,context,executor_root,*,contract=None):
    manifest=json.loads(checked(manifest_ref).read_bytes())
    return source_catalogs(manifest['identity_lock'],context,executor_root,contract=contract)
