"""Scripted OpenArm365 reference ladder for development seed 101.

Real execution is opt-in. OpenArm365 Dev v2 defaults to kp=1500;
ATTN_DIAG_JOINT_KP can override the diagnostic gain.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

from openarm365_dev.policy_validation import validate_generated_policy


STAGES = ("approach", "grasp", "lift", "carry", "release")
DEFAULT_RUNS = ",".join(f"{stage}:0.02" for stage in STAGES)
INITIAL_EEF = (0.4381810091, -2.2771536622, 1.0893940270)
INITIAL_QUAT = (0.0773645919, 0.7601851670, -0.3816544795, -0.5200702741)
INITIAL_OBJ = (0.4246760939, -2.3935140080, 0.9621218952)
INITIAL_SINK = (0.2999998883, -1.7962400050, 0.9861874579)
PREGRASP_M = 0.10
LIFT_M = 0.12
SDK_LIMIT = 70
MODULE_NAME = "benchmarks.attention_harness.diagnostics.reference_ladder"


def tool_z_axis(quat):
    """Third column of the xyzw quaternion rotation matrix, normalized."""
    x, y, z, w = (float(v) for v in quat)
    scale = (x*x + y*y + z*z + w*w) ** 0.5
    x, y, z, w = x/scale, y/scale, z/scale, w/scale
    return (2*(x*z + y*w), 2*(y*z - x*w), 1 - 2*(x*x + y*y))


def segment_count(vector, step_cap):
    """The policy uses the same strictly bounded split, with one safety step."""
    distance = sum(float(v)*float(v) for v in vector) ** 0.5
    return int(distance / step_cap) + 1 if distance else 0


def call_plan(stage, step_cap, *, eef=INITIAL_EEF, quat=INITIAL_QUAT,
              obj=INITIAL_OBJ, sink=INITIAL_SINK, carry_eef=None):
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage}")
    if not math.isfinite(step_cap) or not 0 < step_cap <= 0.05:
        raise ValueError("step_cap must be finite and in (0, 0.05]")
    axis = tool_z_axis(quat)
    pre = tuple(obj[i] - PREGRASP_M*axis[i] for i in range(3))
    pre_steps = segment_count(tuple(pre[i] - eef[i] for i in range(3)), step_cap)
    advance_steps = segment_count(tuple(PREGRASP_M*axis[i] for i in range(3)), step_cap)
    lift_steps = segment_count((0, 0, LIFT_M), step_cap)
    # The carry is horizontal and targets the sink centre. Its Z is retained after lift.
    carry_origin = carry_eef or (obj[0], obj[1], obj[2] + LIFT_M)
    carry_steps = segment_count((sink[0] - carry_origin[0],
                                 sink[1] - carry_origin[1], 0), step_cap)
    counts = {"approach": 3 + pre_steps + advance_steps,
              "grasp": 1, "lift": lift_steps,
              "carry": 1 + carry_steps, "release": 1}
    total = sum(counts[name] for name in STAGES[:STAGES.index(stage) + 1])
    return {"total": total, "by_stage": counts, "segments": {
        "pregrasp": pre_steps, "advance": advance_steps,
        "lift": lift_steps, "carry": carry_steps}}


def build_policy(stage, step_cap=0.02):
    plan = call_plan(stage, step_cap)
    if plan["total"] > SDK_LIMIT:
        raise ValueError(f"{stage}:{step_cap:g} estimates {plan['total']} SDK calls; limit is {SDK_LIMIT}")
    # Each segment is recomputed from live public data; the counter asserts the
    # budget before dispatch, so unusual poses cannot overspend the SDK allowance.
    src = [
        "from robot_sdk import sensors, left_arm, left_gripper",
        f"step_cap = {step_cap!r}",
        "calls = 0",
        "obs = sensors.get_observation()", "calls += 1",
        "objects = sensors.find_objects()", "calls += 1",
        "eef = obs['robot0_left_eef_pos']",
        "q = obs['robot0_left_eef_quat']",
        "x, y, z, w = q[0], q[1], q[2], q[3]",
        "norm = (x*x + y*y + z*z + w*w) ** 0.5",
        "x, y, z, w = x/norm, y/norm, z/norm, w/norm",
        "ax = [2*(x*z + y*w), 2*(y*z - x*w), 1 - 2*(x*x + y*y)]",
        "obj = None", "sink = None",
        "for item in objects:",
        "    if item['name'] == 'obj': obj = item['position']",
        "    if item['name'] == 'sink': sink = item['position']",
        "assert obj is not None and sink is not None, 'obj or sink missing'",
        "pre = [obj[j] - 0.10*ax[j] for j in range(3)]",
        "left_gripper.open()", "calls += 1",
        "delta = [pre[j] - eef[j] for j in range(3)]",
    ]

    def move_segment():
        return [
            "distance = sum(v*v for v in delta) ** 0.5",
            "n = int(distance / step_cap) + 1 if distance else 0",
            f"assert calls + n <= {SDK_LIMIT}, 'SDK call estimate exceeded'",
            "for i in range(n):",
            "    left_arm.move_delta(delta[0]/n, delta[1]/n, delta[2]/n)",
            "    calls += 1",
        ]

    src += move_segment()
    src += ["delta = [0.10*ax[j] for j in range(3)]"] + move_segment()
    if STAGES.index(stage) >= 1:
        src += [f"assert calls + 1 <= {SDK_LIMIT}", "left_gripper.close()", "calls += 1"]
    if STAGES.index(stage) >= 2:
        src += ["delta = [0.0, 0.0, 0.12]"] + move_segment()
    if STAGES.index(stage) >= 3:
        src += [f"assert calls + 1 <= {SDK_LIMIT}",
                "obs2 = sensors.get_observation()", "calls += 1",
                "eef2 = obs2['robot0_left_eef_pos']",
                "delta = [sink[0] - eef2[0], sink[1] - eef2[1], 0.0]"] + move_segment()
    if STAGES.index(stage) >= 4:
        src += [f"assert calls + 1 <= {SDK_LIMIT}", "left_gripper.open()", "calls += 1"]
    source = "\n".join(src) + "\n"
    validate_generated_policy(source)
    return source


def parse_runs(value):
    runs = []
    for token in value.split(","):
        bits = token.strip().split(":")
        if len(bits) != 2:
            raise ValueError(f"expected STAGE:STEP, got {token!r}")
        stage, cap = bits[0], float(bits[1])
        call_plan(stage, cap)
        runs.append((stage, cap))
    if not runs:
        raise ValueError("at least one run required")
    return runs


def _read_json(path):
    return json.loads(path.read_text()) if path.is_file() else {}


def _tcp_position(row):
    matrix = row.get("tcp", {}).get("left")
    if not matrix:
        return None
    return [float(matrix[i][3]) for i in range(3)] if isinstance(matrix[0], list) else list(matrix[:3])


def summarize_execution(run_dir, stage, step_cap, wall_seconds, error=None):
    """Read one real execution directory, retaining per-command certificates."""
    versions = sorted((run_dir / "artifacts").glob("*/cells/*/v1"))
    # The Dev experiment root is named devexp-<hash> below artifacts.
    if not versions:
        versions = sorted(run_dir.glob("artifacts/**/cells/*/v1"))
    version = versions[0] if versions else None
    executions = sorted(version.glob("executions/*")) if version else []
    execution = executions[0] if executions else None
    feedback = _read_json(version / "public-feedback.json") if version else {}
    events = feedback.get("action_trace", [])
    completed = [e for e in events if e.get("status") == "completed"]
    failed = [e for e in events if e.get("status") != "completed"]
    first = failed[0] if failed else None
    certificates = []
    if execution:
        for path in sorted((execution / "planner").glob("continuous-*.json")):
            data = _read_json(path)
            cert = data.get("certificate") or {}
            certificates.append({"file": path.name, "geometry": cert.get("result"),
                                 "queries": cert.get("queries"),
                                 "elapsed_seconds": cert.get("elapsed_seconds"),
                                 "failure_reason": (cert.get("failure") or {}).get("reason")})
    initial = _read_json(execution / "initial_public_observation.json") if execution else {}
    final = _read_json(execution / "final_public_observation.json") if execution else {}
    native = _read_json(execution / "native_result.json") if execution else {}
    physics = []
    physics_path = execution / "native-physics.jsonl" if execution else None
    if physics_path and physics_path.is_file():
        with physics_path.open() as stream:
            for line in stream:
                if line.strip():
                    row = json.loads(line)
                    if not physics:
                        physics.append(row)
                    elif len(physics) == 1:
                        physics.append(row)
                    else:
                        physics[-1] = row
    robot_initial = initial.get("robot", {})
    robot_final = final.get("robot", {})
    eef = robot_initial.get("robot0_left_eef_pos", INITIAL_EEF)
    quat = robot_initial.get("robot0_left_eef_quat", INITIAL_QUAT)
    obj = initial.get("perception", {}).get("values", {}).get("obj_pos", INITIAL_OBJ)
    sink = initial.get("perception", {}).get("values", {}).get("sink_pos", INITIAL_SINK)
    observed_eefs = [e.get("observation", {}).get("robot0_left_eef_pos")
                     for e in events if e.get("operation") == "get_observation"
                     and e.get("status") == "completed"]
    carry_obs = observed_eefs[1] if len(observed_eefs) > 1 else None
    plan = call_plan(stage, step_cap, eef=eef, quat=quat, obj=obj, sink=sink,
                     carry_eef=carry_obs)
    expected = {
        "approach": ["get_observation", "find_objects", "open"] +
                    ["move_delta"] * (plan["segments"]["pregrasp"] + plan["segments"]["advance"]),
        "grasp": ["close"], "lift": ["move_delta"] * plan["segments"]["lift"],
        "carry": ["get_observation"] + ["move_delta"] * plan["segments"]["carry"],
        "release": ["open"]}
    reached = None
    count = 0
    for name in STAGES[:STAGES.index(stage) + 1]:
        required = expected[name]
        segment = events[count:count + len(required)]
        if len(segment) == len(required) and all(
                event.get("status") == "completed" and event.get("operation") == operation
                for event, operation in zip(segment, required)):
            reached = name
            count += len(required)
        else:
            break
    failure_detail = first.get("error") if first else None
    if isinstance(failure_detail, dict):
        failure_detail = failure_detail.get("message") or failure_detail.get("reason")
    return {"stage": stage, "step_cap": step_cap, "wall_seconds": round(wall_seconds, 3),
            "sdk_completed": len(completed), "sdk_failed": len(failed),
            "first_failure": {"operation": first.get("operation"),
                              "reason": failure_detail} if first else None,
            "certificates": certificates,
            "eef_start": _tcp_position(physics[0]) if physics else robot_initial.get("robot0_left_eef_pos"),
            "eef_end": _tcp_position(physics[-1]) if physics else robot_final.get("robot0_left_eef_pos"),
            "obj_start": initial.get("perception", {}).get("values", {}).get("obj_pos"),
            "obj_end": final.get("perception", {}).get("values", {}).get("obj_pos"),
            "gripper_start": robot_initial.get("robot0_left_gripper_qpos"),
            "gripper_end": robot_final.get("robot0_left_gripper_qpos"),
            "native_success": native.get("native_success"),
            "native_status": native.get("status"), "reached_stage": reached,
            "execution_dir": str(execution) if execution else None,
            "runner_error": error}


def markdown_table(records):
    headers = ["阶段", "步长", "墙钟秒", "SDK 完成/失败", "首个失败", "move_delta 认证", "手爪起→止", "物体起→止", "夹爪起→止", "原生结果", "到达阶段"]
    def compact(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")) if value is not None else "—"
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"]*len(headers)) + " |"]
    for r in records:
        certs = "; ".join(f"{i+1}:{c['geometry']} q={c['queries']} t={c['elapsed_seconds']} reason={c['failure_reason']}" for i,c in enumerate(r["certificates"])) or "—"
        failure = r["first_failure"] or r["runner_error"]
        cells = [r["stage"], f"{r['step_cap']:g}", f"{r['wall_seconds']:.1f}",
                 f"{r['sdk_completed']}/{r['sdk_failed']}", compact(failure), certs,
                 compact(r["eef_start"]) + "→" + compact(r["eef_end"]),
                 compact(r["obj_start"]) + "→" + compact(r["obj_end"]),
                 compact(r["gripper_start"]) + "→" + compact(r["gripper_end"]),
                 compact({"status": r["native_status"], "success": r["native_success"]}),
                 r["reached_stage"] or "—"]
        lines.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
    return "\n".join(lines) + "\n"


def _execute_one(run_dir, stage, step_cap):
    # Imports here keep --dry-run and --print-policy offline and fast.
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    from benchmarks.attention_harness.dev_experiment import create, run, verify_manifest
    from benchmarks.attention_harness.parcc_client import ParccResponse
    from benchmarks.attention_harness.eval_agent import default_config as eval_config
    from benchmarks.attention_harness.dev_advisor_proxy import default_config as advisor_config

    class ScriptedProvider:
        response_observer = None

        def chat(self, **kwargs):
            reply = json.dumps({"source": build_policy(stage, step_cap),
                                "reason": "scripted reference ladder"})
            usage = {"total_tokens": 100, "prompt_tokens": 40, "completion_tokens": 60}
            body = {"model": "scripted/reference", "choices": [{"message": {"content": reply}}],
                    "usage": usage}
            if self.response_observer:
                self.response_observer(200, {}, json.dumps(body).encode())
            return ParccResponse("scripted/reference", reply, None, 0.01, 1, usage)

    run_dir.mkdir(parents=True, exist_ok=True)
    store = DevV2Store(run_dir / "ledger.sqlite")
    selection = dict(request_key=f"ref-ladder-{stage}-{step_cap:g}-20261017",
                     mode="interactive", tasks=["pick_place_counter_to_sink"], seeds=[101],
                     policies=["autonomous"], target_depth=1, target_valid_executions=1,
                     max_versions=1, help_budget=0, coverage="budget_limited",
                     reproducible_reset=True, attempt_wall_seconds=480,
                     eval_agent=eval_config(False), advisor_proxy=advisor_config(False))
    experiment = create(store, run_dir / "artifacts", selection)
    manifest = verify_manifest(experiment)
    budget_file = run_dir / "budget.json"
    budget_file.write_text(json.dumps({"goal_id": manifest["goal_id"],
        "ledger_path": str(store.path.resolve()), "experiment_id": experiment["experiment_id"],
        "manifest_sha256": experiment["manifest"]["sha256"],
        "limits": manifest["goal_limits"], "wall_limit": manifest["wall_limit"]}))
    run(store, experiment["experiment_id"], max_cells=1, budget_file=budget_file,
        client=ScriptedProvider())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", default=DEFAULT_RUNS)
    parser.add_argument("--out", type=Path, default=Path("reference-ladder-results"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-policy", metavar="STAGE:STEP")
    parser.add_argument("--execute-one", nargs=3, metavar=("DIR", "STAGE", "STEP"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.execute_one:
        directory, stage, step = args.execute_one
        _execute_one(Path(directory), stage, float(step))
        return 0
    print("提示：OpenArm365 Dev v2 关节位置控制器默认 kp=1500")
    if os.environ.get("ATTN_DIAG_JOINT_KP", "1500") != "1500":
        print("警告：当前 ATTN_DIAG_JOINT_KP 不是 1500；规划与跟踪可能不同于已验证设置。")
    if args.print_policy:
        if "," in args.print_policy:
            parser.error("--print-policy accepts exactly one STAGE:STEP")
        stage, cap = parse_runs(args.print_policy)[0]
        source = build_policy(stage, cap)
        print(f"# {stage}:{cap:g}; estimated SDK calls: {call_plan(stage, cap)['total']}")
        print(source, end="")
        return 0
    runs = parse_runs(args.runs)
    prepared = [(stage, cap, build_policy(stage, cap)) for stage, cap in runs]
    for i, (stage, cap, source) in enumerate(prepared, 1):
        print(f"{i}. {stage}:{cap:g}; estimated SDK calls: {call_plan(stage, cap)['total']}; output: {args.out / f'{i:02d}-{stage}-{cap:g}'}")
        if args.dry_run:
            print("\n".join(source.splitlines()[:12]))
            print("...")
    if args.dry_run:
        print("dry-run: no execution, no simulator, no output directory created")
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    for i, (stage, cap, _) in enumerate(prepared, 1):
        run_dir = args.out / f"{i:02d}-{stage}-{cap:g}"
        if run_dir.exists() and any(run_dir.iterdir()):
            parser.error(f"run directory already contains data: {run_dir}; choose a fresh --out")
    records = []
    for i, (stage, cap, source) in enumerate(prepared, 1):
        run_dir = args.out / f"{i:02d}-{stage}-{cap:g}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "policy.py").write_text(source)
        started = time.monotonic()
        child = subprocess.run([sys.executable, "-m", MODULE_NAME, "--execute-one",
                                str(run_dir.resolve()), stage, str(cap)],
                               env=os.environ.copy(), capture_output=True, text=True)
        (run_dir / "runner.stdout").write_text(child.stdout)
        (run_dir / "runner.stderr").write_text(child.stderr)
        error = child.stderr[-2000:] if child.returncode else None
        record = summarize_execution(run_dir, stage, cap, time.monotonic()-started, error)
        records.append(record)
        (args.out / "results.json").write_text(json.dumps(records, ensure_ascii=False, indent=2))
        (args.out / "results.md").write_text(markdown_table(records))
        print(f"{stage}:{cap:g}: reached={record['reached_stage']}, native_success={record['native_success']}, error={bool(error)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
