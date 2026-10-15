# AttentionBench 请求机制 v2（异步、等待期工作、approval、interrupt）

实现：`benchmarks/attention_harness/request_flow.py`（单个未完成请求的生命周期）、`sim_gt_attention_run.py`（调度）、`core/{policies,runtime,store}.py`、`robocasa_native/safety_monitor.py`（approval 信号）。测试：`tests/test_async_requests.py`。

## 1. 请求怎样发出和回收

| 项 | 规则 |
| --- | --- |
| 默认模式 | `request_mode="async"`（CLI `--request-mode async|blocking`）。`blocking` 保留为 blocking/asynchronous 消融的基线 |
| 同时未完成的请求 | 最多 1 个。有请求挂起时，所有策略的“再问一次”都降级为安全工作（`request_already_pending`），Advisor 看到的失败历史（最近 3 次）不丢 |
| 异步 hint | 第 k 次失败后发出请求；第 k+1 次尝试作为等待期间的自主工作立即运行；回复在**第 k+2 次尝试之前的边界**并入，所以被指导的是第 k+2 次尝试。AdvisorProxy 的调用与第 k+1 次尝试重叠，边界处 join，保证“哪一次尝试拿到指导”不取决于 provider 速度 |
| 何时必须阻塞 | `request_mode=blocking`；approval（被扣下的动作要等批准）；只剩 1 次尝试时（等回复比盲跑更有价值）。原因记在 `blocking_reason` |
| Live–Human-first | 人在期限内未回复时机器人继续做安全工作，不暂停；期限到了在边界转 AdvisorProxy fallback；人可点“忽略”立即转 fallback |
| 回复没用上 | 等待期间自主重试先成功、或运行结束：回复若已到达，标记 `ignored`（credit 已被真实消耗，不退）；回复未到达则取消并退还 credit |

## 2. 请求状态与“是否被采用”

状态机不变：`pending → answered | fallback | timeout | cancelled`。**accepted / ignored 是回复被采用与否的 disposition**，独立于状态（现有 UI 对 `answered` 的判断不受影响）：

- `accepted`：`response.used` 事件（指导/批准/中断真的用到了某次执行）。
- `ignored`：`response.ignored` 事件，原因 ∈ `native_success_first | attempts_exhausted | stopped_before_use | run_ended | superseded`。
- 操作员对**挂起的人类请求**点“忽略”：`request.ignored` 事件 + 转 fallback（`POST /api/requests/{id}/ignore`，幂等）。
- 每条请求带 `explanation`：为什么问（`why`/`reason_code`）、要什么帮助（`assistance_requested`）、Advisor 能看到的证据（`evidence_shown`：trace/evidence/event ID 与投影 SHA）、agent 假设、等待期间怎么办（`while_waiting`），approval 另有 `withheld_actions`。

## 3. 等待期间的安全工作

持久化为 `waiting.activity` 事件（`store.waiting_activities(run_id)`）：`autonomous_retry`（机器人尝试，`robot_motion=true`）、`trace_inspection`、`memory_retrieval`、`approval_gate_hold`（`robot_motion=false`）、`evidence_index`。
具体做哪一种由策略决定：完整规划器有可信匹配 Memory 时检索 Memory，证据不足时继续检查 trace，否则继续重试；其余策略只重试。等待窗口 `waiting_windows` 如实统计期间的 attempt 派发数和 SDK 运动命令数。

## 4. approval

信号来自独立安全监控的**软限位**，不来自 agent：`soft_max_delta_m`、`soft_max_base_delta_m`、`gripper_release_after_grasp`。
- 越过硬限位 = unsafe（原行为不变）；越过软限位 = 动作**在执行前被扣下**（`ApprovalRequired`，没有任何动作发出），attempt 以 `approval_required` 结束。
- 默认不配置规则，因此默认不会触发 approval。规则放在已批准的 sim config 的可选键 `approval_rules`（进入 config SHA；旧 config 不含该键，摘要不变），开发 CLI 用 `--approval-rules`。
- 完整规划器据此发 approval 请求并阻塞等批准；批准后下一次尝试带 `approval_granted=true`，监控放行该次尝试的软限位动作（记录 `approval_used`）。其他策略遇到 approval 信号会失败关闭（`approval_required_policy_cannot_proceed`）。Benchmark 模式没有 `approval_granted` 回调时一律视为拒绝（`approval_not_granted`）。

## 5. interrupt

完整规划器判定 trace 有安全风险时先**停机器人**，再把它作为 CRITICAL 请求写进收件箱：由 `safety_policy` 立即应答（`interrupt_applied`），**不调用 Advisor、不占 assistance credit**（`credit_exempt=true`），并记录 `response.used(interrupt)`。独立安全监控报 unsafe 时只有硬停，不生成请求。操作员手动停止仍走 `/api/runs/{id}/interrupt`。

## 6. 对实验的影响（必须知道）

- 调度语义变了：`reactive_help` 等“失败就问”的策略在异步下每两次尝试最多问一次；10 次尝试配 9 credit 的每日 RoboCasa365 run 不再用满 9 次。需要旧语义时用 `--request-mode blocking`。
- `scheduler_config`（进入 `scheduler_config_sha256`）新增 `request_mode` 与 `approval_rules`，**现有冻结包的运行时源码字节锁、策略/配置摘要均已失效，七策略包需重新冻结、重新审阅**；`execution_authorized` 仍为 false。
- 本机制只在仿真器（sim_gt）开发/正式 runner 上验证。真机的物理急停与 approval 信号来源另需专门接入。


## 7. 只读复现、消融与生命周期读模型（2026-10-16 工程更新）

`--advisor-replay-only` 未命中报 advisor_cache_miss，不调用 provider、不创建缓存。`--no-memory-enabled` 关闭检索/使用；`--no-trace-evidence` 关闭规划器 trace assessment，Advisor 只收到失败摘要/失败历史且无事件/证据/图片。formal scheduler_config 与 run summary 记录开关，旧冻结源码锁不因此有效。

`python -m benchmarks.attention_harness.request_lifecycle --store-path <sqlite> --run-id <run>` 只读输出持久事件时间线，快照字段 request_timeline 与 CLI 同源；旧事件缺少时钟时明确 null，不补签。`random_matching --source-run <attention_run.json> --suite ... --task ... --seed ... --output-dir <新目录>` 从同cell终态完整规划器的持久请求派生随机目标并输出精确SHA，不自动批准/执行实验。崩溃回收只处理有本 PID namespace/boot 身份且确认监督进程死亡的launch，旧/跨namespace身份不猜测。以上为工程实现与测试，真实服务/真人与正式实验仍依交接验收门禁。
