"""Prepare a new explicit V2 launch binding; never grants formal eligibility."""
from pathlib import Path
import argparse,json
from benchmarks.attention_harness.robocasa_native.openarm_config import SCHEMA,ROOT,_REQUIRED_SOURCE,digest,validate_config
from curobo_v2.deployment import deployment_contract


def prepare(reference):
    value=json.loads(Path(reference).read_bytes())
    if value.get('suite')!='robocasa' or value.get('robot',{}).get('model')!='OpenArm':
        raise ValueError('This adapter only declares the fixed native RoboCasa OpenArm branch')
    value.pop('task_source',None);value.pop('sim_runtime',None)
    value['schema_version']=SCHEMA;value['v2_deployment']=deployment_contract()
    value['service_files']={name:digest(ROOT/name) for name in sorted(_REQUIRED_SOURCE)}
    class Selection:pass
    selection=Selection();selection.task_id=value['task_id'];selection.seed=value['seed']
    validate_config(selection,value)
    return value


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    args=p.parse_args();value=prepare(args.reference)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as f:f.write(json.dumps(value,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'config':str(args.output.resolve()),'sha256':digest(args.output),'formal_eligible':False}))
if __name__=='__main__':main()
