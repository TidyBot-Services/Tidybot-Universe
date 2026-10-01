"""Identity binding for separate, predeclared RoboCasa Safety controls.

The station lives at artifact_root.parent. Its identity must use that
directory's name, just as the ordinary Attention runner does. Keep the
production runner, station checks and Safety monitor unchanged.
"""

from pathlib import Path

from ..formal_runner_boundary import FormalRunRequest


def controlled_negative_request(*, run_dir: Path, policy_path: Path,
                                policy_sha256: str, config_path: Path,
                                config_sha256: str,
                                deadline_seconds: float = 120,
                                attention_input: dict | None = None) -> FormalRunRequest:
    run_dir = run_dir.resolve()
    if not run_dir.name.startswith("attention-robocasa-safety-negative-"):
        raise ValueError("controlled negative requires a dedicated Attention run directory")
    request = FormalRunRequest(
        suite="robocasa", task_id="counter_to_sink", seed=101,
        policy_code_path=policy_path, policy_sha256=policy_sha256,
        config_path=config_path, config_sha256=config_sha256,
        artifact_root=run_dir / "attempts",
        overall_deadline_seconds=deadline_seconds,
        run_id=f"run:{run_dir.name}", attempt_id=f"attempt:{run_dir.name}:0",
        attention_input=attention_input,
    )
    request.validate()
    return request
