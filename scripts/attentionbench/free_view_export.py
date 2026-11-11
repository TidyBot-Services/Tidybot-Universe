#!/usr/bin/env python3
"""Export one finished execution as a read-only 3D replay bundle (demo, P0).

Display only.  Reads an execution folder, writes three files for the browser
viewer; nothing is sent back to a simulation and no simulation is started.

  scene.json  body ranges, wall list, frame count, notes on what is real
  geom.bin    vertices (pos f32x3, normal i8x3+pad, rgba u8x4) then u32 indices
  poses.bin   f32 [frames, moving bodies, 7] world position + quaternion (wxyz)

What is real and what is reconstructed (also written into scene.json):
  - robot arms, grippers, base: logged joint values of the real run, replayed
    through forward kinematics of the saved scene model;
  - fixture joints (doors, drawers): first and last public observation,
    linear in between;
  - free objects: the run did not log their pose per step.  Position comes
    from the logged find_objects answers; while a gripper is closed on an
    object it is carried rigidly with that hand; orientation is unknown and
    drawn upright.

Usage: free_view_export.py <execution_dir> <out_dir>
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import mujoco
import numpy as np

VISUAL_GROUP = 1
DEFAULT_RGBA = np.array([0.5, 0.5, 0.5, 1.0], dtype=np.float32)


def quat_mul(a, b):
    out = np.zeros(4)
    mujoco.mju_mulQuat(out, np.asarray(a, dtype=float), np.asarray(b, dtype=float))
    return out


def quat_rot(q, v):
    out = np.zeros(3)
    mujoco.mju_rotVecQuat(out, np.asarray(v, dtype=float), np.asarray(q, dtype=float))
    return out


def quat_conj(q):
    return np.array([q[0], -q[1], -q[2], -q[3]])


# ---------------------------------------------------------------- colours
def texture_means(m):
    means = np.ones((m.ntex, 3), dtype=np.float32)
    for t in range(m.ntex):
        n = int(m.tex_width[t]) * int(m.tex_height[t]) * int(m.tex_nchannel[t])
        data = m.tex_data[int(m.tex_adr[t]): int(m.tex_adr[t]) + n]
        data = data.reshape(-1, int(m.tex_nchannel[t]))[::97, :3]
        if len(data):
            # average in linear light, back to display values
            means[t] = (np.mean((data / 255.0) ** 2.2, axis=0)) ** (1 / 2.2)
    return means


def geom_colour(m, g, tex_mean):
    rgba = m.geom_rgba[g].astype(np.float32)
    mat = int(m.geom_matid[g])
    if mat >= 0 and np.allclose(rgba, DEFAULT_RGBA):
        rgba = m.mat_rgba[mat].astype(np.float32).copy()
        tex = int(m.mat_texid[mat, int(mujoco.mjtTextureRole.mjTEXROLE_RGB)])
        if tex >= 0:
            rgba[:3] *= tex_mean[tex]
    return rgba


# ---------------------------------------------------------------- shapes
def box(size):
    sx, sy, sz = size
    v, n, f = [], [], []
    for axis in range(3):
        for sign in (-1, 1):
            a, b = [(1, 2), (2, 0), (0, 1)][axis]
            base = len(v)
            for da, db in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                p = [0, 0, 0]
                p[axis] = sign * (sx, sy, sz)[axis]
                p[a] = da * (sx, sy, sz)[a]
                p[b] = db * (sx, sy, sz)[b]
                v.append(p)
                nn = [0, 0, 0]
                nn[axis] = sign
                n.append(nn)
            f += [[base, base + 1, base + 2], [base, base + 2, base + 3]]
    return np.array(v, np.float32), np.array(n, np.float32), np.array(f, np.uint32)


def cylinder(radius, half, seg=24):
    v, n, f = [], [], []
    ang = np.linspace(0, 2 * np.pi, seg, endpoint=False)
    for z in (-half, half):
        for a in ang:
            v.append([radius * np.cos(a), radius * np.sin(a), z])
            n.append([np.cos(a), np.sin(a), 0])
    for i in range(seg):
        j = (i + 1) % seg
        f += [[i, j, seg + j], [i, seg + j, seg + i]]
    for z, sign in ((-half, -1), (half, 1)):
        base = len(v)
        v.append([0, 0, z])
        n.append([0, 0, sign])
        for a in ang:
            v.append([radius * np.cos(a), radius * np.sin(a), z])
            n.append([0, 0, sign])
        for i in range(seg):
            f.append([base, base + 1 + i, base + 1 + (i + 1) % seg])
    return np.array(v, np.float32), np.array(n, np.float32), np.array(f, np.uint32)


def sphere(radius, seg=16):
    v, f = [], []
    for i in range(seg + 1):
        th = np.pi * i / seg
        for j in range(seg * 2):
            ph = np.pi * j / seg
            v.append([np.sin(th) * np.cos(ph), np.sin(th) * np.sin(ph), np.cos(th)])
    w = seg * 2
    for i in range(seg):
        for j in range(w):
            a, b = i * w + j, i * w + (j + 1) % w
            f += [[a, b, b + w], [a, b + w, a + w]]
    v = np.array(v, np.float32)
    return v * radius, v.copy(), np.array(f, np.uint32)


def mesh(m, mid):
    va, fa, na = int(m.mesh_vertadr[mid]), int(m.mesh_faceadr[mid]), int(m.mesh_normaladr[mid])
    nf = int(m.mesh_facenum[mid])
    fv = m.mesh_face[fa: fa + nf].reshape(-1)
    fn = m.mesh_facenormal[fa: fa + nf].reshape(-1)
    pairs, inv = np.unique(np.stack([fv, fn], axis=1), axis=0, return_inverse=True)
    v = m.mesh_vert[va + pairs[:, 0]]
    n = m.mesh_normal[na + pairs[:, 1]]
    return v.astype(np.float32), n.astype(np.float32), inv.reshape(-1, 3).astype(np.uint32)


def geom_shape(m, g):
    t, size = int(m.geom_type[g]), m.geom_size[g]
    if t == mujoco.mjtGeom.mjGEOM_BOX:
        return box(size)
    if t == mujoco.mjtGeom.mjGEOM_CYLINDER:
        return cylinder(size[0], size[1])
    if t == mujoco.mjtGeom.mjGEOM_SPHERE:
        return sphere(size[0])
    if t == mujoco.mjtGeom.mjGEOM_MESH:
        return mesh(m, int(m.geom_dataid[g]))
    return None


def mat_from(pos, quat):
    r = np.zeros(9)
    mujoco.mju_quat2Mat(r, np.asarray(quat, dtype=float))
    return r.reshape(3, 3), np.asarray(pos, dtype=float)


# ---------------------------------------------------------------- motion
def load_motion(m, exe: Path):
    reset = json.loads((exe / "reset_receipt.json").read_text())
    cond = reset["execution_conditions"]
    to_native = reset["physical_to_native"]
    receipts = [json.loads(line) for line in (exe / "rpc-receipts.jsonl").read_text().splitlines() if line.strip()]
    last_seq = max(int(r["response"].get("sequence", 0)) for r in receipts)
    physics = (exe / "native-physics.jsonl").read_text().splitlines()
    per_seq = max(1, round(len(physics) / max(1, last_seq)))
    frames = last_seq + 1

    def adr(name):
        return int(m.joint(name).qposadr[0])

    arm_adr = [adr("robot0_" + n) for n in cond["joint_names"]]
    base_adr = [adr("mobilebase0_joint_mobile_" + n) for n in ("forward", "side", "yaw")]
    # logged "left" drives the model's gripper0_<native> fingers
    finger_adr = {}
    for logged, native in to_native.items():
        names = [m.joint(j).name for j in range(m.njnt) if m.joint(j).name.startswith(f"gripper0_{native}_") and "finger" in m.joint(j).name]
        finger_adr[logged] = [adr(n) for n in sorted(names)]
    hand_body = {logged: int(m.site_bodyid[m.site(f"gripper0_{native}_grip_site").id]) for logged, native in to_native.items()}
    hand_site = {logged: int(m.site(f"gripper0_{native}_grip_site").id) for logged, native in to_native.items()}

    # fixture joints: first and last public observation
    def fixture_joints(path):
        out = {}
        obs = json.loads(path.read_text())
        for fx in obs.get("fixtures", []):
            for part in fx.get("interaction_parts", []):
                for ax in part.get("axes", []):
                    if ax.get("joint") and ax.get("position") is not None:
                        out[ax["joint"]] = float(ax["position"])
        return out

    fix0 = fixture_joints(exe / "initial_public_observation.json")
    fix1 = fixture_joints(exe / "final_public_observation.json")

    # free objects: observed positions
    free = {}
    for j in range(m.njnt):
        if m.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE:
            name = m.joint(j).name
            free[name.replace("_joint0", "")] = int(m.jnt_qposadr[j])
    observed = {k: [] for k in free}
    for r in receipts:
        if r["request"]["operation"] == "find_objects" and isinstance(r["response"].get("value"), list):
            for o in r["response"]["value"]:
                if o.get("name") in observed and o.get("frame") == "world":
                    observed[o["name"]].append((int(r["response"]["sequence"]), np.array(o["position"], dtype=float)))

    # gripper close -> open intervals
    holds = []
    open_close = {}
    for r in receipts:
        grp, op = r["request"].get("group", ""), r["request"]["operation"]
        if grp.endswith("_gripper"):
            arm = grp[: -len("_gripper")]
            if op == "close":
                open_close[arm] = int(r["response"]["sequence"])
            elif op == "open" and arm in open_close:
                holds.append({"arm": arm, "start": open_close.pop(arm), "end": int(r["response"]["start_sequence"]),
                              "released": int(r["response"]["sequence"])})
    for arm, start in open_close.items():
        holds.append({"arm": arm, "start": start, "end": frames - 1, "released": frames - 1})

    def observed_at(name, seq):
        obs = observed[name]
        if not obs:
            return None
        before = [o for o in obs if o[0] <= seq]
        after = [o for o in obs if o[0] > seq]
        if not before:
            return after[0][1]
        if not after:
            return before[-1][1]
        return before[-1][1]

    d = mujoco.MjData(m)
    xpos = np.zeros((frames, m.nbody, 3), np.float32)
    xquat = np.zeros((frames, m.nbody, 4), np.float32)
    checks = []
    for hold in holds:
        hold["object"] = None
    for seq in range(frames):
        rec = json.loads(physics[min(len(physics) - 1, max(0, seq * per_seq - 1))])
        d.qpos[:] = m.qpos0
        d.qpos[arm_adr] = rec["qpos"]
        d.qpos[base_adr] = rec["base_qpos"]
        for logged, adrs in finger_adr.items():
            vals = rec["gripper_qpos"][logged]
            for a, v in zip(adrs, vals):
                d.qpos[a] = v
        u = seq / max(1, frames - 1)
        for joint, v0 in fix0.items():
            d.qpos[adr(joint)] = v0 + (fix1.get(joint, v0) - v0) * u
        for name, a in free.items():
            p = observed_at(name, seq)
            if p is not None:
                d.qpos[a: a + 3] = p
            d.qpos[a + 3: a + 7] = [1, 0, 0, 0]
        mujoco.mj_kinematics(m, d)
        for hold in holds:
            if hold["start"] <= seq <= hold["released"]:
                hb = hand_body[hold["arm"]]
                hp, hq = d.xpos[hb].copy(), d.xquat[hb].copy()
                if hold["object"] is None and seq == hold["start"]:
                    tcp = d.site_xpos[hand_site[hold["arm"]]]
                    best = None
                    for name, a in free.items():
                        dist = float(np.linalg.norm(d.qpos[a: a + 3] - tcp))
                        if dist < 0.12 and (best is None or dist < best[1]):
                            best = (name, dist)
                    if best:
                        a = free[best[0]]
                        hold["object"] = best[0]
                        hold["grasp_distance_m"] = round(best[1], 4)
                        hold["rel_p"] = quat_rot(quat_conj(hq), d.qpos[a: a + 3] - hp)
                        hold["rel_q"] = quat_mul(quat_conj(hq), [1, 0, 0, 0])
                if hold["object"]:
                    a = free[hold["object"]]
                    carried_p = hp + quat_rot(hq, hold["rel_p"])
                    carried_q = quat_mul(hq, hold["rel_q"])
                    if seq <= hold["end"]:
                        d.qpos[a: a + 3], d.qpos[a + 3: a + 7] = carried_p, carried_q
                        for oseq, opos in observed[hold["object"]]:
                            if oseq == seq and seq > hold["start"]:
                                checks.append({"object": hold["object"], "sequence": seq,
                                               "error_m": round(float(np.linalg.norm(opos - carried_p)), 4)})
                    else:  # fingers opening: blend to the next observed place
                        after = [o for o in observed[hold["object"]] if o[0] >= hold["released"]]
                        target = after[0][1] if after else carried_p
                        w = (seq - hold["end"]) / max(1, hold["released"] - hold["end"])
                        d.qpos[a: a + 3] = carried_p * (1 - w) + target * w
                        d.qpos[a + 3: a + 7] = carried_q
                        hold["release_q"] = carried_q
                        hold["release_p"] = carried_p * (1 - w) + target * w
        # after release keep the orientation the object was let go with
        for hold in holds:
            if hold["object"] and seq > hold["released"] and "release_q" in hold:
                a = free[hold["object"]]
                d.qpos[a + 3: a + 7] = hold["release_q"]
                # Until the object is seen again it stays where it was let go; an observation made
                # while it was still in the hand must not pull it back.
                if not any(hold["released"] <= o[0] <= seq for o in observed[hold["object"]]):
                    d.qpos[a: a + 3] = hold["release_p"]
        mujoco.mj_kinematics(m, d)
        xpos[seq], xquat[seq] = d.xpos, d.xquat

    info = {
        "frames": frames,
        "seconds_per_frame": float(m.opt.timestep) * per_seq,
        "physics_steps_per_frame": per_seq,
        "task_id": reset.get("task_id"),
        "seed": reset.get("seed"),
        "holds": [{k: (v if not isinstance(v, np.ndarray) else None) for k, v in h.items() if k in ("arm", "start", "end", "released", "object", "grasp_distance_m")} for h in holds],
        "carry_checks": checks,
        "object_observations": {k: len(v) for k, v in observed.items()},
        "fixture_joints": {k: [round(v, 4), round(fix1.get(k, v), 4)] for k, v in fix0.items()},
    }
    return xpos, xquat, info


ROOT = Path(__file__).resolve().parents[2]


def load_model(exe: Path, out: Path):
    """Compile the saved scene.  It names its model files by absolute path inside the release that ran it;
    when that release has been removed, the same file of the release running this script is used instead."""
    text = (exe / "scene.xml").read_text(encoding="utf-8")
    moved = 0

    def fix(match):
        nonlocal moved
        path = Path(match[2])
        if not path.is_absolute() or path.exists():
            return match[0]
        parts = path.parts
        for i in range(1, len(parts)):
            here = ROOT.joinpath(*parts[i:])
            if here.is_file():
                moved += 1
                return match[1] + str(here) + '"'
        return match[0]

    patched = re.sub(r'(\bfile=")([^"]+)"', fix, text)
    if not moved:
        return mujoco.MjModel.from_xml_path(str(exe / "scene.xml")), 0
    copy = out / "scene.patched.xml"
    copy.write_text(patched, encoding="utf-8")
    try:
        return mujoco.MjModel.from_xml_path(str(copy)), moved
    finally:
        copy.unlink(missing_ok=True)


# ---------------------------------------------------------------- bundle
def export(exe: Path, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    m, moved = load_model(exe, out)
    if moved:
        print(f"{moved} model files read from {ROOT} (the run's own release is gone)", file=sys.stderr)
    xpos, xquat, info = load_motion(m, exe)
    moving = np.where((np.ptp(xpos, axis=0).max(axis=1) > 1e-5) | (np.ptp(xquat, axis=0).max(axis=1) > 1e-5))[0]
    moving_index = {int(b): i for i, b in enumerate(moving)}
    tex_mean = texture_means(m)

    # groups: key -> list of (v, n, f, rgba).  key = ("static",) | ("wall", g) | ("body", b)
    groups: dict = {}
    walls = {}
    skipped = 0
    for g in range(m.ngeom):
        if int(m.geom_group[g]) != VISUAL_GROUP:
            continue
        rgba = geom_colour(m, g, tex_mean)
        if rgba[3] < 0.05:
            continue
        shape = geom_shape(m, g)
        if shape is None:
            skipped += 1
            continue
        v, n, f = shape
        gr, gp = mat_from(m.geom_pos[g], m.geom_quat[g])
        v = v @ gr.T + gp
        n = n @ gr.T
        b = int(m.geom_bodyid[g])
        name = m.geom(g).name
        if b in moving_index:
            key = ("body", b)
        else:
            br, bp = mat_from(xpos[0, b], xquat[0, b])
            v = v @ br.T + bp
            n = n @ br.T
            if name.startswith("wall_"):
                key = ("wall", g)
                thin = int(np.argmin(m.geom_size[g]))
                walls[g] = {"name": name, "centre": (bp + br @ gp).round(4).tolist(), "normal": (br @ gr)[:, thin].round(4).tolist()}
            else:
                key = ("static",)
        groups.setdefault(key, []).append((v.astype(np.float32), n.astype(np.float32), f, rgba))

    verts, idx, ranges = [], [], []
    vcount = icount = 0
    for key in sorted(groups, key=lambda k: (0 if k[0] == "static" else 1 if k[0] == "wall" else 2, k[1:] )):
        first = icount
        for v, n, f, rgba in groups[key]:
            rec = np.zeros(len(v), dtype=[("p", "<f4", 3), ("n", "i1", 4), ("c", "u1", 4)])
            rec["p"] = v
            ln = np.linalg.norm(n, axis=1, keepdims=True)
            rec["n"][:, :3] = np.clip(np.round(n / np.maximum(ln, 1e-9) * 127), -127, 127)
            rec["c"] = np.clip(np.round(rgba * 255), 0, 255)
            verts.append(rec)
            idx.append((f + vcount).astype("<u4").reshape(-1))
            vcount += len(v)
            icount += f.size
        entry = {"kind": key[0], "first": first, "count": icount - first}
        if key[0] == "body":
            entry["pose"] = moving_index[key[1]]
            entry["name"] = m.body(key[1]).name
        if key[0] == "wall":
            entry.update(walls[key[1]])
        ranges.append(entry)

    vbytes = np.concatenate(verts).tobytes()
    ibytes = np.concatenate(idx).tobytes()
    (out / "geom.bin").write_bytes(vbytes + ibytes)
    poses = np.concatenate([xpos[:, moving], xquat[:, moving]], axis=2).astype("<f4")
    (out / "poses.bin").write_bytes(poses.tobytes())

    robot = [i for i, b in enumerate(moving) if m.body(int(b)).name.startswith(("robot0_", "mobilebase0_"))]
    centre = xpos[0, moving[robot]].mean(axis=0) if robot else xpos[0].mean(axis=0)
    scene = {
        "format": "attentionbench-free-view-demo-1",
        "display_only": True,
        "vertices": vcount,
        "vertex_bytes": len(vbytes),
        "indices": icount,
        "ranges": ranges,
        "moving_bodies": [m.body(int(b)).name for b in moving],
        "focus": [round(float(x), 3) for x in centre],
        "skipped_geoms": skipped,
        "source": {"execution": exe.name, "task_id": info["task_id"], "seed": info["seed"]},
        "motion": info,
        "notes": {
            "robot": "real: logged joint values of the run, forward kinematics on the saved scene model",
            "fixtures": "first and last public observation, linear in between",
            "objects": "reconstructed: position from logged find_objects answers; carried rigidly with the closed hand; orientation unknown, drawn upright",
            "colours": "material colour times mean texture colour; no textures, no shadows",
        },
    }
    (out / "scene.json").write_text(json.dumps(scene, ensure_ascii=False))
    sizes = {p.name: p.stat().st_size for p in out.iterdir() if p.is_file()}
    print(json.dumps({"vertices": vcount, "triangles": icount // 3, "moving_bodies": len(moving), "frames": info["frames"],
                      "seconds_per_frame": info["seconds_per_frame"], "holds": info["holds"], "carry_checks": info["carry_checks"],
                      "fixture_joints": info["fixture_joints"], "skipped_geoms": skipped, "bytes": sizes}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    export(Path(sys.argv[1]), Path(sys.argv[2]))
