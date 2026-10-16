# OpenArm365 formal runner：阶段 1 差距清单（2026-10-16，只读审计）

目的：让 8769 上的 OpenArm365（RoboCasa365，双臂，cuRobo V2）能被 formal 调度器（`sim_gt_attention_run` / `formal_attention_run`）驱动，使请求机制 v2、Memory、GPU 计量等已有工作适用于它。本文只列差距，没有改任何代码，没有启动模拟器。

## 1. 结论

| 判断 | 依据 |
|---|---|
| 缺的是“接线和格式”，不是“重写 runner” | `openarm365_dev_v2/runner.py::OpenArm365DevRunnerV2` 已经实现 formal 的 `execute(FormalRunRequest)`，并已返回 `attentionbench.formal-runner-result.v1`（含 `sandbox` 四项、`native_evaluator`、`trace/safety/sandbox_receipt/native_result` 四个产物），形状与 `run_with_formal_boundary` 的检查一致 |
| 但有 4 处会直接被拒或语义不通 | entry_lock 被拒绝；没有 `attention_input`（求助内容进不了策略）；预算上限与 formal 不同；配置 schema 不同 |
| 当前 8769 上两个 OpenArm365 formal 入口是死的 | `robocasa365/service.py:81` 的 `start()` 无条件抛 `IdentityError`（已用空目录实测，未启动任何进程），入口 `openarm365-pick-place-counter-to-sink-dev101` / `openarm365-open-cabinet-dev101` 都走这条路径 |

## 2. 接口（已核对）

调度器与模拟器之间只隔一个函数：`runner.execute(request) -> dict`（`formal_runner_boundary.py`）。
`run_with_formal_boundary` 对返回值逐项核对：suite/task/seed/policy_sha256/config_sha256；有 entry_lock 时 result 与 trace 都必须原样带回；`sandbox` 四项为 true；`native_evaluator.native_success` 与 `native_success` 一致；`trace/safety/sandbox_receipt/native_result` 四个产物在 run 根目录内且 SHA 对；`sdk_call_budget.limit == request.sdk_call_limit`；status 在 trace 与 native_result 中一致。

## 3. 差距表

分类：**复用** = 已满足；**小改** = 几行到几十行；**中等** = 需要设计加测试；**待定** = 取决于阶段 4。

| # | formal 的要求（参照 `robocasa365/formal_runner.py`） | Dev v2 runner 现状（`openarm365_dev_v2/runner.py`） | 分类 |
|---|---|---|---|
| G1 | 接受已批准入口锁：`request.entry_lock` 原样写入 trace 和 result | 第一行 `if request.entry_lock is not None: raise ValueError('Dev-only runner cannot claim an approved formal entry')`；binding 里 `entry_lock=None` | 小改 |
| G2 | 把 `public_attention_input(request.attention_input)` 与 `public_demo_steps(...)` 放进策略 context，并写 `policy_context.json`。求助内容就是这样到达下一次尝试的策略的 | 没有处理 `attention_input` / `demo_steps`；context 里只有 robot/task/capabilities 和可选的 `memory_validation` | 小改；但基础策略要真的读它（见 G9） |
| G3 | 配置：`_config(request)` 校验 `request.config_path`，要求 task/seed 一致，并检查 `overall_deadline <= whole_case_wall_seconds`、`attempt_deadline <= attempt_wall_seconds` | 用 `validate_config`（`openarm365_dev.config`）校验 Dev 配置，schema `attentionbench.openarm365-dual-dev-config.v2`，`runner_boundary='dev_loop'`，含 `dev_lock`；`whole_case_wall_seconds=1800` | 中等：需要一个 formal 配置 schema（建议 `attentionbench.openarm365-formal-config.v2`），内容取自 Dev v2 配置，去掉 dev_lock，改为 formal 预算与 `runner_boundary` |
| G4 | 预算：`limit=min(config, request.sdk_call_limit)`；时限由冻结配置决定 | 硬编码 `0<seconds<=480` 和 `0<limit<=80` | 小改，数值由新 formal 配置决定（见 §4） |
| G5 | `result` 与 `trace` 里 `runner_boundary` 的值不应是 `dev_loop` | 写 `runner_boundary='dev_loop'`、`formal_eligible=False`（boundary 仍会再设 formal_eligible=False） | 小改 |
| G6 | 返回结构 | 已有 `schema_version`、`sandbox`、`native_evaluator`、`artifacts`、`recording` | **复用** |
| G7 | 每次尝试独立重置、清理进程组、取消和超时 | `NativeOwner` 每次 `start()`，`finally` 里 `owner.stop(...)` 并回收进程组；取消事件、超时处理已有 | **复用** |
| G8 | Safety：独立于策略的监控，产物 `safety.json` | 来源 `native_per_physics_step_monitor`；违规由 ledger 错误字符串中的 5 个关键词归并（`Unexpected physical`、`Measured native` 等）。formal 参考 runner 用 `robocasa365/safety.py::Safety` 包装每次 SDK 调用 | 待核对：关键词归并是否足够稳；`unsafe_attempts` 当前只有 0/1 |
| G9 | 基础策略消费 `attention_input` | `openarm365_dev_v2/policies/*.py` 是 Dev 的初始策略，由 LLM 每轮改写，不读 `attention_input` | **待定**，这是阶段 4 的核心：固定策略里“hint 改变什么”要先定义 |
| G10 | 注册：`formal_attention_cli --suite robocasa365` 构造 runner | `formal_attention_cli.py:162-168` 构造 `RoboCasa365FormalSuiteRunner(service_source_root=…)`（PandaOmron，服务已封死）。另一处构造点在 `dev_loop.py:339`（Dev v1 通道，同样已死） | 小改：按配置 schema 选 runner；新 runner 不需要 `service_source_root` |
| G11 | Memory 范围：`memory_context(c)` 用 config 里的 robocasa_commit/robot/controller/scene/object_set/camera 生成 | Dev v2 配置含同名字段（复制自 EXPECTED），应可直接用 | 复用，需测试确认 `robot='OpenArm365'`、`controller='BASIC'` 能通过 `ScopedMemoryGateway` |
| G12 | 机器人描述：`formal_attention_run.py:156-159` 已有对 OpenArm365 的分支（`model:'OpenArm'`、`Tidyverse` 平面底座） | 与 Dev v2 一致：双臂、`base_mode='stationary'` | 复用，但 `execution_arm` 现在是 `both`，旧分支按 `left` 写的，需核对 |
| G13 | 冻结：`openarm365/config.py::make_config` 把 `integration_sources`（含 `robocasa365/formal_runner.py`）的 SHA 钉在配置里 | 新 runner 属于新文件，新配置需自己的 source set | 中等，随 G3 |

## 4. 会改变估算的发现

1. **预算量级不同。** PandaOmron 的冻结预算是单次尝试 120 秒、整体 300 秒、SDK 40 次；OpenArm365 双臂在 Dev v2 里是单次尝试最长 480 秒、SDK 80 次、控制步 2400（历史完整成功的 sink 单次 133 秒墙钟，720 控制步）。`docs/cost_estimate_350.md` 按 Franka RoboCasa 的约 208 秒一格估的，对 OpenArm365 要重估，350 格的墙钟很可能明显高于 10.6 小时。
2. **旧 formal OpenArm365 配置是另一个机器人。** `openarm365/config.py` 的 `openarm365-config.v1` 是 **左臂 only、BASIC 控制器、旧 OSC 服务**，且钉了旧服务的源码；它与 Dev v2 的双臂、cuRobo V2 owner 不是同一个机器人。新 formal 配置不能沿用 v1。
3. **approval 信号（我在请求机制里做的软限位）目前只有 Franka RoboCasa 的 `robocasa_native/safety_monitor.py`。** OpenArm365 要在主矩阵里触发 approval，需要在它的 Safety 路径里补等价的钩子；不配置时 approval 不会触发（与 Franka 同样的默认）。
4. **hint 的语义。** 在 formal 里，hint 通过 `attention_input` 改变下一次尝试的行为，策略文件本身不变。Dev v2 里 hint 是给 LLM 的修复提示，会改写策略代码。所以 B 的基础策略必须是“会读 hint 的固定策略”，它的设计与成功率一起是阶段 4 的工作。

## 5. 没有核对的部分（阶段 2 开始前需要补）

- `robocasa365/safety.py`、`adapter.py`、`openarm365_dev/owner.py` 与 `openarm365_dev_v2/owner.py` 的内部细节（只确认了接口和调用方式）。
- `build_sdk` 在 Dev v2 双臂与 formal 参考 runner 之间是否同一个实现（Dev v2 runner 里的 `build_sdk(owner, event)` 来源未读）。
- 8769 的 UI 启动路径（`ui_launch.py` / `ui_server.py`）如何选择 runner：grep 只找到 CLI 与 `dev_loop` 两个构造点，UI 可能通过 CLI 子进程调用，需确认。
- `execute_formal_policy` 对 `attention_input` 的上下文大小/字段限制（`policy_input.py`）。

## 6. 建议的阶段 2 步骤（不含模拟器）

1. 新建 `openarm365_dev_v2/formal_runner.py::OpenArm365FormalSuiteRunner`，**继承或包装** Dev v2 runner，只覆盖 G1、G2、G4、G5，不复制 execute 主体，避免两份漂移。
2. 新建 formal 配置：`make_formal_config / validate_formal_config`（G3、G13），保持 Dev v2 配置不变。
3. 把 `validate_config`（`robocasa365/config.py`）的 schema 分发加上新 schema；`formal_attention_cli` 按 schema 选 runner（G10）。
4. 单元测试，用 `openarm365_dev_v2/tests/fakes.py` 里已有的假 owner：
   - 通过 `run_with_formal_boundary` 的完整检查；
   - entry_lock 原样带回；
   - `attention_input` 进入策略 context；
   - 预算越界被拒；
   - 取消/超时/场景不一致的状态；
   - `ScopedMemoryGateway` 对 OpenArm365 通过。
5. 回归：Dev v2 套件与 `benchmarks/attention_harness/tests` 用名字集合对比基线。

## 7. 需要你决定的

- G9 的方向：基础策略是 **一个固定策略读 hint**（建议），还是别的形式。这决定阶段 4。
- 预算：OpenArm365 的单次尝试 480 秒是否作为 formal 上限，还是要缩短（缩短会降低成功率，但降低成本）。

## 8. 阶段 2 状态（2026-10-16，假 owner 测试，无模拟器）

已完成并测试（`openarm365_dev_v2/tests/test_formal_runner.py`，23 项）：

| 差距 | 处理 |
|---|---|
| G1 入口锁 | `OpenArm365FormalSuiteRunner.allow_entry_lock=True`，锁原样写入 trace 与 result，通过 `run_with_formal_boundary` 的核对；Dev runner 仍拒绝 |
| G2 attention_input | 策略 context 增加 `attention_input` 与 `demo_steps`，并写 `policy_context.json`；无求助时为空字典 |
| G3 / G13 配置 | 新增 `openarm365_dev_v2/formal_config.py`（`attentionbench.openarm365-formal-config.v2`），身份、机器人、场景、锁与 Dev 配置同源，只改边界和三个预算字段；`robocasa365/config.py::validate_config` 已分发该 schema |
| G4 预算 | 三个预算字段（单次尝试秒数、整体秒数、SDK 次数）是配置项，范围限定在 Dev v2 已验证的包络内（尝试 ≤480 s、整体 ≤1800 s、SDK ≤80）；请求超出冻结值被拒 |
| G5 标记 | `runner_boundary` 在 formal 为 `formal`，Dev 保持 `dev_loop` |
| G10 注册 | `formal_attention_cli.robocasa365_runner_class(config)` 按配置 schema 选 runner |
| G11 Memory 范围 | `memory_context` 对 formal 配置给出 OpenArm365 / BASIC 范围，且与 Dev 配置相同 |

Dev runner 只增加了 4 个可覆盖的钩子（`load_config`、`budgets`、`policy_context`、`allow_entry_lock`/`runner_boundary`），默认行为不变。变异检查：去掉入口锁或 attention_input 的实现后，对应 3 项测试按预期失败。

回归（名字集合，非计数）：harness 失败集合 = 基线 56 + 已知的 `test_openarm_lock_preserves_virtualenv_invocation_path`，无新增；Dev v2 仍为 3 个失败与 57 个错误（Service 字节漂移、`openarm365_dev` 配置 re-pin、缺历史数据库），均与本阶段无关。

**仍未做**：G8（独立 Safety 的稳健性核对）、G9（会读 hint 的固定基础策略）、approval 软限位在 OpenArm365 上的钩子、formal 的多次尝试预算文件（`robocasa365/budget.py` 现在对 365 只放行 2 次尝试 / 1 credit 的上限，350 格矩阵需要 P5 时定义新的预算 schema）、8769 的两个入口重新指向新 runner。没有任何真实模拟器运行。
