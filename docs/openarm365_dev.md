# M2 OpenArm365 Dev

This is a Dev extension of `dev_loop`, `AttentionStore` and `ParccClient`; every
session, source version and execution remains `formal_eligible=false`. It does
not create an approval or alter the frozen formal catalog.

The acceptance authority is
`/home/truares/Documents/Codex/2026-10-11/new-chat-2/outputs/openarm-dev-integration/ACCEPTANCE_FROZEN.json`.
The continuation preserved 2,292 uncommitted source files and the binary Git diff
under `continuation-baseline/`. Historical OpenArm SAPIEN grasp/handoff packages
are separate evidence and are not counted as this RoboCasa365 Dev loop.

| Contract | OpenArm365 Dev | Existing PandaOmron Dev |
| --- | --- | --- |
| Task / scene | pick_place_counter_to_sink, seed 101, layout 11, style 34 | Same catalog task/seed/layout/style |
| Robot / arm | OpenArm365, physical left and right | PandaOmron, right |
| Native arm mapping | physical left → native right; physical right → native left | Existing single-arm mapping |
| Public SDK | sensors, left_arm, right_arm, left_gripper, right_gripper, bimanual, base | Existing sensors/arm/gripper/base/torso |
| Translation / rotation | WORLD-frame delta, ≤0.05 m / 0.5 rad per coordinate | Base-frame delta; existing bounds |
| Joint vector | 14 values: physical left joints 1–7, then right 1–7 | No new absolute-joint interface |
| Controller | BASIC composite; JOINT_POSITION kp=1500 by default, torque limits unchanged; reset uses delta zero hold, execution uses absolute goals | Existing catalog controller |
| Gripper | Two independent original parallel grippers, 2 physical joints each; open/close have no parameters | Existing settle_steps API |
| Gripper command | 1 mm inside original [0, 44 mm] limits; measured state never clamped | Existing convention |
| Base | `base.move_delta(dx,dy,dyaw)` in current robot frame; ≤0.20 m norm / 0.3 rad; 2 cm chassis path clearance; uncommanded drift ≤1 mm / 1 mrad | Existing mobile capability |
| Limits | 80 SDK calls, 2,400 control steps, 480 s per attempt | Existing 40-call Dev policy limit |

## 底座停稳与漂移保护

`base.move_delta` 达到 5 mm / 5 mrad 到位范围后，继续发送零底座命令。相邻控制步的底座 x、y、偏航位置差除以控制步时长，三通道速率分别须连续 `BASE_SETTLE_CONSECUTIVE_STEPS = 5` 步不超过 `2e-4 m/s`、`2e-4 m/s`、`2e-4 rad/s`。最多额外等待 `BASE_SETTLE_MAX_STEPS = 60` 步；这些步仍在同一底座动作窗口内，计入 2,400 控制步总预算，期间继续检查 4 mrad 手臂变化上限。未停稳时报公开错误 `Base did not settle within 60 control steps`。

停稳后用当时的底座位置作为 `base_hold` 和回执的 `after_base_qpos`，重新检查 5 mm / 5 mrad 到位误差，包含零命令期间的滑行；超出时仍按原到位失败处理。回执和 journal 的 `base_settle_steps`、`base_settle_displacement` 分别记录等待步数与三通道位置变化。动作窗口外的 1 mm / 1 mrad 漂移守卫保持原判据。

## `ik_path` move_delta

With `ATTN_PLANNER_MODE=ik_path`, `move_delta(dx,dy,dz, rotation_delta)`
targets the requested tool **endpoint pose**. The service checks the endpoint
within 4 mm position and 0.03 rad orientation, then continuously certifies the
actual quintic joint-space path against the geometry. The tool path need not be
straight, including for translation-only calls. The default and explicit
`plan` modes keep their existing routing.

## 可移动物体接触豁免

附着诊断开关 `ATTN_DIAG_ATTACHMENT_DUMP=1` 默认关闭；设置为其它值（包括空字符串）会报错。开启后，每次构造非空附着规划上下文都会在该次运行的 evidence 目录写 `attachment-dump-*.json`。文件包含目标 body 及后代的全部 geom、与目标 geom 相关的所有 MuJoCo 接触（包括正距离接触及法向/六维接触力）、每个目标 geom 到非目标且非机器人 geom 的 5 cm 距离查询及最近 10 项、全局最近 10 项、最终 `support_names` 和各世界形状的纳入/排除原因。距离等于 5 cm 时为查询上限，不能推断更远的精确距离。`overlapping_cover_cells` 当前为 `null`，因为本地没有服务端在起点判定的附着覆盖单元与障碍逐项碰撞结果。该开关只写诊断文件，不改变 `support_names` 判定或规划请求；未设置时不写文件。

`contact_exempt_objects` 默认为 `none`，可由 `ATTN_DIAG_CONTACT_EXEMPT` 覆盖最终值。最终值为 `movable_objects` 时，物理监控允许机器人手指、手掌、连杆或车体与任务 `obj_body_id` 中具有独立 free joint 的物体及其子 body 接触，包括目标物、`distr_counter` 和 `distr_sink`。机器人碰台面、柜子、咖啡机、墙、地板、水槽等固定件仍会中止；机器人自碰撞和其他物理监控判据保持原样。底座路径预检查继续使用完整世界。

`native-actions.jsonl` 的 `contact_exemption` 事件在最终豁免开启时写 `physical_monitor_allows_movable_contacts: true`。已有 `movable_contact_begin/end` 事件仍记录接触开始和结束，沿用每动作 200 条上限。`native-physics.jsonl` 仅在豁免开启且该步有放行接触时写 `expected_movable_contacts`，每项为 `[机器人 geom 名, 物体 geom 名, 距离 m]`。默认关闭时物理日志字段和原接触失败规则不变。

手爪双指的实测对称误差默认上限仍为 1 mm。仅当最终豁免值为 `movable_objects` 且该物理步该侧手指 geom 与可移动物体实际接触时，该侧上限改为 `GRIPPER_MIMIC_CONTACT_BOUND_M = 0.010` m（10 mm）；另一侧、手掌接触、无接触和豁免关闭时仍为 1 mm。单指总行程为 44 mm，一侧先接触物体可能被顶开，因此允许最多 10 mm 的暂态不对称，但不允许整个行程失配。放宽生效的 `native-physics.jsonl` 行额外记录逐侧 `mimic_error_m`、`mimic_bound_m`；默认行字段集合不变。位置、速度、限位和其他碰撞监控均维持原判据。

The real reset receipts verify a 19-dimensional native composite vector: both
7-joint arms, independent gripper channels and a zero-held 3-channel base at reset.
Policy code cannot access that raw
vector. The executor uses actual native controller split indices and rejects
an arm dimension other than seven. Scene assets, official source commits, model
identity, controller/reset targets, adapter hashes and the independent official
V2 deployment lock are bound in the session config. The old OSC reset targets
were moved inside original joint limits; the task, assets and limit thresholds
were not changed. See the retained prerequisite failures and `motion-03/`.

All trajectories come from the authenticated official cuRoboV2 deployment in
`deployment-02/`, including full chassis geometry and the current MuJoCo world.
The legacy raw-action service is not enabled. A planning rejection is a public
failure; infrastructure failures or requests without complete receipts retain
reservations and require review. A stopped/restarted session cannot resend an
already claimed job. Virtualenv executable paths retain their symlink invocation
path so the base Conda interpreter cannot silently replace the locked runtime.

Each version preserves the complete model input/output and provider response,
source, SHA256, reason, unified diff and actual provider usage. Only public
observations, approved SDK action events, public errors and execution state feed
the next request. The native evaluator is called independently after policy
execution and saved separately for display. It does not select versions or stop
iterations. Each attempt creates a new native process/environment, calls reset,
and saves a distinct episode/reset receipt before executing policy code. Native
environment RNG, global NumPy/Python RNG and Python hash seed are fixed. The two
accepted episodes have identical raw scene XML bytes and initial robot/object
observations; distinct episode IDs prove separate resets.

Use **New run** → Benchmark **RoboCasa365** → Task **Pick Place: Counter → Sink**
→ Robot **OpenArm365** → Execution mode **Both arms** → **Start run**. All new
runs use Dev automatic iteration; there is no Frozen/Dev channel selector. The
existing Run settings / Resource budgets / Time limits form is retained. Set
maximum iterations, tokens and the session time limit; the per-attempt execution
deadline and total SDK allowance follow the selected robot. Seed 101 and the
robot/config stay fixed throughout each session. Human assistance controls are
omitted because Dev does not wait for human replies.

**PandaOmron / Right only** remains available where its catalog profile is loaded.
Only published Dev profiles are offered; frozen-only tasks and configurations are
not converted into Dev support. Historical runs remain accessible. Copying a
historical configuration opens this same form and explicitly reports an
unavailable robot/task/seed instead of claiming it was copied. The five existing
page modules and left navigation remain.
Execution history exposes immutable source, diff, requests, trace and recording;
unknown executions show review evidence without refunding their reservation.

Evidence is under `real-ui/`. The first session
`dev-13dc2e187f73469fad5ef78d` generated real GLM v1 but failed before simulator
startup because of the virtualenv symlink bug. It remains `needs_review` and is
not counted as loop acceptance. Session `dev-64077de12cc44f4a87fc57bc` obtained
real automatic revision but lacked a complete recording/runtime proof. Session
`dev-71fbcbebe0884aafb3e359e6` saved both videos but failed the full XML audit:
upstream collision-debug colors used unseeded global RNG. Their failed audits
remain unchanged.

The final UI-started session `dev-c099e24c66bf4eeabb4c037c` passes the independent
real-loop audit and supplemental raw/HTTP/ledger audit. GLM authored v1, received
its real public planning failure and complete source, authored changed v2, and
the native MuJoCo simulator executed v2. Both attempts opened the actual left
gripper (500 physics steps / 20 control steps each) before arm planning was
rejected by authenticated cuRoboV2. The loop passes; both native task results
are **false**. This does not demonstrate successful picking or arm motion in
these Dev attempts. Both-arm motion capability is separate prerequisite proof.

The session ends `budget_exhausted` after two versions, with 2 real provider
calls, 7,982 tokens, 2 executions, 8 SDK calls, 270.522 seconds of execution and
305.532 seconds of session wall time. All jobs settle and no reservation or
live owner remains. Each version has a saved 21-frame 1920×1080 real recording.
The final config SHA256 is
`27e7bd9156e10a359d255ab6dca5c827fe18a68bcaad452ca3a2a1d94a47ff42`.

`ACCEPTANCE_RESULT.json` maps the six requirements to evidence and distinguishes
implementation tests from real execution. An actual UI-service restart during
the preceding live session retained the same owner and job identities without
redispatch. Stop/cancellation and configuration-tamper coverage are controlled
tests; no moving-arm operator-stop experiment is claimed by this Dev package.
The frozen delivery contains exact source snapshots, scoped changes from the
continuation baseline, model requests/responses, videos, reset/physics/native
records, a consistent SQLite backup, failure history and SHA256 manifests.

For reproduction, create an AttentionStore and artifact directory, then run:

```bash
/home/truares/.cache/tidybot-attention/venv/bin/python -B -u \
  -m benchmarks.attention_harness.ui_server \
  --store /absolute/path/attention.sqlite3 \
  --artifact-root /absolute/path/runs \
  --run-catalog openarm_bimanual/launch_configs/main-ui-fixed-curobo-v15/catalog.json \
  --port 8800
```

The installed official simulator runtime/assets and `deployment-02` lock are
required. Credentials are read by the existing PARCC client; they are not copied
into policy namespaces or evidence. Source/config drift fails closed. Use a fresh
session request key for a new experiment; do not edit historical session locks,
versions or held claims. Reproduction incurs new model and simulator usage.

Contract validation:

```bash
python -B -m pytest -q benchmarks/attention_harness/tests/test_openarm_dev_loop.py \
  benchmarks/attention_harness/tests/test_dev_loop.py \
  benchmarks/attention_harness/tests/test_ui_server.py
```

The final non-overlapping test groups total 206 passed: 71 Dev/OpenArm/UI,
121 formal/store/Panda/365/PARCC, 11 installed adapter and 3 frozen-approval
boundary checks. Panda's branch, selection and tests pass; this work does not
claim a fresh real Panda simulator run. The inherited real Panda Dev loop and
its current retired-service limitations are recorded separately in delivery.

The retained logs distinguish missing test/service paths from real regressions.
Original formal-source admission failures already existed before this extension;
the formal freeze guards remain enforced and original approval bytes are not
rewritten to make an old catalog appear approved for changed source.

<!-- dev-shared-workspace-followup -->
## Dev 四模块工作台恢复：展示范围验收完成，实际8769已生效

当前主工程 `feature/attention-native-robosuite`。此前仅统一了 New run，运行后仍使用简化 `renderDev`；原“保留五模块”的记录只证明模块名称/分区存在，未证明 Dev 使用原样式组件。本轮将 Dev 只读投影接回共享 `render`，恢复参数卡片、图形进度、录像与动作时间轴、紧凑请求/结果和真实资源卡片；版本理由/源码默认折叠。OpenArm365 显示 Both arms/Stationary/真实模型，不再套用旧人工协助/GPU预算。

保存的 bundle/trace 按 run/attempt/config/policy/version/SHA256 严格绑定；原生任务结果独立展示，历史失败仍为 false。缺少实时帧生产器时明确提示；本轮只接现有历史录像，不宣称新增实时画面。Stop/Resume 仅使用既有 Dev API。未修改执行器、正式批准/旧锁，也未由此工作创建真实模型或模拟器执行。用户原 dev-2b1c45586fca41c59abf9e7a 自行执行至预算耗尽，未被本轮停止或重发。

验证：纯投影 14 PASS；JS语法和diff检查通过；Cua 在显式标记 ISOLATED UI TEST 的8891只读预览检查1280/1840桌面、390手机无溢出、三段统一New run取消、模型生成/待核查/停止等待/真实终态、v1真实RGB回放/SDK时间轴、非零播放位置跨轮询与重载保留。Stop/Resume端点检查仅在fixture，不冒充真实执行器停止验收；未重跑完整浏览器自动化套件。

**实际发布已通过**：用户授权交接后，「接通 OpenArm 双臂执行链」将补丁合并至当前发布；8769实际运行deployment-22，GET /ui/live.js SHA256 `80feba721cc730334c13452335492218fbf6442a4ee2b26e4463ebc56db9dca7` 与本任务候选逐字节相同。Cua在真实8769原Dev链接验证原样式/Both arms、v1真实RGB与5条SDK事件/native false、非零1.47s回放位置跨轮询/重载保留且不自动播放、390/1280无横向溢出、New run打开与Cancel，8项实际页面检查均PASS。主服务发布由另一任务完成，本任务未直接重启8769或操作真实会话Stop/Resume。展示范围验收完成，不代表双臂执行链Gate A–F或原生任务成功。

证据：`outputs/ui-dev-shared-workspace/ACCEPTANCE_FROZEN.json`、`projection-tests.log`、`UI_CHECKS.json`、`HANDOFF.md`、`RESULT.json`、`PRODUCTION_RELEASE.json`、`PRODUCTION_UI_CHECKS.json`及实际页面截图。未来发布沿用已合并Dev分支；双臂执行链由另一任务继续其固定验收包。
<!-- /dev-shared-workspace-followup -->
