# TidyBot AttentionHarness: native Robosuite path

This package owns the service client adapter, runner, seed policy, artifacts,
timeout, and success-result envelope. It never imports the simulator or
MuJoCo directly. The peer `robosuite_sim` process owns the official Robosuite
environment and native evaluator; another benchmark harness is not a runtime
dependency.

`NativeRobotSDK` is the code-execution boundary. It exposes `sensors`, `arm`,
and `gripper`; its position controller sends actions to the service-owned local
OSC controller, so basic motion does not require PyRoKi.

Frozen tasks:

- `cube_lift` -> Robosuite `Lift`
- `cube_stack` -> Robosuite `Stack`

Setup and smoke test:

```bash
./benchmarks/attention_harness/setup_env.sh
~/.cache/tidybot-attention/venv/bin/python -m pytest benchmarks/attention_harness/tests
MUJOCO_GL=egl TIDYBOT_ROBOSUITE_INTEGRATION=1 \
  ~/.cache/tidybot-attention/venv/bin/python -m pytest \
  benchmarks/attention_harness/tests/test_native_integration.py
```

Run one episode:

```bash
MUJOCO_GL=egl ~/.cache/tidybot-attention/venv/bin/python -m \
  benchmarks.attention_harness.runner \
  --task cube_lift --seed 101 --policy reference
```

The runner starts and stops a local `robosuite_sim` process by default. To use
an already-running service instead:

```bash
python -m robosuite_sim --port 8082
python -m benchmarks.attention_harness.runner \
  --service-url http://127.0.0.1:8082 \
  --task cube_lift --seed 101 --policy reference
```

The reference policy runs inside `robosuite_sim`, where it may read privileged
object positions solely to prove the task/controller/evaluator chain is
solvable. Those positions never cross the service boundary. Model policies
receive only camera and robot proprioception.

## PARCC development smoke (D4/D5)

The model path is deliberately restricted to development seeds `101-125`.
`parcc/GLM` writes one SDK program, invalid programs may receive at most two
validation-feedback retries, and the accepted program runs in a credential-free
subprocess behind an AST capability gate. `parcc/Qwen` reviews the public before
and after camera images. Its review is diagnostic; only Robosuite native success
is authoritative.

Use the protected key wrapper so the credential is never placed on a command
line or in an artifact:

```bash
MUJOCO_GL=egl ~/bin/with-litellm.sh \
  ~/.cache/tidybot-attention/venv/bin/python -m \
  benchmarks.attention_harness.runner \
  --task cube_lift --seed 101 --policy parcc
```

Each model episode additionally records `developer_request.json`,
`developer_response.json`, `generated_policy.py`, `sandbox_worker.json`,
`execution.json`, and `evaluator.json`. A Qwen timeout is recorded but cannot
change or erase the native evaluator result.

## D6 evaluator parity

The legacy ASPIRE wrapper and TidyBot-native service intentionally have
different reset, controller, settle, and Stack sampling behavior. D6 therefore
freezes evaluator parity only; it does not claim observation or trajectory
parity. The exporter compares the normalized native predicate source and 30
behavioral decisions over development seeds 101--105.

```bash
ASPIRE_SIM_ROOT=/path/to/ASPIRE/aspire/sim \
  ./benchmarks/attention_harness/run_d6_parity.sh
```

The strict gate rejects missing or duplicate `(task_id, seed, probe_id)` rows,
non-Boolean decisions, version differences, predicate-source differences, or
agreement below 100%.

## D7 protocol freeze

Create and verify the deterministic manifest:

```bash
python -m benchmarks.attention_harness.freeze create
python -m benchmarks.attention_harness.freeze verify
```

The current manifest freezes the native harness contract, D6 evidence, and
privileged reference policy. It deliberately reports `heldout_ready: false`:
the D4/D5 programs are connectivity probes, not frozen task-solving model
policies. Consequently this command must fail closed:

```bash
python -m benchmarks.attention_harness.freeze verify --require-heldout-ready
```
