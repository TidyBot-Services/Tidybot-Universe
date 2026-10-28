#!/usr/bin/env python3
"""Tabulate cabinet probe JSON; no simulator imports."""
import argparse
import json
from pathlib import Path


def summarize(folder):
    lines = ['| Seed | Target/category | AABB size m | Min width mm / fits 80.5 | Cabinet/type | Interior AABB m | Door state | Mount→object XY/ΔZ m | Mount→interior XY/ΔZ m | Same resets |',
             '|---|---|---|---|---|---|---|---|---|---|']
    def fmt(value):
        if isinstance(value, float):return round(value, 4)
        if isinstance(value, list):return [fmt(x) for x in value]
        if isinstance(value, dict):return {k: fmt(v) for k,v in value.items()}
        return value
    for path in sorted(Path(folder).glob('seed*/probe.json')):
        row = json.loads(path.read_text()); scene = row['resets'][0]['scene']
        t, c = scene['target'], scene['cabinet']
        values = [row['seed'], f"{t['public_name']}/{t['category']}", t['aabb_size_m'],
                  f"{t['narrowest_graspable_width_m']*1000:.2f} / {t['fits_80_5mm']}",
                  f"{c['fixture_name']} ({c['fixture_class']}, {c['type']})",
                  {k:v['aabb_world_m'] for k,v in c['interior_regions'].items()}, c['door_state'],
                  scene['mount_to_object'], scene['mount_to_cabinet_interiors'], row['same_normalized_public_state']]
        lines.append('| '+' | '.join(str(fmt(v)).replace('|', '/') for v in values)+' |')
    if len(lines)==2:raise ValueError('no seed*/probe.json found')
    return '\n'.join(lines)+'\n'


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--probe-dir', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args=p.parse_args(argv)
    args.out.write_text(summarize(args.probe_dir))


if __name__=='__main__':main()
