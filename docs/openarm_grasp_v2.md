# OpenArm dual gripper grasp v2

Status: partially validated engineering candidate. `formal_eligible=false`.
Real authenticated V2 handoff in either role direction is **UNVERIFIED**.

`openarm_bimanual/grasp.py` owns both grippers, persistent holding, common holding,
release permission and ownership. `grasp_feedback.py` reads the original SAPIEN
scene; the existing native joint drives apply targets. No object constraints,
pose writes, joint-state writes, model/tendon edits or collision exemptions are
introduced. The old bimanual runtime remains disabled by `retired_entry`; the
fixed authenticated V2 deployment has no declared `both` branch. This change does
not connect that external planner dependency or enable its legacy alternatives.

## Protocol and units

`POST /grasp` requires `schema="openarm.grasp/2"`, `arm`, `epoch`, a bounded
`request_id`, `mode="acquire"|"position"`, and `drive_force_limit_n`.
Specify exactly one of `finger_displacement_m` or `jaw_gap_m`. Acquisition also
requires the designated native `target_id` and `desired_contact_force_n`.

- Finger displacement is each driver's prismatic travel in metres; the existing
  native mimic couples its follower. The follower has zero drive stiffness.
- Jaw gap is the spacing of the opposing **collision mesh bounding planes**
  along the native jaw axis, derived from loaded mesh scale and URDF origins.
  `gap = zero_gap + q_driver + q_follower`; nominal equal-finger displacement is
  `(gap-zero_gap)/2`. It is not the separation of the joint origins or a tactile
  aperture sensor. Both finger q/v and the geometry provenance are recorded.
- Desired contact force is normal contact load **per finger**, in newtons.
  Configured driver cap is a separate limit. The native model currently specifies
  333 N; convenience close uses an experimental 2 N/finger target and 20 N cap.
  These experimental settings are not a recommended grasp force and have not
  passed loaded handoff validation. Coupled target load `2*desired <= cap` is
  checked; all commands remain below loaded official limits.
- Use the actual reported upper displacement (currently
  `0.04399999976158142` m) rather than a rounded decimal above the loaded limit.

Example for an already attested dual host:

```python
robot_sdk.right_gripper.grasp(
    finger_displacement_m=0.006,
    desired_contact_force_n=2.0,
    drive_force_limit_n=20.0,
    request_id="receive-001",
)
```

The SDK obtains target identity and epoch from its attested service. Repeating
an identical request id returns the same action; changed arguments conflict.
A new acquisition while holding preserves the existing contract and target,
without another closing ratchet. Raw `/actions` finger commands are rejected.
Historical v1 receipts remain v1 evidence; they are not imported as v2 passes.

The SDK verifies the receipt's schema, epoch, side, action id and exact v2 request.
Acquisition requires both a completed certificate and current stable holding of
the designated target with the same force contract. Position completion requires
the matching live position command and false grasped/holding flags. Missing,
historical or contradictory evidence invokes Stop. Cancel and timeout retain their
existing exception types, including cancellation during the final state query.

## Confirmation, persistence and transfer

Acquisition requires native designated-target positive bilateral contact, actual
finger speed <=0.005 m/s, mimic error <=1 mm, and a 12-step stable window. The
window also checks finger movement <=0.2 mm, object/TCP motion <=0.2 mm/0.002 rad,
and raw normal forces within 50–150% of the desired load with bounded variation.
The original 4 mm/0.03 rad fixed relative-pose, 1 mm tracking, limit and full-group
720-step completion gates remain. Retargeting only generates commands; it is
never the grasp certificate. Final action completion requires a current stable
certificate and the original full-group position/velocity gates.

Action `status`, `grasped`, `holding`, and `ownership` are distinct. A running
action may establish holding before step720; its receipt still says `running`.
The continuous force outer loop keeps running after that action returns.
`legacy_position_completion` and `legacy_full_group_completion` record original
position/velocity comparisons separately from v2 grip success.

The handoff sequence is donor currently stable → receiver acquisition → stable common
holding → release permission → physical donor opening → receiver independent
holding for >=12 steps → ownership transfer. Both roles are supported by the
same coordinator. Release requires current stability and completed acquisition;
direct position commands to either participant are otherwise rejected. A release
request id belongs to exactly one current handoff release action. Identical retries
return that action's current receipt without changing targets, rebinding the native
action or restarting its budget. Historical open/grasp ids and ids from another
handoff conflict. The donor keeps ownership throughout release; its live holding
state becomes false on opening, and ownership changes only after the independent
receiver window. Cancel/Stop terminal events carry the same status as their receipts.
During release and the independent window, fresh grasp requests on either side
are rejected until ownership confirmation; cached identical retries remain
read-only. Handoff begin also obeys the same active-action barrier as the runtime.
SDK handoff waits for
ownership confirmation after the release action. Contact loss, slip, bad feedback,
excessive holding velocity/force or failed identity invokes the existing Stop
callback even with no active action, invalidates the epoch and suspends the force
loop. Stop commits bounded native drive targets; measured rest is independently
checked rather than assumed from a stop receipt.
Feedback boolean flags and epochs are type checked; numeric joint/force vectors
reject boolean or nonnumeric values before they can become evidence. Only an
explicit false identity flag is classified as identity failure; malformed identity
feedback is classified as invalid feedback. Both invoke the same native Stop.

The SDK binds handoff success to the same roles, epoch, group and release action,
matching controller/state ownership, current stable receiver holding, released
donor state and the complete independent window. `phase=completed` alone is
insufficient. Handoff POSTs require the same authenticated V2 owner gate as grasp.

## Feedback provenance

Verified installed build: SAPIEN3.0.3. Normal contact estimate is
`sum(abs(J dot unit_normal))/dt`, dt=1/240 s, J in N*s, estimate in N. All solver
impulse points are counted, including positive-separation points inside the
contact margin. Native bilateral contact identification still uses its unchanged
nonpositive-separation/positive-impulse predicate. This does not widen collision
or contact exemptions. EMA alpha=.25 is used for control only; raw contact is
required by the safety gate.

An isolated native 1 kg static-weight probe measures 9.809062 N against 9.81 N,
relative error0.00956%. The first attempt discarded margin points and measured
6.615626 N; that failed calibration is retained. This checks contact-force units
and accumulation, not motor-force sensing or loaded-grasp performance.

There is no verified direct drive-force measurement on this build.
`measured_drive_force_n=null`. Applied qf is passive compensation, configured
force_limit is a cap, and incoming joint forces are constraint reactions. None is
reported as measured motor force; no PD estimate is used as sensor evidence.
The [SAPIEN contact documentation](https://sapien.ucsd.edu/docs/2.2/tutorial/basic/contact.html)
explains impulse/time conversion; the installed v3 probe supplies the actual
build verification.

## Evidence and remaining acceptance

Evidence root: `/home/truares/桌面/openarm-grasp-replacement-20261011`.
Freeze and original copies are in `freeze/` and `baseline/`. Failures are retained.
The original scene/object ids match inherited Stage1; all121 original native task
Python files match the locked deployed copy. No historical native failures or
successes are changed.

| Package | Verified level | Remaining |
|---|---|---|
| A | Unit/adapter contracts, caps, units, isolation, retired-entry rejection | Real authenticated dual service/SDK binding |
| B | Native dual kitchen unloaded close/open, 720steps +240 hold per side | None for this gripper-only package |
| C | Both-role coordinator unit sequence only | All real moving handoff stages in both directions |
| D | Native empty-left/right, cancel, Stop, feedback-valid/identity-failure boundary injection and measured rest; remaining cases unit-tested | Real wrong-object/single-finger/contact-loss/slip/mechanical-stop and actual V2 runtime identity failure in the moving chain |
| E | Protected model/task/evaluator/contact hashes, no binding/state-write paths | Holder/attachment agreement throughout real moving handoff |

The physical probes use the original dual-arm native kitchen, counter_to_sink,
seed101, objects and tolerances. They issue only gripper commands. Their request
logs are explicitly **in-process component** records, not authenticated HTTP/SDK
acceptance. Native evaluation remains false because these are not task-completion
runs. `delivery/RUNBOOK.sh` reproduces tests and physical probes into fresh output
directories. C and the real-chain portions of A/D/E must pass before goal completion.

Revision r2 freezes coordinator corrections separately in `revisions/r2/`.
Its red regression logs preserve the pre-fix failures. Prior `physics/candidate-*`
and `delivery/` snapshots remain historical evidence for their exact source;
the r2 ledger and fresh physical probes bind the revised candidate. The separate
MuJoCo OpenArm365 execution prerequisite uses another task/model environment and
does not establish the frozen SAPIEN dual-arm execution dependency or C acceptance.

Revision r3 freezes SDK receipt/identity corrections in `revisions/r3/`. Its
pre-fix regression failures and synthetic transport request/response traces are
explicitly separate from authenticated SDK evidence. Native controller, feedback
adapter, runtime, validators and scene code are unchanged from r2; that exact
physics evidence is reused for B and the six native component negatives. It does
not verify the revised SDK against a real dual service or establish C.

Revision r4 freezes the release-window guard and strict feedback corrections in
`revisions/r4/`, with fresh native gripper-only validation bound to the revised
controller. The acceptance ledger continues to require real authenticated moving
handoff and the remaining A/D/E evidence before completion.
