"""Apply edit A to base_link_z spheres: drop the top layer at z=0.5.

Reads the original yaml from curobo_service_v0_8/.../franka_tidyverse_mesh.yml,
removes any sphere on `base_link_z` whose center z >= threshold, writes to
the eval/assets copy. Reports what was changed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(Path("/home/truares/文档/curobo_service_v0_8")
                                          / "curobo_service/assets/spheres/franka_tidyverse_mesh.yml"))
    ap.add_argument("--dst", default=str(Path(__file__).parent.parent
                                          / "assets/spheres/franka_tidyverse_mesh.yml"))
    ap.add_argument("--link", default="base_link_z",
                    help="link to edit")
    ap.add_argument("--mode", choices=["drop", "scale"], default="drop",
                    help="drop = remove spheres above z; "
                         "scale = keep them but multiply their radius")
    ap.add_argument("--threshold-z", type=float, default=0.4,
                    help="apply edit to spheres with center z >= this (m)")
    ap.add_argument("--scale-factor", type=float, default=0.5,
                    help="(scale mode) multiply radius of above-threshold spheres")
    # Back-compat aliases used in earlier runs.
    ap.add_argument("--drop-above-z", type=float, default=None,
                    help="(alias) sets mode=drop and threshold-z=this")
    ap.add_argument("--scale-radius", type=float, default=None,
                    help="(alias) sets mode=scale and scale-factor=this")
    args = ap.parse_args()

    src = Path(args.src)
    dst = Path(args.dst)
    with src.open() as f:
        data = yaml.safe_load(f)

    spheres = data.get("collision_spheres", {})
    if args.link not in spheres:
        print(f"link '{args.link}' not in source", file=sys.stderr)
        return 2

    # Resolve back-compat aliases
    mode = args.mode
    threshold_z = args.threshold_z
    scale_factor = args.scale_factor
    if args.drop_above_z is not None:
        mode = "drop"
        threshold_z = args.drop_above_z
    if args.scale_radius is not None:
        mode = "scale"
        scale_factor = args.scale_radius

    orig = list(spheres[args.link])
    kept: list[dict] = []
    affected: list[dict] = []
    for s in orig:
        z = s["center"][2]
        if z >= threshold_z:
            if mode == "drop":
                affected.append(s)
            else:  # scale
                old_r = s["radius"]
                s = dict(s)  # don't mutate the read-back ref
                s["radius"] = round(old_r * scale_factor, 4)
                kept.append(s)
                affected.append({"center": s["center"], "old_r": old_r, "new_r": s["radius"]})
        else:
            kept.append(s)

    spheres[args.link] = kept
    data["collision_spheres"] = spheres

    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)

    print(f"src: {src}")
    print(f"dst: {dst}")
    print(f"link '{args.link}':  mode={mode}  threshold_z={threshold_z}"
          + (f"  scale_factor={scale_factor}" if mode == "scale" else ""))
    print(f"  original: {len(orig)} spheres")
    if mode == "drop":
        print(f"  dropped (z >= {threshold_z}): {len(affected)}")
        print(f"  kept:     {len(kept)}")
        if affected:
            zs = sorted({round(s['center'][2], 3) for s in affected})
            rs = sorted({round(s['radius'], 3) for s in affected})
            print(f"  dropped z values: {zs}")
            print(f"  dropped radii:    {rs}")
    else:
        print(f"  scaled (z >= {threshold_z}): {len(affected)}  (×{scale_factor})")
        print(f"  unchanged: {len(kept) - len(affected)}")
        if affected:
            example = affected[0]
            print(f"  example: r {example['old_r']} → {example['new_r']}")


if __name__ == "__main__":
    sys.exit(main())
