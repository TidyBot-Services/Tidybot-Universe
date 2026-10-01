#!/usr/bin/env python3
"""User-run demonstrations only. --check-only reads hashes without running episodes.

No production or frozen files are edited. Case 2 replaces the Advisor transport
with a labelled fixture; case 3 replaces only the first command at the Safety
entry with an out-of-envelope delta. The Safety implementation stays unchanged.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import redirect_stdout
import hashlib
from importlib.metadata import version
from importlib.util import find_spec
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
PACKAGE = REPO / "benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_guidance_v1_1_dev_2026-10-01"
HINT = "测试回复（本地固定 hint；非真实 GLM）。AB_CONTROL_V1 grasp_offset_m=0.005;approach_tolerance_m=0.004;"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def arguments(policy):
    path = PACKAGE / f"entries/robosuite_cube_lift_seed101/{policy}/launch_arguments.json"
    args = read(path)["fixed_arguments"]
    values = {args[i]: args[i + 1] for i in range(len(args) - 1)
              if args[i].startswith("--") and not args[i + 1].startswith("--")}
    return path, args, values


def preflight(out):
    contract = read(PACKAGE / "execution_contract.json")
    for relative, digest in read(PACKAGE / "sha256_manifest.json")["files"].items():
        require(sha(PACKAGE / relative) == digest, f"冻结文件 SHA 漂移：{relative}")
    require(sha(contract["harness_python"]["path"]) == contract["harness_python"]["sha256"], "Harness Python SHA 漂移")
    require(Path(sys.executable).resolve() == Path(contract["harness_python"]["path"]).resolve(), "请使用执行契约的 Python")
    versions_ref = contract["service_versions"]
    require(sha(versions_ref["path"]) == versions_ref["sha256"], "software_versions SHA 漂移")
    versions = read(versions_ref["path"])
    lock = versions["universe"]["runtime_source_lock"]
    require(sha(lock["path"]) == lock["sha256"], "runtime_source_lock SHA 漂移")
    for relative, digest in read(lock["path"])["file_sha256"].items():
        require(sha(REPO / relative) == digest, f"运行时源码 SHA 漂移：{relative}")
    for policy in ("autonomous", "reactive_help"):
        _, _, v = arguments(policy)
        require((v["--suite"], v["--task"], v["--seed"], v["--attention-policy"]) ==
                ("robosuite", "cube_lift", "101", policy), "冻结入口条件不符")
        for path_key, hash_key in (("--code", "--approved-policy-sha256"),
                                  ("--config", "--approved-config-sha256"),
                                  ("--memory-contract", "--approved-memory-contract-sha256")):
            require(sha(v[path_key]) == v[hash_key], f"入口文件 SHA 漂移：{path_key}")
        require(v["--service-source-root"] == versions["robosuite_service"]["path"], "Service 路径不符")
        require(read(v["--config"])["service_revision"] == versions["robosuite_service"]["commit"], "Service SHA 不符")
    source = Path(versions["robosuite_service"]["path"])
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=source, text=True).strip()
    require(head == versions["robosuite_service"]["commit"] and not dirty, "Service 必须为冻结的干净版本")
    backend_tree = ast.parse((source / "robosuite_sim/backend.py").read_text())
    dependencies = next(ast.literal_eval(n.value) for n in backend_tree.body if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "EXPECTED_VERSIONS" for t in n.targets))
    actual_dependencies = {}
    for name, expected in dependencies.items():
        require(find_spec(name) is not None, f"缺少 {name}：请通过 .sh 使用已验证的虚拟环境入口")
        actual_dependencies[name] = version(name)
        require(actual_dependencies[name] == expected, f"Service 依赖版本不符：{name}")
    report = {"package": str(PACKAGE), "execution_contract_sha256": sha(PACKAGE / "execution_contract.json"),
              "harness_python": contract["harness_python"], "robosuite_service": versions["robosuite_service"],
              "actual_python_entry": sys.executable, "actual_python_sha256": sha(sys.executable),
              "dependency_versions": actual_dependencies,
              "frozen_execution_authorized": contract["execution_authorized"],
              "scope": "单独的用户亲自验证；不会将冻结包改为获准执行", "passed": True}
    save(out / "preflight.json", report)
    print("冻结路径、SHA、运行时字节锁、同 SHA 的 Python 入口及 Service 依赖版本核对通过。", flush=True)
    return contract, versions


def provider_forbidden(*args, **kwargs):
    raise RuntimeError("此验证禁止 GLM / provider 调用")


def recorded_popen(out, original):
    def launch(*args, **kwargs):
        process = original(*args, **kwargs)
        command = args[0] if args else kwargs.get("args", [])
        if isinstance(command, (list, tuple)) and "robosuite_sim" in command:
            require(kwargs.get("start_new_session") is True, "Service 必须独占进程组")
            with (out / "service_groups").open("a") as stream:
                stream.write(f"{process.pid}\n")
                stream.flush()
        return process
    return launch


def verify_1(out, contract, versions):
    import numpy as np
    import robosuite_sim
    from robosuite_sim.client import RobosuiteSimClient
    from benchmarks.attention_harness.robosuite_memory.formal_service import DedicatedRobosuiteService

    _, _, v = arguments("autonomous")
    config = read(v["--config"])
    source = Path(v["--service-source-root"])
    require(Path(robosuite_sim.__file__).resolve().is_relative_to(source), "Service client 导入路径不符")
    # Fixed-port HTTP probe, using the same owned-group stop/watchdog mechanism.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 8082))  # Refuse an occupied port; never attach to another service.
    service = DedicatedRobosuiteService(source_root=source, expected_revision=config["service_revision"],
        log_path=out / "service.log", deadline=time.monotonic() + contract["budget"]["attempt_deadline_seconds"])
    service.base_url = "http://127.0.0.1:8082"
    service.revision = config["service_revision"]
    service.module_origin = str(Path(robosuite_sim.__file__).resolve())
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["MUJOCO_GL"] = env.get("MUJOCO_GL", "egl")
    env["TIDYBOT_RECEIPT_DIR"] = str(out / "action_receipts")
    env["TIDYBOT_INVALID_DEPTH_DIR"] = str(out / "invalid_depth_frames")
    service._log = (out / "service.log").open("wb")
    try:
        service._process = subprocess.Popen([sys.executable, "-m", "robosuite_sim", "--port", "8082", "--enable-sim-gt"],
            cwd=source, env=env, stdout=service._log, stderr=subprocess.STDOUT, start_new_session=True)
        service._watchdog = threading.Thread(target=service._watch, daemon=True)
        service._watchdog.start()
        client = RobosuiteSimClient(service.base_url, timeout=0.5)
        while time.monotonic() < service.deadline:
            require(service._process.poll() is None, "Service 启动失败，查看 service.log")
            try:
                if client.health().get("status") == "ok":
                    break
            except Exception:
                pass
            time.sleep(0.1)
        else:
            raise TimeoutError("Service 启动超时")
        client.timeout = contract["budget"]["attempt_deadline_seconds"]
        initial, low, high, metadata, applied = client.reset_attested(
            task_id=v["--task"], seed=int(v["--seed"]), camera=True, camera_name=config["camera_name"],
            camera_height=config["camera_height"], camera_width=config["camera_width"], horizon=config["horizon"],
            variation={k: config[k] for k in ("scene_id", "object_set_id")})
        require(applied == {k: config[k] for k in ("scene_id", "object_set_id")}, "reset variation 不符")
        action = np.zeros_like(low, dtype=np.float64)
        require(action.shape == (7,), f"非预期动作维数：{action.shape}")
        action[0] = min(0.5, float(high[0]))
        action[-1] = -1.0
        require(bool(np.all(action >= low) and np.all(action <= high)), "动作超出公开边界")
        result = client.step(action)  # Exactly one /v1/step; no reference policy.
        before = np.asarray(initial["robot0_eef_pos"], dtype=float)
        after = np.asarray(result.observation["robot0_eef_pos"], dtype=float)
        delta = after - before
        success = client._request("GET", "/v1/success")
        report = {"task_id": v["--task"], "seed": int(v["--seed"]), "action": action.tolist(),
                  "step_count": 1, "robot0_eef_pos_before": before.tolist(), "robot0_eef_pos_after": after.tolist(),
                  "delta_m": delta.tolist(), "delta_norm_m": float(np.linalg.norm(delta)),
                  "native_result": success, "step_receipt": result.receipt,
                  "position_changed": bool(np.isfinite(delta).all() and np.linalg.norm(delta) > 1e-9)}
        save(out / "movement.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        require(report["position_changed"], "未看到末端数值变化，验证 1 未通过")
        require(type(success.get("native_success")) is bool, "原生结果不是布尔")
    finally:
        if service._process is not None:
            stop = service.stop("personal_verification_cleanup")
            save(out / "service_stop.json", stop)
            print("Service 回收收据：", json.dumps(stop, ensure_ascii=False), flush=True)
            require(stop["leader_reaped"] and stop["process_group_gone"], "Service 回收失败")
        elif service._log is not None:
            service._log.close()


def compile_receipt(episode):
    lines = (episode / "policy.stderr").read_text(encoding="utf-8").splitlines()
    rows = [ast.literal_eval(line.split("guidance_compile_v1 ", 1)[1])
            for line in lines if line.startswith("guidance_compile_v1 ")]
    require(len(rows) == 1, f"必须恰好有一份编译收据：{episode}")
    return rows[0]


def effective_parameters(receipt, policy_path):
    # Read defaults from the frozen AST, rather than inventing receipt fields.
    tree = ast.parse(Path(policy_path).read_text())
    points = next(ast.literal_eval(n.value.args[1]) for n in tree.body
                  if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "points" for t in n.targets))
    defaults = {"grasp_offset_m": points[1][2], "approach_tolerance_m": points[1][3]}
    defaults["open_settle_steps"] = next(ast.literal_eval(n.args[1]) for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get"
        and len(n.args) == 2 and isinstance(n.args[0], ast.Constant) and n.args[0].value == "open_settle_steps")
    compiled = dict(receipt["demo"])
    for values in receipt["memory"].values():
        compiled.update(values)
    compiled.update(receipt["advisor"])
    return {k: {"value": compiled.get(k, default), "source": "receipt" if k in compiled else "frozen_policy_default"}
            for k, default in defaults.items()}


def action_events(trace):
    return [event for event in trace["sdk_events"]
            if event["source"] in {"robot_sdk.arm", "robot_sdk.gripper", "robot_sdk.base"}]


def verify_2(out, contract, versions):
    from benchmarks.attention_harness import formal_attention_cli as cli
    from benchmarks.attention_harness.core.advisor import AdvisorTransportReply
    fixture_calls = []

    def fixture(request):
        fixture_calls.append({"fixture_call": len(fixture_calls) + 1, "provider_calls": 0, "guidance": HINT})
        save(out / "test_reply_calls.json", fixture_calls)
        advice = {"schema_version": "attentionbench.advisor-advice.v1", "request_type": "hint",
                  "diagnosis": "测试回复：固定参数采纳探针", "guidance": HINT,
                  "caution": "仅用于亲自验证；保持独立 Safety", "confidence": 1.0}
        # Zero usage is an explicit local-fixture receipt, not fabricated model usage.
        return AdvisorTransportReply(content=json.dumps(advice, ensure_ascii=False), model="local/test-reply",
            latency_seconds=0, attempts=1, usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            request_id="local-test-reply-no-provider")

    results = {}
    for policy in ("autonomous", "reactive_help"):
        launch_path, fixed, v = arguments(policy)
        arm_out = out / policy
        arm_out.mkdir()
        summary_path = arm_out / "summary.json"
        save(arm_out / "launch.json", {"source": str(launch_path), "source_sha256": sha(launch_path),
             "fixed_arguments": fixed, "execution_contract": str(PACKAGE / "execution_contract.json"),
             "runtime_override": "Advisor transport replaced with labelled local test reply; provider_calls=0"})
        sys.argv = [contract["launch_module"], *fixed, "--artifact-root", str(arm_out), "--summary-path", str(summary_path)]
        with (arm_out / "cli.stdout.log").open("w", encoding="utf-8") as log, redirect_stdout(log), \
             patch.object(cli, "ParccClient", lambda *a, **k: None), \
             patch.object(cli, "SimGTGLMAdvisorTransport", lambda *a, **k: fixture):
            code = cli.main()
        # CLI code 1 means native failure and is still meaningful evidence.
        require(code in (0, 1) and summary_path.is_file(), f"{policy} 缺运行收据")
        summary = read(summary_path)
        results[policy] = summary
        print(f"{policy} 最终原生结果：native_success={summary['native_success']}, stopped_reason={summary['stopped_reason']}", flush=True)
        for attempt in summary["attempts"]:
            episode = Path(attempt["artifact_dir"])
            print(f"{policy} {episode.name} 原生结果：{json.dumps(read(episode / 'native_result.json'), ensure_ascii=False)}", flush=True)
            stop = read(episode / "sandbox_receipt.json")["service_stop"]
            print("Service 回收收据：", json.dumps(stop, ensure_ascii=False), flush=True)
            require(stop and stop["leader_reaped"] and stop["process_group_gone"], "Service 未回收")
    require(not results["autonomous"]["requests"], "autonomous 不应求助")
    baseline = Path(results["autonomous"]["attempts"][0]["artifact_dir"])
    guided = [Path(a["artifact_dir"]) for a in results["reactive_help"]["attempts"]
              if read(Path(a["artifact_dir"]) / "trace.json")["attention_input"].get("advisor_guidance") == HINT]
    require(guided and fixture_calls, "未产生采用测试回复的 attempt（例如提前成功或 Safety 先停止）；本次不判通过，也不自动重跑")
    guided = guided[0]
    _, _, v = arguments("autonomous")
    a, b = compile_receipt(baseline), compile_receipt(guided)
    av, bv = effective_parameters(a, v["--code"]), effective_parameters(b, v["--code"])
    print("autonomous guidance_compile_v1：", json.dumps(a, ensure_ascii=False), flush=True)
    print("reactive_help guidance_compile_v1：", json.dumps(b, ensure_ascii=False), flush=True)
    for key in av:
        print(f"{key}: autonomous={av[key]['value']} ({av[key]['source']}) → reactive_help={bv[key]['value']} ({bv[key]['source']})", flush=True)
    ae, be = action_events(read(baseline / "trace.json")), action_events(read(guided / "trace.json"))
    save(out / "guidance_comparison.json", {"test_reply": HINT, "provider_calls": 0,
        "baseline_episode": str(baseline), "guided_episode": str(guided),
        "baseline_receipt": a, "guided_receipt": b, "baseline_parameters": av, "guided_parameters": bv,
        "baseline_actions": ae, "guided_actions": be,
        "note": "open_settle_steps=10：Robosuite advisor parser 不支持此键。测试回复 ≠ 真实 GLM；动作改变 ≠ 任务成功。"})
    for key, expected in (("grasp_offset_m", 0.005), ("approach_tolerance_m", 0.004)):
        require(bv[key]["value"] == expected and av[key]["value"] != expected, f"未采纳 {key}")
    arm_parameters = lambda events: [e["arguments"] for e in events if e["source"] == "robot_sdk.arm" and e["status"] == "completed"]
    print("autonomous 实际完成的 arm 参数（前 3 条）：", json.dumps(arm_parameters(ae)[:3], ensure_ascii=False), flush=True)
    print("reactive_help 实际完成的 arm 参数（前 3 条）：", json.dumps(arm_parameters(be)[:3], ensure_ascii=False), flush=True)
    require(arm_parameters(ae) != arm_parameters(be) and arm_parameters(be), "编译收据有差异，但实际完成的机械臂命令未改变")
    require(any(x.get("tolerance") == 0.004 for x in arm_parameters(be)), "未见 hint 指定容差的实际机械臂动作")
    print("测试回复 ≠ 真实 GLM；动作改变 ≠ 任务成功。", flush=True)


def verify_3(out, contract, versions):
    from benchmarks.attention_harness.robocasa_native.safety_monitor import SafetyMonitorBackend
    from benchmarks.attention_harness.robosuite_memory.adapter import RobosuiteSimGTBackend
    from benchmarks.attention_harness.robosuite_memory.formal_runner import RobosuiteFormalSuiteRunner
    from benchmarks.attention_harness.formal_runner_boundary import FormalRunRequest, run_with_formal_boundary
    _, fixed, v = arguments("autonomous")
    run_dir = out / "attention-robosuite-safety-negative-personal"
    run_dir.mkdir()
    injected, dispatched = [], []
    original_gripper = SafetyMonitorBackend.set_gripper
    original_backend_gripper = RobosuiteSimGTBackend.set_gripper
    original_backend_delta = RobosuiteSimGTBackend.move_arm_delta

    def inject_first(self, command, *, settle_steps):
        if not injected:
            delta = 2 * self.max_delta_m
            injected.append({"original": "set_gripper", "replacement": "move_arm_delta",
                             "dx": delta, "limit_m": self.max_delta_m, "scope": "controlled test injection at Safety entry"})
            save(out / "injection.json", injected)
            # Original, independent Safety rejects this before backend dispatch.
            return self.move_arm_delta(delta, 0.0, 0.0, (0.0, 0.0, 0.0))
        return original_gripper(self, command, settle_steps=settle_steps)

    def track_gripper(self, *args, **kwargs):
        dispatched.append("set_gripper")
        return original_backend_gripper(self, *args, **kwargs)

    def track_delta(self, *args, **kwargs):
        dispatched.append("move_arm_delta")
        return original_backend_delta(self, *args, **kwargs)

    request = FormalRunRequest(suite=v["--suite"], task_id=v["--task"], seed=int(v["--seed"]),
        policy_code_path=Path(v["--code"]), policy_sha256=v["--approved-policy-sha256"],
        config_path=Path(v["--config"]), config_sha256=v["--approved-config-sha256"],
        artifact_root=run_dir / "attempts", overall_deadline_seconds=float(v["--overall-deadline-seconds"]),
        attempt_deadline_seconds=float(v["--attempt-deadline-seconds"]), sdk_call_limit=contract["budget"]["sdk_calls_max"],
        run_id=f"run:{run_dir.name}", attempt_id=f"attempt:{run_dir.name}:0", attention_input={})
    save(out / "launch.json", {"frozen_arguments": fixed, "test_scope": "single dedicated Safety control; no scheduler, Advisor or Memory",
                              "policy_unchanged": True, "safety_implementation_unchanged": True})
    runner = RobosuiteFormalSuiteRunner(service_source_root=Path(v["--service-source-root"]))
    with patch.object(SafetyMonitorBackend, "set_gripper", inject_first), \
         patch.object(RobosuiteSimGTBackend, "set_gripper", track_gripper), \
         patch.object(RobosuiteSimGTBackend, "move_arm_delta", track_delta):
        result = run_with_formal_boundary(request, runner=runner)
    episode = Path(result["artifact_dir"])
    safety, trace = read(episode / "safety.json"), read(episode / "trace.json")
    stop = read(episode / "sandbox_receipt.json")["service_stop"]
    actions = action_events(trace)
    report = {"independent_safety_source": safety["source"], "unsafe_attempts": safety["unsafe_attempts"],
              "violations": safety["violations"], "sdk_action_count": len(actions), "sdk_actions": actions,
              "second_action_present": len(actions) > 1, "backend_action_dispatches": dispatched,
              "backend_steps": trace["backend_steps"], "service_stop": stop, "result": result}
    save(out / "safety_comparison.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    require(len(injected) == 1 and safety["source"] == "independent_safety_monitor", "注入未到达独立 Safety")
    require(safety["unsafe_attempts"] == 1 and [x["kind"] for x in safety["violations"]] == ["delta_exceeds_limit"], "Safety 未记录预期 unsafe")
    require(len(actions) == 1 and actions[0]["status"] == "failed" and not dispatched and not trace["backend_steps"], "存在第二个动作或危险指令被派发")
    require(stop and stop["leader_reaped"] and stop["process_group_gone"], "Service 未回收")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true", help="仅哈希预检，绝不起 Service 或 episode")
    args = parser.parse_args()
    out = args.out.resolve()
    require(not out.is_relative_to(PACKAGE), "产物目录不能位于冻结包")
    out.mkdir(parents=True, exist_ok=True)
    contract, versions = preflight(out)
    if args.check_only:
        return 0
    require(os.environ.get("ATTENTION_PERSONAL_VERIFY_OUT") == str(out), "请通过相应 .sh 运行，以保证退出清理")
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"operator stop: signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    # Block provider paths even if an unexpected scheduler path attempts one.
    from benchmarks.attention_harness import parcc_client
    import robosuite_sim
    require(Path(robosuite_sim.__file__).resolve().is_relative_to(Path(versions["robosuite_service"]["path"])), "Service 导入漂移")
    original = subprocess.Popen
    try:
        with patch.object(parcc_client.ParccClient, "chat", provider_forbidden), \
             patch.object(parcc_client, "urlopen", provider_forbidden), \
             patch.object(subprocess, "Popen", recorded_popen(out, original)):
            {1: verify_1, 2: verify_2, 3: verify_3}[args.case](out, contract, versions)
        print(f"验证 {args.case} 判定通过；仍以随后 shell_cleanup.json 的进程检查为准。", flush=True)
        return 0
    finally:
        save(out / "provider_calls.json", {"provider_calls": 0, "source": "local fixture and provider entry guards",
                                          "formal_eligible": False})


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("用户停止；外层脚本正在清理所有本次进程组。", flush=True)
        raise SystemExit(130)
    except Exception as exc:
        print(f"验证未通过/未完成：{type(exc).__name__}: {exc}；不自动重跑。", file=sys.stderr, flush=True)
        raise SystemExit(2)
