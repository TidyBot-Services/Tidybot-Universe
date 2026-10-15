# Mobile OpenArm with native display cameras

The accepted engineering binding is `launch_configs/catalog.json` (byte-identical to candidate5). Its internal profile ID retains candidate5 for provenance. The global live catalog selects this exact profile first for new OpenArm left-arm counter_to_sink seed101 runs. Original Franka and fixed OpenArm approved configuration/policy/DAG files remain unchanged. Historical recordings retain their original resolution and robot identity.

Accepted run: `run:attention-robocasa-counter_to_sink-seed101-16431d7fb225`. 31/31 physical actions completed, native task evaluator success true, no unsafe attempts, no human Hint submissions. This is single-seed engineering acceptance; right-arm task success, other tasks, multi-seed experiment admission and hardware are outside its claim.

## Start the independent UI

Use `./openarm_mobile/run_ui.sh` from this repository. Default address is http://127.0.0.1:8796/ui/ . Choose a free port with ATTENTION_UI_PORT if an existing UI is already using it. ATTENTION_UI_STORE and ATTENTION_UI_ARTIFACTS select a separate store and artifact directory. The simulator remains the pinned ManiSkill Python environment; the UI uses the existing Harness Python environment. No environment installation is required.

## Model and physical control

`model/identity.json` pins OpenArm compat_v6 and the local TidyBot-Services/maniskill-tidyverse URDF source at revision fbbac3a0e105001f46a0a63b61ebda51ac771980, including its locally modified source SHA. The chassis is the native simulator planar X/Y/Yaw PhysX articulation: force-limited prismatic X/Y and revolute Z drives, mass 50 kg, diagonal inertia 1/1/0.5 kg m², COM [-0.198,0,0.2] m, collision box [0.733,0.523,0.606] m at [-0.198,0,0.309] m. OpenArm body is fixed to base_link at Z=0.6 m. Original arm joint, link, inertia and collision geometry are preserved.

This is an actual simulated drive chassis, with dynamic collision feedback. It is a planar joint-drive physical abstraction, not a wheel/ground traction or hardware implementation. Execution never teleports the robot root. Native initialization/reset and separate collision-planning/render replicas have their own pose assignment routines.

Base coordinates are X forward, Y left, Z up; articulation X/Y live in the initial mount frame, with yaw around Z. SDK local translation rotates using the actual current base world matrix; world commands map into the initial mount frame. SDK `sdk.base.move_delta(dx,dy,dtheta,frame="local")` uses epoch-bound POST /base/actions and awaits actual completion, with unchanged max 0.3 m and 0.5 rad per call. GET /state reports qpos/qvel/goals/world matrices for base and both arms. POST /stop holds current drives and confirms measured base velocity <=0.005; reset changes epoch and rejects stale commands. The selected arm can execute; the other arm remains held and collision-active.

## Cameras and recordings

Chest first person is mounted to openarm_body_link0; Left wrist (or Right wrist in a right-arm service) is mounted to the executing link7. External overview is fixed in the scene. All three are native SAPIEN 1920×1080 cameras. The original calibrated base_camera RGB-D remains 640×480 and retains its original implementation and policy protocol.

Actual loaded collision/visual geometry, original material textures and actual entity poses are exported to an isolated NVIDIA rendering process. It steps no physics. Control publishes snapshots into a bounded queue of one with nonblocking put_nowait and drops overloaded snapshots. Display failure is explicit; it does not stall control. Render recording is disk-bounded and VFR-encoded after the owned controller exits.

Each three-camera batch shares the physical monotonic timestamp, wall-clock capture time, step and epoch; sample records include camera K/world transform, rendering latency, CPU seconds and maximum RSS. The acceptance recording has 618 actual frames per view, 3.7265 actual capture FPS, a 165.57-second source span and a 2.765-second maximum capture gap. Requested 10 FPS and the 25 Hz encoded time base are not reported as sampling rates. VFR MP4 contains one final held frame (619 encoded frames), with identical PTS across all views and <=40 ms time-base quantization. Camera switch restores media time, playback rate/state and selected timeline action once the selected video is ready. Browser loading may temporarily pause the media while metadata is fetched.

## Verification and limitations

Acceptance artifacts are under the projectless chat outputs/openarm-mobile directory: frozen acceptance/baseline, red/green and complete Harness/DOM logs, seven physical lifecycle cases, actual UI screenshots/Computer Use observations, three videos, native evaluator receipt, resource/performance data and source copies. Failed framing and strict gripper-convergence trials are retained with their original results.

The native outcome and execution status are independent from optional diagnostic completion. Parcc/GLM diagnosis lacks credentials in the acceptance Harness and reports Diagnostic failed; native task success is true. Submitted Hint, execution association, action changes and native task outcome retain distinct UI semantics. No Hint was submitted during physical acceptance.

All source locks fail closed. Old fixed OpenArm approved configs pin their historical shared Harness sources; if the shared sources no longer match, a fresh launch with those old configs is rejected rather than silently re-approved. Their historical sources/records and approved files are retained. The new mobile default has the complete current source lock.
