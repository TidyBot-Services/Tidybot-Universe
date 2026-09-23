# AttentionBench v2 perception tracks

The authoritative mode contract is
[`protocol/v2/perception.json`](../benchmarks/attention_harness/protocol/v2/perception.json).
This is additive: the v1 Robosuite non-oracle freeze manifest is untouched.

```text
AttentionHarness -> shared TidyBotSDK.sensors.find_objects()
                         |                  |
                 sim_gt provider       vision provider
                         |                  |
                RoboCasa / Robosuite    RGB-D -> detector -> control frame
                                            |
                                        real robot
```

The v2 object is exactly `{name, position, confidence, source, frame}`.
`position` is in the SDK backend's control frame, in meters. The RoboCasa GT
provider transforms `/perceive` world coordinates into `arm_base`; it drops
segmentation IDs, fixture metadata, evaluator debug, and other simulator
fields. `confidence=1.0` denotes simulator-provided identity/position rather
than calibrated detector confidence. The mode is selected at construction;
there is no vision-to-GT fallback. The router rejects `sim_gt` for
`real_robot`.

The development runner is
`benchmarks.attention_harness.robocasa_native.sim_gt_cli`. It uses the
existing ManiSkill and agent_server services, but never ASPIRE. The harness
owns reset, the native Boolean evaluator, artifacts, Advisor request, and
Memory candidate. A trusted lab policy receives only `(sdk, context)` and
uses `sdk.sensors.find_objects()`, `sdk.arm`, and `sdk.gripper`. Example:

```bash
python -m benchmarks.attention_harness.robocasa_native.sim_gt_cli \
  --task counter_to_sink --seed 101 --perception-mode sim_gt \
  --policy my_lab_policies:counter_to_sink \
  --sim-url http://127.0.0.1:5500 --agent-url http://127.0.0.1:8080 \
  --confirm-simulator-agent
```

The confirmation is mandatory because the current agent_server health API
does **not** attest whether its arm/gripper ports lead to the simulator or
physical hardware. Confirm the endpoint and port mapping before any motion.
The trusted callback is not an untrusted-code sandbox. Thus this runner is
labelled `formal_eligible=false`; do not treat its success as a frozen
benchmark policy score. A generated-policy sandbox, service identity
attestation, timeout enforcement, two-task five-seed chain smoke, and 25/25
development success per task are still required for a formal v2 freeze.

On failure, `--advisor` asks the GLM proxy using a v2 prompt that explicitly
permits agent-visible GT object positions while still excluding evaluator
debug. The answer creates a *candidate* Memory record with
`perception_mode=sim_gt`; it is not auto-trusted. Retrieval accepts only
trusted records that explicitly match the mode, suite, and task. An untagged
or vision-tagged memory cannot silently influence a simulator-GT run.

The real-robot vision path is a separate track. It still needs RGB-D
calibration, detection, coordinate conversion, replay validation, and its
own outcome assessor. Never pool its success rate with simulator-GT or v1
Robosuite non-oracle results.
