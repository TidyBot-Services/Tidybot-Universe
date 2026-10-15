"""Immutable startup identity for the original RoboCasa simulator only."""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RobotSelection:
    model: str
    execution_arm: str

    def __post_init__(self):
        if not isinstance(self.model, str) or not isinstance(self.execution_arm, str):
            raise ValueError("robot model and execution arm must be strings")
        arms = {"Franka": {"single"}, "OpenArm": {"left", "right", "both"}}
        if self.execution_arm not in arms.get(self.model, set()):
            raise ValueError("unsupported robot model or execution arm")

    @property
    def inactive_arm(self) -> str | None:
        if self.model == "Franka" or self.execution_arm == "both":
            return None
        return "right" if self.execution_arm == "left" else "left"

    def artifact(self) -> dict[str, str]:
        return {"model": self.model, "execution_arm": self.execution_arm}


def robot_selection(config: dict[str, Any]) -> RobotSelection:
    if config.get("suite") != "robocasa":
        raise ValueError("robot selection is supported only for original RoboCasa")
    if "robot" not in config:
        if config.get("schema_version") == "attentionbench.robocasa-formal-config.v3":
            return RobotSelection("Franka", "single")
        raise ValueError("robot identity is not recorded")
    robot = config["robot"]
    if not isinstance(robot, dict) or "model" not in robot:
        raise ValueError("robot configuration requires a model")
    model = robot["model"]
    if robot.get("execution_arm")=="both" and config.get("schema_version")not in {"attentionbench.robocasa-openarm-bimanual-config.v1","attentionbench.robocasa-openarm-bimanual-config.v2"}:
        raise ValueError("Both arms requires its independent bimanual config schema")
    return RobotSelection(model, robot.get("execution_arm", "left" if model == "OpenArm" else "single"))


def robot_run_configuration(config, config_sha256):
    result={'robot':robot_selection(config).artifact(),'config_sha256':config_sha256,
        'model_sha256':config.get('model_sha256'),
        'parameters_sha256':config.get('robot_parameters',{}).get('sha256'),
        'sim_runtime':config.get('sim_runtime')}
    if config.get('schema_version') in {'attentionbench.robocasa-openarm-mobile-config.v1','attentionbench.robocasa-openarm-bimanual-config.v1'}:
        result['base']={'model':'Tidyverse planar chassis','capability':'mobile_base_joint_drive'}
    elif config.get('schema_version') in {'attentionbench.robocasa-openarm-formal-config.v1','attentionbench.robocasa-openarm-formal-config.v2'}:result['base_status']='fixed'
    if config.get('schema_version')=='attentionbench.robocasa-openarm-bimanual-config.v2':result['base_status']='fixed'
    if 'v2_deployment' in config:result['v2_deployment']=config['v2_deployment']
    return result
