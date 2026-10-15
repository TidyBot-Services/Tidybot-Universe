"""Read-only FK/IK diagnosis from real recorded scene coordinates."""
from pathlib import Path
from types import SimpleNamespace
import json
import xml.etree.ElementTree as ET
import numpy as np
import sapien
import argparse
from scipy.spatial.transform import Rotation

from benchmarks.attention_harness.robocasa_native.robot_config import RobotSelection
from .kitchen import ROOT, MODEL
from .planning import OpenArmPlanner


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--yaw-sweep", action="store_true")
    parser.add_argument("--mount-z", type=float)
    args = parser.parse_args()
    bootstrap = json.loads((ROOT / "outputs/openarm-robocasa/scene-probe-6/bootstrap.json").read_text())
    scene = json.loads((ROOT / "outputs/openarm-robocasa/scene-probe-8/result.json").read_text())
    xml = ET.parse(MODEL).getroot()
    joints = {j.get("name"): j for j in xml.findall("joint")}
    names = bootstrap["joint_order"]
    qlimits = np.array([[float(joints[n].find("limit").get(key)) for key in ["lower", "upper"]] for n in names])
    mount = bootstrap["mount"]
    if args.mount_z is not None:
        mount[2] = args.mount_z
    rig = SimpleNamespace(xml=xml, names=names, idx={n: i for i, n in enumerate(names)}, qlimits=qlimits,
                          robot=SimpleNamespace(qpos=np.array(bootstrap["initial_qpos"]), root_pose=sapien.Pose(mount[:3], mount[3:])))
    kitchen = SimpleNamespace(rig=rig, selection=RobotSelection("OpenArm", "left"))
    planner = OpenArmPlanner(kitchen)
    results = []
    for height in [.15, .05, 0.]:
        point = np.array(scene["objects_after"]["obj"]["position"]) + [0, 0, height]
        orientations = [None, np.diag([1., -1., -1.])]
        if args.yaw_sweep:
            orientations = [Rotation.from_euler("z", yaw).as_matrix() @ np.diag([1., -1., -1.])
                            for yaw in [np.pi / 2, np.pi, -np.pi / 2, -1.696]]
        for orientation in orientations:
            try:
                candidates = planner.inverse(point, orientation)
                row = {"reachable": True, "candidates": len(candidates), "best": candidates[0][1].tolist()}
            except Exception as exc:
                row = {"reachable": False, "error": str(exc)}
            row.update(position=point.tolist(), orientation=None if orientation is None else orientation.tolist(), diagnostics=planner.inverse_diagnostics)
            results.append(row)
    filename = "ik-yaw-sweep" if args.yaw_sweep else "ik-diagnosis"
    if args.mount_z is not None:
        filename += "-mount-z" + str(round(args.mount_z * 1000))
    (ROOT / ("outputs/openarm-robocasa/" + filename + ".json")).write_text(json.dumps(results, indent=2))
    print(json.dumps([{k: row[k] for k in ["position", "orientation", "reachable"]} for row in results]), flush=True)


if __name__ == "__main__":
    main()
