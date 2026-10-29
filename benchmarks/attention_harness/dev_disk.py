"""Shared Dev v2 admission and graceful-pause disk checks (decimal GB)."""
import math,os,shutil,time
from pathlib import Path
from .dev_configuration import DISK_DEFAULTS
from .core.dev_store import DevConflict

def thresholds():
    result={k:float(os.environ.get('ATTENTION_DEV_V2_'+k.upper(),v)) for k,v in DISK_DEFAULTS.items()}
    if any(not math.isfinite(v) or v<=0 for v in result.values()) or result['start_min_free_gb']<result['run_min_free_gb']:
        raise ValueError('invalid Dev v2 disk thresholds')
    return result

def status(root):
    path=Path(root).resolve()
    while not path.exists():path=path.parent
    usage=shutil.disk_usage(path)
    return dict(thresholds(),free_gb=usage.free/1e9,used_percent=100*usage.used/usage.total)

def require_start(root):
    row=status(root)
    if row['free_gb']<row['start_min_free_gb']:
        raise DevConflict(f"磁盘剩余 {row['free_gb']:.1f} GB，低于 {row['start_min_free_gb']:g} GB，未启动")
    return row

def experiment_root(exp):
    return exp.get('disk_root') or exp['artifact_dir']

def pause_if_low(store,exp_id):
    with store.tx() as db:exp=store.get(db,'experiments',exp_id)
    row=status(experiment_root(exp))
    if row['free_gb']>=row['run_min_free_gb']:return False
    detail=f"磁盘剩余 {row['free_gb']:.1f} GB，低于 {row['run_min_free_gb']:g} GB，磁盘不足，已暂停"
    with store.tx() as db:
        exp=store.get(db,'experiments',exp_id)
        if exp.get('cancelled'):return False
        exp.setdefault('pause_requested_at',time.time())
        if exp.get('pause_reason')!='disk_low':
            store.event(db,'experiment_pause_requested',dict(experiment_id=exp_id,reason='disk_low',detail=detail,**row))
        exp.update(pause_reason='disk_low',pause_detail=detail)
        store.put(db,'experiments',exp_id,exp)
    return True
