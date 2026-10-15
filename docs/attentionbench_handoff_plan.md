# AttentionBench 剩余工作交接计划（2026-10-16）

本文件交给下一位执行者（GPT/Codex）。目标是把 `timeline.md` 的 “System components to add” 做完整。**先读 `AGENTS.md` 和 `STATUS.md`**，其中的“单板块封闭验收”“不得把工程通过说成正式实验验收”等规则继续有效。

## 0. 当前状态（接手前必读）

仓库：主仓库 `Tidybot-Universe-attention-native`，分支 `attentionbench/baseline-20261015`；Memory 服务 `~/桌面/attention_memory_service`，同名分支。**均未 push。**

| 提交 | 内容 |
| --- | --- |
| 主 `60cb9c0`、`824ffd4` | 基线：固定当时未提交的代码、测试、文档（不含 `outputs/`、`openarm_phase*`、`openarm_bimanual/launch_configs`） |
| 主 `98db33a` | 请求机制 v2（设计见 `docs/attentionbench_request_mechanism.md`） |
| 主 `be7c2f0` | Memory 生命周期 v2（见 `docs/attentionbench_memory_lifecycle.md`） |
| Memory `d76c6ed`、`15968a3` | 基线；验证裁决、enable、目录/使用记录/历史接口 |
| 主 第 3 步提交 | UI 后端：资源预留、GPU 计量、执行目标选项、run `lock`、自主工作阻塞原因、`permitted_actions`、请求/Memory 快照字段（`gpu_meter.py`、`tests/test_resources_and_lock.py`） |

测试怎么跑（用现成 venv）：

```bash
cd ~/桌面/Tidybot-Universe-attention-native
PYTHONPATH=~/桌面/attention_memory_service:$PWD ~/.cache/tidybot-attention/venv/bin/python -m pytest -q -p no:cacheprovider benchmarks/attention_harness/tests
cd ~/桌面/attention_memory_service && PYTHONPATH=$PWD:~/桌面/Tidybot-Universe-attention-native ~/.cache/tidybot-attention/venv/bin/python -m pytest -q -p no:cacheprovider tests
```

回归标准：**基线有 56 个固定失败**（OpenArm、`test_agent_actions_v2`、`test_fixed_ui`、curobo 部署等，清单 `outputs/baseline-failures-20261015.txt`）。对比失败集合，**不新增失败**才算通过，不要比数量。最近一次完整结果（第 3 步提交时）：**1309 通过 / 56 失败（与基线完全相同）/ 10 跳过，无新增失败**；Memory 服务仓库 77 通过。

## 1. 已经做出的设计决定（不要推翻，除非用户明确要求）

1. 请求默认**异步**（`request_mode=async`），同时只允许 1 个未完成请求；`blocking` 保留为消融基线。approval 和“只剩 1 次尝试”时强制阻塞。
2. “被采用 / 被忽略”是 **disposition**（`response.used` / `response.ignored` 事件），**不是新的请求状态**。现有前端大量判断 `request.state === 'answered'`，不要改状态枚举。
3. approval 信号来自独立安全监控的**软限位**，规则在已批准 config 的可选键 `approval_rules`（默认不配置，所以默认不触发）。interrupt 是先停机再入收件箱的 CRITICAL 请求，不调 Advisor、不占 credit。
4. 每个被采用的 hint 都生成候选 Memory（带指导后的结果和 `effective` 标记），由配对验证筛选；入库不晋升；验证触发保持人工。
5. 资源预留表 `resource_reservations` **只在首次使用时创建，不在 `_initialize` 里创建**，否则打开冻结的 Memory 快照会改动其字节。
6. GPU 时间 = 主机 GPU 利用率加权秒数（含同卡其他进程），预算 0 表示“不设预算但照样记录”，无读数时是 `null`（未追踪），不是 0。
7. `scheduler_config` 已加入 `request_mode`、`approval_rules`、`gpu_seconds_limit`，所以**旧七策略冻结包的摘要和源码字节锁都已失效**。

## 2. 任务清单（按顺序做；每项都要有测试，并更新 `STATUS.md` 与 `docs/attentionbench_progress.md`）

### A. 收尾第 3 步 —— 已完成
- harness 全量回归已按基线对比通过并提交；接手时只需先重跑一遍确认环境仍然一致（失败集合应仍是那 56 个）。

### B. 剩余的后端小项（都是纯代码 + 测试，不需要模拟器）
1. **Run 崩溃回收**：新增 `run_recovery.py`。launch 的监督进程已死、run 仍是 RUNNING 时：取消挂起/fallback 的请求（退还 assistance credit、释放 `resource_reservations`、结束等待窗口），run 转 FAILED（原因 `scheduler_lost`），launch 记终态。在 UI server 启动和 `/api/runs` 列表时调用；必须幂等，且不动存活的进程。测试用 `populated_store`（见 `tests/test_attention_store.py`）造一个带挂起请求的 run。
2. **Advisor 只读缓存重放**：给 `SimGTAdvisorProxy` 加 `replay_only`（缓存未命中直接报错 `advisor_cache_miss`，不调用 provider），CLI `--advisor-replay-only`，使相同条件可以逐字复现。记录进 `scheduler_config`。
3. **消融开关**：`memory_enabled`（关闭时完整规划器不检索、不使用 Memory）与 `trace_evidence`（关闭时规划器不用 trace assessment，Advisor 包只含失败摘要，不含 events/evidence）。两者进 `scheduler_config`，在 `AttentionRuntime._advisor_packet` 与 `formal_attention_run.execute` 里落实。测试要证明“关掉后 Advisor 包里确实没有对应内容”。
4. **随机升级的次数匹配工具**：现在的 `target_request_count` 是人工预注册的常数。新增函数/CLI，从同任务同 seed 的完整规划器 run 的 `requests`（不含 interrupt）数量派生随机策略配置，并写出可审的 JSON 与 SHA。
5. **请求生命周期时间线**：`request_lifecycle.py`，按请求列出 pending/answered/timeout/fallback/cancelled/ignored/deferred 的时刻、回复延迟、实际采用的回复、关联的执行和候选 Memory；放进 run 快照 `request_timeline`，并提供 CLI 输出 JSON。这是 live-human 验证（要求记录响应延迟、状态转换、最终采用的回复和 Memory 产物）的记录格式。
- 验收：各自的新测试通过；不新增失败。

### C. 合并 8769 分叉（高风险，需要用户批准后再动运行中的服务）
- 8769 实际运行 `outputs/dev-live-monitor-20261014/candidate-eval-agent/`（非 git 仓库）；主工作区的 Dev v2 后端（`dev_v2_loop`、`dev_experiment`、`dev_ledger`、`dev_report`、`dev_planner*`、`dev_wait_recovery` 等）更新，8769 版本独有 `v2_evaluator.py`、`daily_dev_v2.py`、`dev_preview.py`、多视角相机。**不要整体覆盖任何一边**，需要三方合并，先核实 `candidate-fontfix` 是否是公共祖先。
- 已有的约束：不要动 `candidate-fontfix`、`candidate-final`；重启 8769 必须先确认没有活动 run，并保留回滚办法（旧目录保留）；自动模式的权限分类器会拦截重启和写切换脚本，需要用户自己添加 allow 规则，不要绕过。
- 验收：合并后 `test_dev_workspace.cjs` 与 Dev v2 相关 Python 测试全过；数据库切换前后逐行哈希一致。

### D. 前端（后端接口都已就绪，前端一行都没动）
接口清单（均已有测试）：
- 请求：`explanation`、`permitted_actions`、`request_dispositions`、`waiting_activity`、`request_timeline`（B5 完成后）；`POST /api/requests/{id}/ignore`。
- 资源：每项都有 `reserved`；GPU 有 `budgeted`、`coverage`。
- run 上下文：`lock`（条件、`policy_switchable`、`ranking_eligible`）、`autonomous_work`（running / continuing_while_waiting / blocked 的 resource|failure|dependency）、`/api/run-options` 的 `execution_targets`、`request_modes`、`limits.gpu_seconds`。
- Memory：快照 `memory`；`GET /api/memories[?status=]`、`GET /api/memories/{id}`、`POST /api/memories/{id}/{disable|enable|rollback|expiry}`（需要 `actor`、`reason`）。
- 要做的界面：收件箱显示 explanation/优先级/截止/状态与忽略按钮（按 `permitted_actions` 显示，不要在页面里硬编码）；请求详情显示检索到的 Memory 内容和版本；候选/验证队列；生命周期按钮；资源卡片显示 reserved 与 GPU 覆盖说明；执行目标与请求模式作为独立选项，真机显示“不可用”及原因；run 锁定条件和是否可参与排名；自主工作的阻塞原因。
- 测试环境（来自之前的记录）：`NODE_PATH=/home/truares/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules`，`CHROME_PATH=/usr/bin/google-chrome`，`UI_TARGET_TIMEOUT=6000`；`test_live_ui.cjs` 有 85 项固有红灯，比较失败名集合而不是数量；必须用真实截图验证，不要把“被阻塞的测试”报成通过。
- 验收：用真实 UI（候选目录先试，不直接上 8769）走通：收到请求 → 忽略/回复/延后 → 看到采用或忽略 → 看到 Memory 候选 → 禁用/回滚 → 刷新后状态保留。

### E. 基础 benchmark 稳定性（需要模拟器，按 `AGENTS.md` 的 M1→M6 一次只开一个模块）
- 现状（`attentionbench-fixed-base-profile-20260929/engineering_v35_profile`）：50 槽基础策略档案原生成功 **0**，RoboCasa 6 槽无效（手臂结果未知被独立 Safety 判 unsafe，或目标实例后缀不匹配），`safety_gate=false`，`depth_comparison_bias_gate=false`（历史 depth 500 的原始异常帧缺失）。
- timeline 要求每套至少 2 个稳定任务，目前冻结主实验每套只有 1 个任务（`cube_lift`、`counter_to_sink`）。先审计再冻结验收包，不要边做边加需求。
- 不要为了翻布尔值放宽门槛，不要改历史签名。

### F. 重新冻结并准入
- 改动已使旧冻结包失效：重新生成 350 个配置/锁（2 任务 × 25 seed × 7 条件），重新计算源码字节锁，重写 `REVIEW.md`，重新做总体准入审计。
- 批准必须由用户发出，不能由执行者代写；`execution_authorized` 在用户明确授权前保持 false。

### G. 实验（每一项都需要用户另行明确授权，涉及真实模型调用和模拟器）
1. 开发集短验证：确认冻结配置无集成错误，不计入结果。
2. 350 格主矩阵：两任务 × 25 seed × 7 条件，同一 Advisor、同一预算。运行中只修明确的基础设施 bug，不按中途结果改任务/策略/提示/预算。
3. 三项消融（子集 5–10 seed）：blocking/asynchronous、Memory on/off、trace-evidence on/off（B3 提供开关）。
4. 在真实服务上跑至少一个候选的 5 seed 配对验证，各覆盖一次晋升和一次拒绝路径（目前拒绝路径只被单元/集成测试覆盖）。
5. live-human 验证：内部用户在真实 UI 里处理 hint / approval / interrupt，覆盖及时回复、延迟/无回复（fallback）、忽略、取消；用 B5 的时间线记录。这些数据只用于系统验证和案例，不进主 benchmark 排名。
6. held-out（1001–1100）最后做，且需要单独冻结。

### H. LIBERO 迁移（主 benchmark 稳定之后）
- 现状：attention 分支里**没有任何 LIBERO 代码**；旧 bridge 在另一个 checkout `~/桌面/Tidybot-Universe/benchmarks/libero_native`（分支 `benchmark-test`）。
- 步骤：审计旧 bridge 的 SDK 边界、成功判定、重置、产物路径 → 选一个 transfer suite 和 3–5 个任务 → 接入同一套 trace/request/artifact/result 管线（实现 `attempt_executor` 即可复用 `run_sim_gt_attention`）→ 只跑 Autonomous、Reactive 或 Retry-k、完整规划器。

### I. 不在范围内
真机执行目标（需要经认证的真机 runner 和独立硬件急停）、Deploy 在线发现、把 LIBERO/真人任务混进主线模块。

## 3. 红线（延续项目规则）
- 不 push、不合并默认分支、不改历史证据和历史签名、不动 held-out、不动 `candidate-fontfix`/`candidate-final`。
- 未经用户明确授权，不启动付费模型调用或模拟器批量运行，不重启 8769。
- 区分四个层次并如实汇报：代码已存在 / 自动测试通过 / 真实服务跑通 / 正式实验验收完成。失败和未覆盖项保留原文，不得改写。
