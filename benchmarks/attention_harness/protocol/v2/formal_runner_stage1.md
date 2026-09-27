# 正式 runner 第一阶段核查（2026-09-27）

本文件保留第一阶段核查及后续工程进展，不是正式实验授权。开发与单 seed
工程运行仍为 `formal_eligible=false`。初次核查时，`formal_runner_boundary.py`
只验证请求／产物形状，测试使用 FakeRunner，尚未注册真实 suite runner；
后续两套真实 adapter 的进展见文末。

## RoboCasa 上次动作失败的直接证据

- `counter_to_sink` seed 101 的修正后开发 smoke：夹爪命令成功；手臂命令失败，独立安全监控记录 `action_outcome_unknown`，没有将其当成可重试动作。
- 独立 Agent Server 的 `logs/code_execution.log` 中，执行 ID `6c074787-d560-43` 在运行 11.48 秒后退出 code 1；其 stderr 明确为 `robot_sdk.arm.ArmError: Timeout: arm did not converge (error=0.3588 m)`。这证明 SDK 的收敛超时是直接失败原因；“撞到障碍物”只是 SDK 的推测，不能视为已证实根因。
- 安全产物记录手臂位置由 `[0.3864, 0, 0.4894]` 移至约 `[0.6553, 0.2964, 0.5710]`，说明失败前确有部分运动。不能在不知道动作结果时盲目重试。
- U 的 `robocasa_native/agent_actions.py` 现只从远端 stderr 提取已知、限长的 arm 不收敛数值，附在错误证据中；不透传任意 stderr，不改变 fail-closed 的 `action_outcome_unknown` 判定。专项回归同时验证未自动重试／取消已终止的 job。

## 双 suite 正式 runner 的剩余验收

| 条件 | RoboCasa 初次核查 | Robosuite 初次核查 | 进入正式实验前的证据 |
| --- | --- | --- | --- |
| 策略来源／固定配置 | 开发沙箱保存源 SHA；请求契约另有 config SHA | 开发沙箱保存源 SHA；请求契约另有 config SHA | 真实 runner 在执行前后核对同一批准 digest、task/seed/camera/perception/service 版本，产物固化 |
| 隔离与 SDK 边界 | 生成策略在子进程，通过 parent SDK RPC；非敌对代码的 OS 隔离尚未认证 | 共用开发子进程；OS 隔离尚未认证 | 各 suite 的进程、凭据、文件／网络边界和逃逸负例验收，不能仅凭 AST 验证自证 |
| 整体 deadline／动作取消 | 单 job 有 cancel token 与局部 smoke；本次非收敛动作安全中止 | Service 当前公开 `/v1/step`，未见取消端点 | 对 reset、排队、动作和整个 episode 设置统一 deadline；超时后确认动作停止或明确 fail-closed，不留后台动作 |
| 独立安全与原生 evaluator | 安全产物和 native success 已存在于开发运行；此次 unsafe=1、success=false | 开发运行有安全／原生结果 | 两 suite 真实 runner 保存不可由策略伪造的 trace、safety、sandbox receipt、native result 及 SHA；缺任一项拒绝正式标记 |
| 冻结矩阵 | 任务／seed／场景／相机连续性与成功策略尚未验收 | 任务／seed／相机连续性与成功策略尚未验收 | 预注册开发矩阵、no-op 负控、参考策略与稳定性；之后独立审批准入，held-out 不用于调试 |

初次核查建议先实现 RoboCasa 与 Robosuite 的真实 `FormalSuiteRunner` adapter 和不可伪造的收据，再做 deadline／取消矩阵。边界检查通过本身仍返回 `formal_eligible=false`；只有独立验收通过后才考虑改变标记。

## 后续 Robosuite 单 seed 工程验收（2026-09-27）

本节更新 Robosuite 一列；上面的表格保留第一阶段核查时的状态。
U `robosuite_memory/formal_runner.py` 已实现真实 adapter，独立
`formal_cli.py` 经 AttentionHarness 的 `run_robosuite_formal_attempt` 接入
`run_with_formal_boundary`。策略在 bubblewrap 命名空间中运行，只能通过
父进程持有的 Shared SDK RPC 发出动作。配置固定 task／seed／相机／
scene／object set／perception／Service commit，策略和配置在运行前后核对摘要。

R Service 保持 `12bc69a` 干净 checkout。它的 GL 单线程 `/v1/step` 不提供
HTTP 取消；正式路径改为每 attempt 独占一个从该 checkout 启动的 Service
进程组，deadline 或取消时终止进程组、回收 leader 并确认整个组消失。
这避免了客户端 HTTP 超时后 Service 继续执行动作。

U `evidence/robosuite_formal_seed101_acceptance_2026-09-27.json` 保存
`cube_lift` seed 101 的正常、动作中超时、取消、安全拒绝四次真实 Service
运行的命令、版本、产物 SHA、结果和停止收据。四项工程探针通过；
正常探针原生成功为 false，未证明任务成功率。完整开发稳定性、
独立冻结矩阵和 RoboCasa 正式 runner 仍未完成，所有结果继续
`formal_eligible=false`。

## 后续 RoboCasa 单 seed 工程验收（2026-09-27）

U `robocasa_native/formal_runner.py`、`formal_services.py`、`formal_cli.py`
和 `formal_acceptance.py` 已提供真实 RoboCasa 正式路径。每次 attempt
独占启动 C 的 RoboCasa Service 与 A 的 Agent Server，端口偏移 800；
策略只经 bubblewrap 中的 Shared SDK RPC 请求传感器、手臂、夹爪和底盘。
批准配置固定 task、seed、scene/object ID、相机、语言目标、安全限值、
两服务的提交和工作树摘要。父进程持有原生 evaluator 和独立安全监测，
写出四类 SHA-256 产物。超时或取消时终止两套 Service 进程组并确认回收。

`evidence/robocasa_formal_seed101_language_attested_2026-09-27.json` 记录
`counter_to_sink` seed 101 的正常、整体超时、操作员取消、动作中取消和
安全拒绝五项真实服务工程探针，均通过；动作中取消有 Agent Server
`cancelled/stopped` 收据。正常 no-op 的原生成功为 false；这不证明
任务成功率。C、A 的现有工作树仍有未提交改动；工程配置钉住了当时
的工作树摘要，但尚不能视为发布版固定提交。两任务 25-seed 成功门槛、
独立冻结矩阵与正式成绩仍未完成，所有结果
继续 `formal_eligible=false`。

实际 Service 的 seed 101 任务语言分别是 sink 的 `boxed drink` 和 cab 的
`kettle electric`；早期批准配置误写为 `yogurt` 和 `condiment bottle`。
当前 Runner 在 reset 后逐字核对 Service 语言，匹配后才启动策略。
`evidence/robocasa_cab_seed101_language_attested_noop_2026-09-27.json`
也验证修正后的 cab 配置在真实双 Service 上通过单次 no-op 联通；
其原生成功仍为 false。
`evidence/robocasa_sink_seed101_language_rejection_2026-09-27.json`
使用旧配置验证错误语言被拒绝，策略未启动、原生结果未计算，双 Service
仍完整回收；这属于准入负例，不是正式 episode 成绩。

`evidence/robocasa_formal_cab_seed101_noop_2026-09-27.json` 另记录
`counter_to_cab` seed 101 的修复前真实服务 no-op 联通：固定变体与相机、
原生失败判断、四类哈希产物和双进程组回收均通过。其后 seed 106 原始
失败见 `evidence/robocasa_cab_seed106_variation_blocker_2026-09-27.json`；
实际注册的 T `single_stage/kitchen_pnp.py` 已修复柜体相对台面采样区过窄，
两个任务 101–125 的每 seed 双 reset 变体发现均通过，见两份
`robocasa_{cab,sink}_101_125_variation_discovery_2026-09-27.json`。
当前 v3 正式配置还固定 T 工作树、模拟器 Python 与 ManiSkill 源码摘要；
`evidence/robocasa_cab_seed106_formal_noop_2026-09-27.json` 另核查原失败
seed 的 v3 正式 Runner no-op 路径、原生失败判断、独立安全与哈希产物。
这些发现不是原生任务成功，也不是第二任务正式矩阵验收。
