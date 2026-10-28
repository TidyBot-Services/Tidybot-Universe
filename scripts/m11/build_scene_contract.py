#!/usr/bin/env python3
"""Build an UNREGISTERED seed-keyed cabinet contract from N fresh processes × 2 resets."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.m11_cabinet_probe import TASK, parse_seeds, run_process, write_json, check_environment
from openarm365_dev_v2.scene_contract import SCHEMA_V2, fingerprint, REGISTERED_CONTRACT


def build(folder, seeds, processes):
    if type(processes) is not int or processes < 2:
        raise ValueError('at least two independent processes required')
    entries={}
    evidence=[]
    for seed in seeds:
        normalized=set(); raw=set(); pids=set(); settings=set()
        for index in range(processes):
            path=Path(folder)/f'seed{seed}'/f'process-{index+1:03d}'/'probe.json'
            value=json.loads(path.read_text())
            receipt=json.loads(path.with_name('receipt.json').read_text())
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            pid=value.get('pid')
            if (type(pid) is not int or pid<=0 or pid in pids or receipt.get('pid')!=pid
                    or receipt.get('returncode')!=0 or receipt.get('probe_sha256')!=digest):
                raise ValueError('independent process receipt mismatch: '+str(path))
            pids.add(pid)
            if (value.get('task_id')!=TASK or value.get('seed')!=seed
                    or value.get('scene_reset_seed')!=seed or value.get('layout_style')!=[11,34]
                    or value.get('motion_calls')!=0 or value.get('planner_calls')!=0
                    or value.get('same_normalized_public_state') is not True):
                raise ValueError('probe scene/reset metadata mismatch')
            profile=value['execution_settings']
            if profile['profile']!='r4-20261027':raise ValueError('r4 required')
            settings.add(json.dumps(profile,sort_keys=True))
            rows=value['resets']
            if len(rows)!=2 or [r['reset_index'] for r in rows]!=[0,1]:
                raise ValueError('exactly two ordered resets required')
            for row in rows:
                if (set(row['kp'])!={'left','right'} or
                    any(not v or any(k!=1500 for k in v) for v in row['kp'].values())):
                    raise ValueError('default kp=1500 required')
                for rounded,key,values in [(True,'normalized_sha256',normalized),(False,'raw_sha256',raw)]:
                    actual=fingerprint(row['state'],rounded)
                    if row[key]!=actual:raise ValueError('state fingerprint mismatch')
                    values.add(actual)
            evidence.append(dict(path=str(path.resolve()),sha256=digest,pid=pid))
        if len(normalized)!=1 or len(settings)!=1:
            raise ValueError(f'unstable normalized state or execution settings for seed {seed}')
        entries[str(seed)]=dict(normalized_sha256=next(iter(normalized)),raw_sha256s=sorted(raw),
                                fresh_processes=processes,resets=2*processes)
    return dict(schema=SCHEMA_V2,tolerance=1e-6,tasks={TASK:dict(seeds=entries)},
        provenance=dict(candidate=True,registered=False,layout_style=[11,34],execution_profile='r4-20261027',
                        hook='scene_reset_seed == seed (canonical region order)',kp=1500,sources=evidence))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seeds',type=parse_seeds,required=True)
    p.add_argument('--probe-dir',type=Path,required=True)
    p.add_argument('--processes',type=int,default=20)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--planner-source',type=Path)
    p.add_argument('--collect',action='store_true',help='run N fresh probe processes per seed first')
    args=p.parse_args(argv)
    if args.processes<2:p.error('--processes must be >=2')
    if (args.out.resolve()==REGISTERED_CONTRACT.resolve() or
            'scene_contracts' in args.out.resolve().parts or args.out.name=='scene_contract.py'):
        p.error('cannot modify registered contracts or registration code')
    if args.out.exists():p.error('refusing to overwrite candidate')
    if args.collect:
        check_environment()
        for seed in args.seeds:
            for i in range(args.processes):
                run_process(seed,args.probe_dir/f'seed{seed}'/f'process-{i+1:03d}',args.planner_source)
    value=build(args.probe_dir,args.seeds,args.processes)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    write_json(args.out,value)
    return 0


if __name__=='__main__':raise SystemExit(main())
