"""Run a seven-policy GT-perception development sequence on either simulator."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .attention_modes import AssistanceMode
from .core.policies import POLICY_IDS
from .robocasa_native.agent_actions import AgentServerActionBackend
from .robocasa_native.client import RobocasaSimClient
from .robocasa_native.sim_gt_cli import _load_policy
from .robocasa_native.policy_sandbox import execute_generated_policy, validate_generated_policy
from .robocasa_native.tasks import ROBOCASA_TASKS
from .robosuite_memory.adapter import RobosuiteSimGTBackend
from .sim_gt_attention_run import run_robocasa_attention, run_robosuite_attention
from .task_registry import TASKS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["robocasa", "robosuite"], required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--attention-policy", choices=POLICY_IDS, required=True)
    policy_group = parser.add_mutually_exclusive_group(required=True)
    policy_group.add_argument("--robot-policy", help="operator-approved trusted module:function")
    policy_group.add_argument("--generated-policy-file", type=Path,
                              help="development-only sandboxed policy source")
    parser.add_argument("--artifact-root", type=Path, default=Path("artifacts/attentionbench-v2-attention"))
    parser.add_argument("--store-path", type=Path, help="shared Memory/Attention SQLite store")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--assistance-credits", type=int, default=1)
    parser.add_argument("--assistance-mode", choices=[mode.value for mode in AssistanceMode],
                        default=AssistanceMode.BENCHMARK_PROXY.value)
    parser.add_argument("--human-deadline-seconds", type=float, default=60.0)
    parser.add_argument("--token-limit", type=int, default=30000)
    parser.add_argument("--policy-config", type=Path, help="JSON decision-policy parameters")
    parser.add_argument("--approved-policy-config-sha256", help="pre-registered random policy config digest")
    parser.add_argument("--demo-prior", type=Path, help="public pre-task demo description text")
    parser.add_argument("--approved-demo-sha256", help="pre-registered SHA-256 of demo manifest")
    parser.add_argument("--hypothesis", default="", help="agent-visible failure hypothesis")
    parser.add_argument("--variation", type=Path, help="attested variation JSON")
    parser.add_argument("--sim-url")
    parser.add_argument("--camera-name", default="agentview")
    parser.add_argument("--replay-camera-ws", help="RoboCasa camera bridge WS URL for per-attempt replay")
    parser.add_argument("--record-replay", action="store_true",
                        help="record public Robosuite RGB frames during each attempt")
    parser.add_argument("--agent-url", default="http://127.0.0.1:8080")
    parser.add_argument("--confirm-simulator-agent", action="store_true")
    args = parser.parse_args()
    if args.attention_policy == "budget_matched_random_escalation" and (
            args.policy_config is None or args.approved_policy_config_sha256 is None):
        parser.error("random escalation requires approved pre-registered policy config")
    if args.approved_policy_config_sha256 is not None and args.policy_config is None:
        parser.error("approved policy config digest requires --policy-config")
    policy_config = None
    if args.policy_config is not None:
        policy_config_bytes = args.policy_config.read_bytes()
        if (args.approved_policy_config_sha256 is not None and
                hashlib.sha256(policy_config_bytes).hexdigest() != args.approved_policy_config_sha256):
            parser.error("policy config differs from approved SHA-256")
        policy_config = json.loads(policy_config_bytes)
    if args.generated_policy_file is not None:
        generated_code = args.generated_policy_file.read_text(encoding="utf-8")
        validate_generated_policy(generated_code)

        def robot_policy(sdk, context):
            execute_generated_policy(
                code=generated_code, sdk=sdk, context=context, timeout_seconds=90.0,
            )
        runner_boundary_mode = "generated_sandbox"
    else:
        robot_policy = _load_policy(args.robot_policy)
        runner_boundary_mode = "trusted_dev"
    common = dict(
        task_id=args.task, seed=args.seed, artifact_root=args.artifact_root,
        policy_id=args.attention_policy, robot_policy=robot_policy,
        runner_boundary_mode=runner_boundary_mode,
        max_attempts=args.max_attempts, assistance_credits=args.assistance_credits,
        assistance_mode=AssistanceMode(args.assistance_mode),
        human_deadline_seconds=args.human_deadline_seconds,
        token_limit=args.token_limit,
        store_path=args.store_path,
        policy_config=policy_config,
        demo_prior=None if args.demo_prior is None else args.demo_prior.read_text(),
        approved_demo_sha256=args.approved_demo_sha256,
        runtime_variation=None if args.variation is None else json.loads(args.variation.read_text()),
        hypothesis=args.hypothesis,
    )
    if args.suite == "robocasa":
        if args.task not in ROBOCASA_TASKS:
            parser.error("unknown RoboCasa task")
        if not args.confirm_simulator_agent:
            parser.error("RoboCasa actions require --confirm-simulator-agent")
        result = run_robocasa_attention(
            **common,
            backend_factory=lambda: AgentServerActionBackend(
                base_url=args.agent_url, simulator_attested=True,
            ),
            client_factory=lambda task: RobocasaSimClient(
                task, base_url=args.sim_url or "http://127.0.0.1:5500",
            ),
            replay_camera_ws=args.replay_camera_ws,
        )
    else:
        if args.task not in TASKS:
            parser.error("unknown Robosuite task")
        result = run_robosuite_attention(
            **common, camera_name=args.camera_name, record_replay=args.record_replay,
            adapter_factory=lambda task, camera: RobosuiteSimGTBackend(
                task, camera_name=camera,
                service_url=args.sim_url or "http://127.0.0.1:8082",
            ),
        )
    if args.generated_policy_file is not None:
        result["generated_policy"] = {
            "source": str(args.generated_policy_file.resolve()),
            "sha256": hashlib.sha256(generated_code.encode("utf-8")).hexdigest(),
            "boundary": "spawned_process_sdk_rpc",
        }
        (Path(result["artifact_dir"]) / "attention_run.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if result["native_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
