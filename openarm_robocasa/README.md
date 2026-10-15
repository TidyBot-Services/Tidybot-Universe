# OpenArm in original RoboCasa

The original `counter_to_sink` seed101 has a verified physical OpenArm **left-arm** native-success run. The opposite arm stays controlled and participates in collision checks. Right-arm control is validated; right-arm task success and cabinet task success are not accepted.

Dependencies are the existing Python3.11.15 / ManiSkill3.0.0b22 / SAPIEN3.0.3 simulator environment. No installation or accepted-model modification is needed. See [integration and coordinates](../docs/openarm_robocasa_integration.md) and `outputs/openarm-robocasa/` for the freeze, retained failures and acceptance evidence.

Start an independent UI without changing the primary history store:

```bash
bash openarm_robocasa/run_ui.sh
# http://127.0.0.1:8795/ui/
```

Optional `ATTENTION_UI_PYTHON`, `ATTENTION_UI_PORT`, `ATTENTION_UI_STORE` and `ATTENTION_UI_ARTIFACTS` override the UI runtime and its own evidence destination. The pinned simulator runtime is selected by the catalog, independently of the UI interpreter. No simulator runs until the operator presses Start.

Choose **RoboCasa → Move to the sink → Franka or OpenArm → Left or Right**. OpenArm defaults to Left. The model and side lock at startup and remain visible during execution and replay. The current OpenArm sink profiles use `launch_configs/counter_to_sink_{left,right}_engineering6.json` and `configs/robot_parameters_v5.json`. Old hashes/configs are retained; stale source or policy hashes reject a new launch explicitly.

For a short cabinet compatibility check in a new evidence directory:

```bash
PYTHONPATH=. /home/truares/miniconda3/envs/maniskill/bin/python -B \
  -m openarm_robocasa.validate_task_compatibility \
  --parameters openarm_robocasa/configs/counter_to_cab_compatibility.json \
  --out outputs/openarm-cab-compatibility-new
```

This checks real kitchen loading, RGB-D, gripper, a planned physical TCP movement, inactive-arm hold, the original evaluator, stop and reaping. It does not run a cabinet transfer strategy or assert cabinet native success.

Interface attestation, policy completion and native task success are separate fields. Integration profiles remain `formal_eligible=false`; the present evidence is not admission to a broader formal experimental matrix. Supported public hints use `AB_CONTROL_V1 grasp_offset_m=0.005;` (0–0.020m). Unsupported base/settle overrides explicitly fail; submission/preview does not prove adoption.
