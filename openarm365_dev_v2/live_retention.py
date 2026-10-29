"""Best-effort retention for the display-only execution preview."""
import json,logging,re
from pathlib import Path

LOG=logging.getLogger(__name__)

def prune_live(evidence):
    live=Path(evidence)/'live';result=dict(kept=[],deleted=0,errors=[])
    try:
        if not live.exists():return result
        frames=sorted(p for p in live.glob('frame-*.jpg') if re.fullmatch(r'frame-\d+\.jpg',p.name))
        keep=set(frames[-3:])
        if (live/'latest.json').exists():
            name=json.loads((live/'latest.json').read_bytes())['file']
            if not isinstance(name,str) or not re.fullmatch(r'frame-\d+\.jpg',name):raise ValueError('invalid latest frame')
            if live/name not in keep:
                keep=set(frames[-2:])|{live/name}
        result['kept']=sorted(p.name for p in keep)
        for frame in frames:
            if frame in keep:continue
            try:frame.unlink();result['deleted']+=1
            except OSError as exc:result['errors'].append(f'{frame.name}: {type(exc).__name__}: {exc}')
    except Exception as exc:result['errors'].append(f'{type(exc).__name__}: {exc}')
    try:
        path=Path(evidence)/'live-pruning.json'
        previous=json.loads(path.read_bytes()) if path.exists() else {}
        result['errors']=list(dict.fromkeys(previous.get('errors',[])+result['errors']))
        path.write_text(json.dumps(result,indent=2)+'\n')
    except Exception as exc:result['errors'].append(f'diagnostic: {type(exc).__name__}: {exc}')
    if result['errors']:LOG.warning('Live frame pruning failed in %s: %s',evidence,'; '.join(result['errors']))
    return result

def close_execution(evidence,executor,planner,env,cleanup):
    try:
        try:
            if executor:cleanup['planner']=executor.close()
            elif planner:cleanup['planner']=planner.close()
        finally:
            if env:env.close()
    finally:cleanup['live_pruning']=prune_live(evidence)
