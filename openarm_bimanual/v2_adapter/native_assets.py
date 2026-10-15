"""Verify the separate immutable native data baseline before any scene import."""
from pathlib import Path
import hashlib,json,os,time
from .identity import IdentityError,digest

def asset_identity(lock):
    data=lock.get('native_assets',{})
    required={'manifest','manifest_sha256','env_root','data_root','file_count','bytes'}
    if not required<=data.keys():raise IdentityError('Native data manifest is not locked')
    if digest(Path(data['manifest']))!=data['manifest_sha256']:
        raise IdentityError('Native data manifest changed')
    return dict(manifest_sha256=data['manifest_sha256'],env_root=data['env_root'],data_root=data['data_root'],
                file_count=data['file_count'],bytes=data['bytes'],full_verified=True)

def verify_assets(lock):
    started=time.monotonic();identity=asset_identity(lock);manifest=json.loads(Path(lock['native_assets']['manifest']).read_text())
    root=Path(identity['data_root']);env=Path(identity['env_root'])
    if(root!=env/'data'or not root.is_absolute()or root.is_symlink()or env.is_symlink()
            or manifest.get('data_root')!=str(root)or manifest.get('asset_env_root')!=str(env)
            or manifest.get('count')!=identity['file_count']or manifest.get('bytes')!=identity['bytes']):
        raise IdentityError('Native data baseline paths/counts differ from lock')
    expected={};total=0
    for row in manifest['files']:
        path=Path(row['path'])
        if(path!=root/row['relative']or not path.is_relative_to(root)or path.is_symlink()
                or not path.is_file()or path.stat().st_mode&0o222 or path.stat().st_size!=row['bytes']):
            raise IdentityError('Native asset is missing, writable, or relocated: '+str(path))
        if str(path)in expected:raise IdentityError('Duplicate native asset in manifest')
        with path.open('rb')as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual!=row['sha256']:raise IdentityError('Native asset bytes changed: '+str(path))
        expected[str(path)]=actual;total+=row['bytes']
    actual_paths=set()
    for path in [env,root,*root.rglob('*')]:
        if path.is_symlink()or path.stat().st_mode&0o222:raise IdentityError('Native baseline lost its read-only seal: '+str(path))
        if path.is_file():actual_paths.add(str(path))
    if(actual_paths!=set(expected)or len(expected)!=identity['file_count']or total!=identity['bytes']):
        raise IdentityError('Native baseline has unlisted files or incomplete coverage')
    return dict(result='PASS',**identity,elapsed_seconds=time.monotonic()-started)

def verify_data_environment(lock):
    identity=asset_identity(lock)
    if os.environ.get('MS_ASSET_DIR')!=identity['env_root']:
        raise IdentityError('Native data environment differs from the explicit locked root')
    import mani_skill
    module=Path(mani_skill.__file__).resolve()
    if(not module.is_relative_to(Path(lock['prefix']))or lock['files'].get(str(module))!=digest(module)
            or Path(mani_skill.ASSET_DIR).resolve()!=Path(identity['data_root'])):
        raise IdentityError('Actual ManiSkill data/package path differs from lock')
    return identity
