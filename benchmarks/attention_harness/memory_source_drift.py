"""Freeze a current source lock without changing any original evidence."""
import copy,hashlib,json
from pathlib import Path
from openarm365_dev_v2.source_policy import dev_tier,source_file,drift_record
from .dev_v2_loop import checked,immutable


def current_identity(config, lock_ref, directory):
    origin=json.loads(checked(dict(uri=lock_ref.get('path',lock_ref.get('uri')),sha256=lock_ref['sha256'])).read_bytes())
    if not dev_tier(config):return lock_ref,None
    current=current_source_lock(origin)
    if current==origin:return lock_ref,None
    return _seal(config,origin,current,lock_ref,Path(directory))


def _seal(config,origin,current,lock_ref,directory):
    token=hashlib.sha256(json.dumps(current,sort_keys=True).encode()).hexdigest()
    ref=immutable(directory/('current-lock-'+token+'.json'),current)
    current_ref=dict(path=ref['uri'],sha256=ref['sha256'])
    record=drift_record(config,origin,current,lock_ref['sha256'],ref['sha256'])
    from openarm365_dev_v2.dev_config import verify_lock
    verify_lock(current_ref,expected_source_root=current['source_root'])
    return current_ref,record


def execution_config(config, current_ref, record):
    if record is None:return config
    return dict(config,dev_lock=current_ref,origin_dev_lock=config['dev_lock'])


def current_source_lock(origin):
    """Refresh code only; every other resource digest stays frozen."""
    current=copy.deepcopy(origin)
    for folder in origin['source_sets']:
        names=sorted(str(p.absolute()) for p in Path(folder).rglob('*.py') if '__pycache__' not in p.parts)
        current['source_sets'][folder]=names
        for name in set(origin['source_sets'][folder])-set(names):
            current['files'].pop(name,None)
        for name in names:
            if source_file(origin,name):current['files'][name]=hashlib.sha256(Path(name).read_bytes()).hexdigest()
    harness=Path(origin['source_root'])/'benchmarks/attention_harness'
    for name in list(harness.glob('dev*.py'))+list((harness/'dev_ui').glob('*')):
        if name.is_file() and source_file(origin,str(name)):current['files'][str(name)]=hashlib.sha256(name.read_bytes()).hexdigest()
    for name in list(current['files']):
        if source_file(origin,name):
            if not Path(name).exists():current['files'].pop(name)
            else:current['files'][name]=hashlib.sha256(Path(name).read_bytes()).hexdigest()
    return current
