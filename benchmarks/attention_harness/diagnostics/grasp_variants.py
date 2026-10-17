"""Serial seed 101–103 grasp variants; --dry-run never steps the simulator."""
from __future__ import annotations
import argparse, contextlib, io, json, math, os, subprocess, sys
from pathlib import Path
from . import grasp_chain_probe

FIELDS={'theta','hold','offset','z','lift','height'}

def parse_variants(spec):
    result=[];names=set()
    for item in spec.split(';'):
        if not item.strip():continue
        if ':' not in item:raise ValueError('variant requires name:key=value')
        name,raw=item.split(':',1)
        if not name or not all(c.isalnum() or c in '_-' for c in name) or name in names:
            raise ValueError('variant names must be unique letters/digits/_/-')
        values={}
        for pair in raw.split(','):
            if '=' not in pair:raise ValueError('variant parameter requires key=value')
            key,value=pair.split('=',1)
            if key not in FIELDS or key in values:raise ValueError(f'unknown/duplicate variant parameter: {key}')
            number=float(value)
            if not math.isfinite(number):raise ValueError('variant parameters must be finite')
            values[key]=number
        if 'theta' not in values:raise ValueError('each variant requires theta')
        values.setdefault('hold',0.)
        if values['hold']<0 or not values['hold'].is_integer():raise ValueError('hold must be a nonnegative integer')
        if values['hold']:raise ValueError('nonzero hold unavailable: dev SDK has no exact settle/hold operation')
        result.append((name,values));names.add(name)
    if not result:raise ValueError('no variants')
    return result

def format_table(rows):
    lines=['| 变体 | 上升 cm | 最大滑动 mm | 监控中止 | 原因 | 闭爪后两指开度 m |',
           '| --- | ---: | ---: | --- | --- | --- |']
    for row in rows:
        show=lambda key: '—' if row.get(key) is None else str(row[key])
        reason=show('aborted_by').replace('|','\\|').replace('\n',' ')
        lines.append(f"| {row['variant']} | {show('object_lift_cm')} | {show('max_slip_mm')} | {show('monitor_aborted')} | {reason} | {show('finger_openings_after_close_m')} |")
    return '\n'.join(lines)+'\n'

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--variants',required=True)
    p.add_argument('--only',help='comma-separated variant names')
    p.add_argument('--out',type=Path,default=Path('grasp-variants-results'))
    p.add_argument('--seed',type=int,choices=(101,102,103),default=102)
    p.add_argument('--base-shift',default='0,0.25,0')
    p.add_argument('--sequence',choices=('rotate-first','translate-first','combined'),default='translate-first')
    p.add_argument('--rotation-step',type=float,default=.15)
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--held-reference-mode',choices=('fixed','settled'))
    args=p.parse_args(argv)
    try:
        variants=parse_variants(args.variants)
        if args.only:
            selected=set(args.only.split(','))
            if selected-{name for name,_ in variants}:raise ValueError('--only names absent from --variants')
            variants=[item for item in variants if item[0] in selected]
        if args.out.exists():raise ValueError('--out already exists; choose a fresh directory')
    except ValueError as exc:p.error(str(exc))
    args.out.mkdir(parents=True)
    rows=[]
    for name,values in variants:
        command=['--seed',str(args.seed),'--base-shift',args.base_shift,'--exempt','movable_objects',
                 '--sequence',args.sequence,'--rotation-step',str(args.rotation_step),
                 '--theta-deg',str(values['theta']),'--oracle-from-reset',
                 '--grasp-offset-mm',str(values.get('offset',0.)),
                 '--grasp-z-offset-mm',str(values.get('z',0.)),
                 '--close-hold-steps',str(int(values['hold'])),
                 '--lift-step-m',str(values.get('lift',.025)),
                 '--lift-height-m',str(values.get('height',.10)),
                 '--out',str(args.out/name)]
        if args.held_reference_mode is not None:
            command.extend(['--held-reference-mode',args.held_reference_mode])
        if args.dry_run:
            output=io.StringIO()
            with contextlib.redirect_stdout(output):grasp_chain_probe.main(command+['--dry-run'])
            (args.out/f'{name}-policy.py').write_text(output.getvalue())
            row=dict(variant=name,dry_run=True,command=command,object_lift_cm=None,
                     max_slip_mm=None,monitor_aborted=None,aborted_by=None,
                     finger_openings_after_close_m=None)
        else:
            env=os.environ.copy()
            env.update(ATTN_PLANNER_MODE='ik_path',ATTN_IK_CANDIDATES='8',
                       ATTN_IK_MAX_JOINT_JUMP='1.0',ATTN_DIAG_HELD_TRACE='1')
            completed=subprocess.run([sys.executable,'-m',
                'benchmarks.attention_harness.diagnostics.grasp_chain_probe',*command],env=env,
                capture_output=True,text=True,check=False)
            result_path=args.out/name/'results.json'
            result=json.loads(result_path.read_text()) if result_path.is_file() else {}
            row=dict(variant=name,parameters=values,returncode=completed.returncode,
                     object_lift_cm=result.get('object_lift_cm'),max_slip_mm=result.get('max_slip_mm'),
                     monitor_aborted=result.get('monitor_aborted'),
                     aborted_by=result.get('aborted_by') or completed.stderr[-1000:] or None,
                     finger_openings_after_close_m=result.get('finger_openings_after_close_m'))
        rows.append(row)
        (args.out/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
        (args.out/'results.md').write_text('# 抓取变体矩阵\n\n'+format_table(rows))
    print(format_table(rows),end='')
    return 0

if __name__=='__main__':raise SystemExit(main())
