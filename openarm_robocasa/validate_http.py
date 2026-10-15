"""Real HTTP engineering probe; retain all failures and native results."""
from pathlib import Path
import argparse
import importlib.util
import io
import json
import time
import traceback

import numpy as np
from PIL import Image

from .session import OwnedOpenArmService

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--arm", choices=["left", "right"], default="left")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location("accepted_openarm_http_client", ROOT / "openarm_phase1/validate_client.py")
    helpers = importlib.util.module_from_spec(spec); spec.loader.exec_module(helpers)
    checks = helpers.Checks(); result = {}; owned = None
    try:
        with OwnedOpenArmService(args.out / "owned", python="/home/truares/miniconda3/envs/maniskill/bin/python",
                                 arm=args.arm, deadline=time.monotonic() + 180) as owned:
            client = helpers.Client(owned.url, args.out)
            checks.check("OpenArm identity", client.get("/health")["robot"] == {"model": "OpenArm", "execution_arm": args.arm})
            state = client.wait_steps(480)
            inactive = "right" if args.arm == "left" else "left"
            held = np.array(state[inactive]["qpos"])
            payload = client.get("/observation")
            (args.out / "observation.npz").write_bytes(payload)
            with np.load(io.BytesIO(payload), allow_pickle=False) as observation:
                rgb = observation["rgb"]; depth = observation["depth_m"]; valid = observation["valid_depth"]
                checks.check("real RGB/depth", rgb.shape == (480, 640, 3) and np.isfinite(rgb).all()
                             and 0 <= rgb.min() <= rgb.max() <= 1 and np.std(rgb) > 0
                             and depth.shape == (480, 640) and valid.mean() >= .01 and np.isfinite(depth).all())
                Image.fromarray(np.uint8(np.clip(rgb, 0, 1) * 255)).save(args.out / "kitchen.png")
            native = client.get("/task/success")
            checks.check("native false initially", native["success"] is False)
            client.post("/actions", {inactive: state[inactive]["qpos"]}, expected=400)
            checks.check("inactive arm rejection", client.get("/state")["epoch"] == state["epoch"])
            q = np.array(state[args.arm]["qpos"]); q[0] += .05 if args.arm == "left" else -.05
            receipt = client.post("/actions", {args.arm: q.tolist(), "epoch": state["epoch"]}, expected=202)
            finished = client.wait_action(receipt["id"])
            checks.check("real joint motion completed", finished["status"] == "completed", finished)
            checks.check("inactive arm held", np.max(abs(np.array(client.get("/state")[inactive]["qpos"]) - held)) <= .01)
            receipt = client.post("/actions", {args.arm + "_gripper": .04}, expected=202)
            finished = client.wait_action(receipt["id"])
            checks.check("real gripper motion completed", finished["status"] == "completed", finished)
            receipt = client.post("/actions", {args.arm: state[args.arm]["qpos"]}, expected=202)
            stopped = client.post("/stop", {})
            checks.check("committed stop", stopped["epoch"] > state["epoch"] and client.get("/actions/" + receipt["id"])["status"] == "stopped")
            hold = client.wait_steps(stopped["total_steps"] + 240)
            checks.check("stop does not resume old motion", hold["active_action"] is None)
            client.post("/actions", {args.arm: q.tolist(), "epoch": state["epoch"]}, expected=409)
            from benchmarks.attention_harness.robocasa_native.openarm_backend import OpenArmActionBackend
            sdk = OpenArmActionBackend(owned.url, execution_arm=args.arm, timeout_seconds=90.)
            obs = sdk.observe()
            checks.check("SDK selected TCP world frame", np.allclose(obs["robot0_eef_pos"], np.array(hold["tcp_world"][args.arm])[:3, 3], atol=.004))
            objects = sdk.find_objects(["obj"])
            target = np.array(objects[0]["position"]) + [0, 0, .15]
            sdk.move_arm_to_position(*target.tolist(), tolerance=.004, max_steps=100)
            checks.check("SDK physical pregrasp", bool(sdk.action_receipts), sdk.planned_motion_receipts)
            result = {"engineering_checks_passed": True, "native_evaluation": client.get("/task/success"),
                      "sdk_actions": sdk.action_receipts, "sdk_plans": sdk.planned_motion_receipts}
    except BaseException:
        (args.out / "failure.txt").write_text(traceback.format_exc())
        result.update(engineering_checks_passed=False, error=traceback.format_exc())
    finally:
        checks.save(args.out / "checks.json")
        if owned is not None:
            result["reap"] = owned.stop_receipt
        (args.out / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False))
    print(json.dumps({"passed": result.get("engineering_checks_passed"), "checks": len(checks.rows),
                      "reap": result.get("reap"), "error": result.get("error", "")[-1200:]}), flush=True)
    return int(not result.get("engineering_checks_passed"))


if __name__ == "__main__":
    raise SystemExit(main())
