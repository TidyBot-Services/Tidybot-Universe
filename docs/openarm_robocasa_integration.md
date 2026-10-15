# OpenArm / original RoboCasa integration

Engineering integration and the **OpenArm left-arm `counter_to_sink`, seed101** capability are accepted. Both the independent UI run `e55f0c8c59ff` and final primary UI run **`e080173341f5`** completed 26/26 real controller actions and achieved unchanged **`native_success=true`**. The final primary run used frozen catalog revision 6 and completed in 205.9/300 seconds. A completed controller action, a completed policy and the native task verdict are separate facts. `counter_to_cab` real scene/SDK/evaluator compatibility passed after sink validation; cabinet task success and right-arm task capability remain separate, unaccepted capabilities.

## Frozen scope and compatibility

The original pre-execution acceptance freeze is `outputs/openarm-robocasa/acceptance_freeze.json` (SHA-256 `ec219c04b8508215ba829e33cb641d296cc00e426ba77938e2a9c46ccdc7f649`). Revisions 2–6 append measured compatibility/control findings and frozen source/policy identities without changing acceptance tolerances. It does not erase the original freeze or earlier failed experiments.

| Component | Real runtime / identity | Conclusion |
| --- | --- | --- |
| Python | `/home/truares/miniconda3/envs/maniskill/bin/python`, 3.11.15 | Same interpreter as accepted OpenArm and original RoboCasa |
| SAPIEN | 3.0.3 | Native PhysX articulation and fixed tendon APIs available |
| ManiSkill | 3.0.0b22 | Original registered kitchen task loads with `robot_uids=none`; an adapter repairs only the missing-agent preload state path |
| Task source | `/home/truares/文档/Tidybot-Universe/sims/robocasa_tasks` | Read-only; original registered task class and `evaluate()` are used |
| OpenArm URDF | `openarm_phase0_recheck/model/compat_v6/robot.urdf` | SHA `ae5f37c7f0c509df81d01229c13f0f624d619e253542cedfb0008aaa6d827456`; unchanged geometry, limits, inertias and SRDF |
| Accepted HTTP foundation | `openarm_phase1/service.py` and its original acceptance config | Original files retained; config SHA `f0120618728e75549e7c0bb7d65bb84c8e6813af0dc97e50b48345cd3490c07f` |
| Integration parameters | `openarm_robocasa/configs/robot_parameters_v5.json` plus paired SHA file | Independent from accepted Phase 0/1 and Franka configs |

No dependency installation or editing of the existing simulator environment is required. Each attempt owns one independent process group and random loopback port, with attempt-local caches and evidence. Original Franka still starts its original simulator and agent service. `robocasa365`, hardware, coordinated dual-arm policies and Advisor/Memory extensions are outside this work.

ManiSkill creation resets global PhysX defaults. The independent kitchen explicitly restores 32 position / 8 velocity iterations, enhanced determinism and one CPU worker before creating the task. Both arms, the actual kitchen collision shapes, the native task objects and SRDF collision exceptions remain enabled. Raising diagnostic solver precision did not cure finger contact instability.

The final independent integration uses native fixed tendon stiffness `1e5`, original mimic coefficients/offsets and explicit tendon damping `20000`. The follower has zero position/velocity servo; execution never mirrors its qpos. Free-space finger servo is `2000/50`; after 12 consecutive actual bilateral contacts with the exact target, the selected driver uses contact servo `6000/500` with original URDF effort limits and a one-time measured-position inset of `0.0004m`. Arm servo remains `300/30`. This contact controller is frozen separately in parameters v5 (SHA `9372e3dba206375a81b7b20564ed8236fb83019cf53fcb164a3aa5adb045c2ea`). It passed the original final error/speed criteria and genuine lift/transfer/release. Earlier damping candidates, convergence failures and slips remain in evidence. Accepted geometry, mass/friction, coupling, installation environment and original Phase 0/1 packages are unchanged.

## Robot identity and startup

`robot_config.RobotSelection` is immutable. Original approved v3 configs imply `Franka/single` only for new validated launches. Unknown historical records acquire no invented identity. New OpenArm configs explicitly contain `OpenArm/left` or `OpenArm/right`; the default UI choice is left. The opposite arm and its gripper reject commands for the lifetime of that process. `/reset` preserves the selected model and side. There is no hot switching or fallback to Franka.

The live catalog retains the original Franka policy/config/graph hashes and adds independent OpenArm engineering profiles and real one-node task graphs. Current files are `launch_configs/counter_to_sink_{left,right}_engineering6.json`; left config SHA is `d141713cb8ff862c0113ec3d6190a620414b568164412540c454a783e8c5d097` and policy SHA is `1746a6d97dba999be6f567a2983e82f98723f1293d3f271a9bb5ce259f18d960`. Development status is distinct from execution/native status. The generic scheduler, trace projection, help/reply/feedback and replay paths are reused. Existing historical runs and frozen experiment packages are not rewritten.

Each new run records robot/config/model/parameter identity. State transitions must retain that metadata. A real early integration run exposed a decode omission; its failing transition test and correction are retained. That earlier run remains `Not recorded` at the run level; its actual trace/service evidence contains the attested OpenArm identity. No retrospective identity backfill is performed.

## Coordinates, installation and observations

| Quantity | Convention |
| --- | --- |
| SDK positions | SAPIEN world XYZ, metres; right-handed; Z up |
| Root pose | `[x,y,z,qw,qx,qy,qz]`; quaternion order is **wxyz** |
| Selected end effector | URDF `openarm_left_hand_tcp` or `openarm_right_hand_tcp`; no Panda tool offset |
| Joint coordinates | Exact loaded `joint_order`; 7 arm radians per side |
| Gripper command | Per-finger displacement in metres, **not total jaw width**; actual loaded range `[0,0.04399999976158142]` |
| RGB-D | Native 640×480 base camera, floating RGB `[0,1]`, depth metres, validity mask |
| Camera calibration | `K`, `T_world_optical`, `T_world_opengl`, `extrinsic_world_optical`; optical axes x right / y down / z forward |
| GT objects | Explicit `sim_gt`, world position and quaternion; no native evaluator state in policy observations |

Mounting is relative to the actual task destination fixture. For sink seed 101 the measured root is approximately `[3.59914475,-1.74979706,0.6,1,0,0,-1.8366e-6]`. The sink mount offset is `[0.55,-0.5,0.6]m`, with fixture yaw plus π/2. The parameter file records a left near-task ready pose initialized only at reset and the unchanged right stow pose. The downward TCP orientation is yaw-aligned by −0.1255308rad to the realized seed101 flat target faces; right uses its own orientation. This is a seed101 strategy/configuration, not a general multi-seed capability claim. URDF analytical FK matched the accepted full FK with zero measured discrepancy in the sampled comparison.

`/observation` returns a single-owner NPZ containing RGB-D/calibration **and** `state_json`; joints, both TCP poses, epoch and physical step are captured without an intervening physics step. The SDK rejects identity/step mismatches. `/camera` is an RGB-only NPZ recording transport with native uint8 pixels, the same identity and physical stamp. It avoids starving the physics owner by repeatedly compressing segmentation/depth for the OpenArm 1 fps recorder; original Franka recording remains 5 fps. It does not resize images. UI live camera identity is checked against the recorded run.

## HTTP and SDK contract

| Endpoint | Operation / rejection |
| --- | --- |
| `GET /health`, `/model`, `/state`, `/collisions` | Read actual loaded model, both arms/grippers, installation and contact state |
| `GET /observation`, `/camera` | Coherent full RGB-D observation / lightweight public RGB recording |
| `POST /perceive {}` | Explicit GT task-object and destination positions |
| `POST /plan {position:[x,y,z]}` | Bounded selected-arm URDF IK and real kitchen collision planning; rejected plans report `reachable=false`, `action_dispatched=false` |
| `POST /actions` | Selected arm 7-joint vector or gripper travel plus epoch; official limits, inactive lock and actual trajectory collision checks |
| `POST /grasp {epoch}` | Close until 12 consecutive real contacts with both selected fingers and the exact target entity, or the safe free-close limit `0.006 m` |
| `GET /actions/<id>` | Actual fixed-step completion/error metrics and contact proof |
| `POST /stop {}` | Priority epoch barrier, cancel active motion and commit bounded current-state hold |
| `POST /reset {}` | Priority barrier and original same-scene native reset; robot initialization is confined to reset |
| `GET /task/info` | Original task/language, realized variation identity and camera names |
| `GET /task/success` | Unchanged native task `evaluate()`; evaluator side only |

SDK gripper open uses the actual loaded upper limit. Close has an explicit contact-hold capability and produces actual contact receipts. Free-space closure completing is not proof of grasp. Policies must check object motion; current diagnostic policies abort when the object does not follow a lift.

Unreachable/read-only planning failures are typed and dispatch no action. Unsupported base motion and rotation deltas raise `CapabilityNotAvailableError`. Invalid inputs, inactive-arm commands, stale epochs, collisions and convergence failures remain explicit errors. SDK cancellation/deadline commits `/stop`; a separate process-owner watchdog handles cancellation, overall deadline and startup failure, followed by TERM/KILL as needed and actual `wait()`/process-group verification.

## Planning and unchanged limits

Planning uses loaded URDF limits, 14 bounded IK seeds, 4 mm position / 0.03 rad orientation qualification and bounded bidirectional RRT. Both arms and 113 actual kitchen collision bodies participated in the audited sink scene. Collision edges sample the SDK's actual segmented per-joint slew at at most 0.02 rad, with a 5 mm non-contact clearance. Only the selected finger links may intentionally contact the exact target; other arm/object/body contacts cause rejection or an actual physical stop. A bilaterally held target is also cloned into the planning scene to check its swept geometry. Clone pose writes occur only in the **separate planning scene**.

Every physical controller action still runs exactly 720 steps at `dt=1/240 s`. Original acceptance limits remain: arm error ≤0.03 rad, finger error ≤0.001 m, final arm speed ≤0.05 rad/s, final finger speed ≤0.005 m/s; inactive arm error ≤0.01 rad. Original effort/velocity limits, 0.5 rad/s arm target slew and 0.02 m/s finger target slew are retained. Harness safety limits are unchanged. Failed convergence is retained as failure; no added settle steps or relaxed tolerances are used to convert it into success.

During execution there are no writes to actual object poses or native success flags. Original task reset initializes object poses; all subsequent interaction comes from PhysX stepping and robot drives.

## Evidence and acceptance

Evidence is under `outputs/openarm-robocasa/`. Failed runs/probes and prior freeze/config revisions are retained. 795 accepted Phase 0/1 files match the pre-edit hashes; original Franka policy/config/graph and the nine initial native-task/model/dependency source pins also match. The final attempt's 30 required source pins match after execution. Every final OpenArm action used exactly 720 physical steps and the original thresholds.

| Requirement | Measured evidence / status |
| --- | --- |
| Before-edit baseline | Harness **669 passed / 10 skipped**, DOM **187/187**; no fresh pre-edit physical Franka native-success baseline |
| New red → green tests | **52 passed**; identity/selection/locking/routing/arms/errors, coherent observation, source/config pins, pure hint preview, actual launch-policy sandbox contract; [index](../outputs/openarm-robocasa/test-red-green-index.json) |
| Complete final regression | Harness **739 passed / 10 skipped**, DOM **205/205**, including prior draft/selection/focus/disconnect/stop/reconnect/replay/submission-versus-adoption assertions; combined-tree counts include concurrent UI work |
| Real lifecycle | `lifecycle-final/checks.json`: **27/27**, all seven cases passed; left/right control, gripper, inactive arm, same-scene reset/epoch, read-only unreachable plan, SDK/owner cancellation and deadlines, actual 500-step fault, group reaping |
| Complete final OpenArm task | Primary UI run **`run:attention-robocasa-counter_to_sink-seed101-e080173341f5`**, launch `ff18efc20b3b4ee2908968c27721ca00`; interface `attested`, policy `completed`, **native true**, **26 completed / 0 failed**, no execution error |
| Physical/native proof | **19,439** qpos/qvel/goal/drive-target samples through step19439, actual object trajectory and contacts; native evaluator ran at step19314; original `PnPCounterToSink.evaluate`; no execution writes to actual object poses/success |
| Actual unchanged tolerances | Maximum final arm error **0.00716933rad**, finger error **0.000941097m**, arm speed **0.00224533rad/s**, finger speed **0.00357938m/s**; inactive arm max qpos deviation **0rad** across this recording |
| Real final video/replay | **170** recorded 640×480 samples, 173.92s capture span, 174.92s encoded playback; release event `+170.504193s` seeks actual sample164 at video **168.381855s**, readyState4; exact saved mapping matches |
| Original Franka comparison | `comparison-franka-3`: same task/seed/kitchen and original approved config/policy; policy **completed**, native **false**, 29 distinct seven-joint/TCP/gripper/base/object samples, **91** native1920×1080 frames, trace/receipts/evaluator/source pins and reaping |
| Cabinet compatibility | `cab-compatibility-2`: original cabinet scene/objects, RGB-D, gripper and physically planned −15mm TCP movement, 112 collision bodies, inactive error **8.94e-7rad**, native evaluator/stop/reap; **passed**, native **false** |
| Main historical preservation | UI reloads kept prior run/launch API objects equal; final database audit: original **412 rows across17 tables unchanged**, only36 new rows from actual failed-before-physics and successful launches |
| UI presentation | Real primary New run → robot/arm → Start; locked OpenArm/Left during execution and replay; actual native-success label; page console errors0; English product controls retained |

Franka uses its original SDK arm-base convention; OpenArm uses the explicit world convention above. The comparison preserves these raw frames and separate API timestamps. Its asynchronous Franka samples are not asserted to share one physical step. Both runs use scene `29f6ddc71995db3e0fd6`; the realized object fingerprints differ and are recorded explicitly. This is a genuine same-task engineering comparison, not a controlled causal performance benchmark.

The original primary UI is available at `http://127.0.0.1:8769/ui/` with final v6 profiles. Start an independent history store with `bash openarm_robocasa/run_ui.sh`; see [startup README](../openarm_robocasa/README.md). The final primary run is also copied byte-for-byte to `outputs/openarm-robocasa/main-ui-final-v6-evidence/`, with its complete file hash manifest. Original trace URIs are retained in that copy.

The reviewable [acceptance checklist](../outputs/openarm-robocasa/acceptance_checklist.json) and [comparison manifest](../outputs/openarm-robocasa/comparison-summary.json) link actual configuration, observations, trajectories, action receipts, frames/video, trace, raw native verdict and stop receipts.

## Retained failures and practical limits

Initial installation collided with a door/wall; the cabinet needed its own collision-free mount offset `[-0.05,-0.9,0.6]m`. Earlier sink grasps lost contact because corner contact rotated the target, or transfer slipped. Alignment and the independent contact controller solved the accepted seed101 case without changing accepted model inertias/friction, force limits, target objects or tolerances. A real prior full run stopped at finger speed **0.0065028m/s > 0.005m/s**; it remains a failed native-false run. Other planning/recording/startup failures and motion evidence are retained.

The first final primary launch (`a693c8378b7848aea92ccfcf43655eb4`) rejected a new error-string `join` call at the unchanged policy-sandbox validator **before physical service execution**. Its genuine failed run remains in history. A new red test reproduced that exact contract failure; the error message was corrected, the allowlist stayed unchanged, revision6 was frozen, and the subsequent primary UI run passed. A separate earlier UI runtime missing a pre-existing dependency also remains in evidence; no credentials or dependency installation were changed.

Public hint preview uses the persisted, locked robot identity. OpenArm supports `AB_CONTROL_V1 grasp_offset_m=0.005;` in **0–0.020m**. Base motion and settle/approach overrides explicitly report unsupported capability; the actual policy rejects them before perception/motion. The HTTP preview test compares full SQLite dumps and leaves the pending request unchanged. No live human Hint was submitted in these physical acceptance runs; shared help/reply/feedback behavior is covered by existing Harness/DOM tests. Neither a preview nor submission claims adoption.

Right-arm SDK/control/lifecycle pass; **right-arm task success remains unaccepted**. Cabinet compatibility passes, but **cabinet transfer strategy, target/destination reachability and native success remain unaccepted**. Multi-seed reliability is not claimed. Profiles remain **`formal_eligible=false`** pending a broader formal stability/admission matrix; this does not change the genuine native task result. The existing GLM diagnosis failed independently in successful UI runs and is displayed separately from native success. Advisor/Memory, hardware, dual-arm collaboration, hot switching and RoboCasa365 are outside this goal.
