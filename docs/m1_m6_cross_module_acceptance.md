# M1–M6 跨模块全链工程验收：冻结包

冻结日：2026-09-29。基线 U `feature/attention-native-robosuite` @ `f0d9093f35a462febdc349b0bfd21799b8e37074`，开始时工作树干净。独立 M/R/C/A/T 版本和状态、配置逐字节 SHA、执行命令、原件及审计放在 `/home/truares/桌面/attentionbench-m1-m6-cross-20260929/`。本文件先于本轮真实 Dev、模拟器和模型执行落盘；后续修复须保留失败原件，不能倒改此包的通过标准。

## 范围、配置与预算

只验工程链路，运行均为 `formal_eligible=false`。RoboCasa `counter_to_sink` 开发 seed 101，Robosuite `cube_lift` 开发 seed 103（现有 trusted v1 限定范围）；均为 `sim_gt`，选择七策略之一 `full_trace_aware_attention_planner`。各套从冻结任务配置与 M1 lock 出发，通过真实 Orchestrator Graph 的有界 `parcc/GLM` Dev、外部操作员对本次生成源码／配置／lock／收据精确 SHA 的明确批准、正式 Bridge、七策略 Harness、对应正式 Runner 与独立 Service、公开 Trace 决策与需要时真实 AdvisorProxy、Memory、诊断 Eval 和 UI 持久投影。原生任务成功不是工程通过条件，原生结果必须原样保留。

每套预算上限：最多两份经批准的 Graph 候选，其中每份 Dev 至多两次格式调用、每次一个 provider attempt；每份正式 run 至多三个 simulator attempt、一个 Advisor credit、3000 tokens、总截止 300 秒，Graph 不得自动重复派发；真实 Eval 每个已落盘 run 最多一次诊断生成，重启只读复用。故障注入仅用隔离证据副本和未批准 Graph，不新增模拟器 attempt。两套正常验收以**相同的已冻结 U/M/R/C/A/T 提交**执行；任何版本变化须先记录失败及另行冻结新版本复验，不能把跨版本结果合并。不得用测试回复、开发 runner、mock Service、held-out、正式七策略效果矩阵、真人或 LIBERO 填补证据。

## 固定通过条件

1. **入口与批准**：任务、seed、策略、预算、代码、配置与 M1 lock 经 Graph 持久冻结。真实 Dev 原始回复、usage、源码和收据可复核；批准前无 Harness run。外部操作员对本次精确 SHA 明示批准，Bridge 对批准后篡改或错配 fail closed。
2. **双正式全链**：每套 Graph 只派发已批准候选，Bridge 命令为正式 CLI，Harness 每个 attempt 均有正式 Runner、原生 evaluator、独立 Safety、`trace`／`safety`／`sandbox_receipt`／`native_result` 四类产物。Graph、entry lock、run、attempt、Trace、Advisor 请求、Eval 和 UI 身份及代码／配置 SHA 一致，所有产物复算 SHA，Service 停止与进程组回收可核验。Trace 决策来自公开持久证据；需要 Advisor 请求时必须是真实 `parcc/GLM`，不能是测试 transport。
3. **Memory**：使用已有证据的隔离权威库。Robosuite trusted v1 仅在验证范围检索、给精确 attempt 颁授权并实际使用，范围外拒绝；RoboCasa 未晋升 candidate 不被当作 trusted 检索、授权或使用。RoboCasa 不要求新正向效果，也不改晋升门槛。
4. **诊断、展示与恢复**：Eval 引用同 run／attempt 和事件，原生结果及 Safety 不被诊断文字改写；UI 显示持久预算、请求、公开媒体、Trace、诊断和结果。Graph/UI 独立进程重启读取相同证据，不新建已完成 attempt。
5. **受控故障**：至少一例批准／身份错配、一次产物身份或 SHA 错配、一次中途失败／不明派发状态均明确拒绝或记录；恢复不重复执行已完成 attempt。检查只用隔离副本或尚未派发节点。

上述全部在同版本双模拟器通过，才更新 `STATUS.md`、`docs/attentionbench_progress.md` 并提交当前功能分支。任一项缺失或失败，保留失败证据、明确阻塞点，不宣称全链完成，也不提前提交。本包不修改 M1–M6 已通过的模块门槛。M3 历史 depth 500 根因风险独立列示，受控拒绝或某次未触发均不算根因解决。

## 执行前已知集成缺口

既有 M2 Graph 路径在单 attempt 交接后直接进入 `review`，不继续 Memory/Eval；其 `validate_handoff` 固定要求恰好一个 attempt，不能直接证明本包的 Trace 决策和 Memory 使用。跨模块衔接需新增独立契约并回归既有 M2 门槛，不能放宽原函数。当前运行进程没有 `PARCC_API_KEY`／`LITELLM_KEY`；真实 Dev、Advisor、Eval 等待凭据，不以测试回复代替。此两项在最终逐项审计中保持待核，直到实际修复和真实运行证明通过。

## 预检记录（非全链通过）

外部 `freeze.json` SHA-256 `4b2d419ffbb3fddd04a00c65ef19905044711b2e8463a08950059914f47b7cc6`。双 Graph 的任务锁与隔离 Memory 权威库已准备，尚未生成新源码、出具本轮批准或启动新正式 attempt。Robosuite seed 103 配置 SHA `1a0953f5c5311e3e69f2ddee99781e0f912b6add87847e0b8c5321c320e7ff1d`，RoboCasa seed 101 配置 SHA `fd32f0dfde74c6a754edf8e0b4ba78f94f733762e226bec2af177341edb2cc27`。隔离 Memory 预检：Robosuite 在限定 context 返回 trusted v1；RoboCasa 返回空，其两份旧记忆仍为 candidate，0 grant／0 use 基线见外部 `memory_baseline.json`。

新增 `crosschain_gate.py` 只在 `m1_m6_crosschain=true` 节点验多 attempt；原 M2 `validate_handoff` 未变。既有 M2／Bridge／生成相关测试 **11 passed**；历史双 attempt 正式原件通过新契约预检，批准 SHA、run 身份与 runner 边界三类改动被拒。历史原件验证仅证明校验器可用，不是本次真实 Graph 全链。预检审计和 SHA 索引在外部证据根；`STATUS.md` 与模块进度页不因此更新，当前分支不提交。

新增契约还把 Graph 所选 task、seed、策略、模拟器配置 SHA 和运行预算绑定到外部冻结文件 SHA；历史正式原件的隔离预检确认预算改动也被拒绝。Graph 中途派发记录和完成后的单次复用仍需本轮真实运行核验。

本轮的隔离故障副本位于外部 `faults/`：正式 Trace 追加空白字节后 Eval 明确拒绝 `trace digest mismatch`；修改 attempt link 后拒绝 `attempt identity or approval mismatch`；Graph 在派发已认领但无结果的中途状态重启，`/attention/auto-start` 返回 `spawned=[]`，节点停在 `review` 并保留中断原因。三例未新增模拟器 attempt。它们是故障关口预检，不能代替双套真实全链和其完成 attempt 的重启复用验收。

Graph 回归按仓库实际入口执行：普通 pytest 文件 **46 passed**，`test_orchestrator_pipeline.py` 是独立脚本而非 pytest fixture，按文件说明运行 **84 passed、0 failed**。先前把它混入 pytest 的一次命令产生 28 个 `fixture 'orch' not found` 收集错误，原日志留在外部证据目录；纠正调用后无这些错误，不把错误日志删改或算作代码失败。

## 双套真实 Dev 生成，等待明确批准

用户提供临时真实模型凭据后，先锁定 `execution_lock.json`（SHA `107907d79735f113667c65f9f25845fec7db4300bb70df52b01fe18203e05b7f`），再由两套真实 Graph 各发出一次 `parcc/GLM` Dev 请求。Robosuite 与 RoboCasa 均为 **1 format call／1 provider attempt**，分别使用 380／450 tokens；原始回复、usage、生成收据和代码已保存。两套均停在 `review`／`awaiting_approval`，批准前 `require_approval` 拒绝，独立进程重启后的 `/attention/auto-start` 均返回 `spawned=[]`。没有 Bridge 派发、Harness run 或模拟器 attempt。`preapproval_audit.json` SHA `6cff0ede6b13364c3be8dcb1bed34749a4f312a4a83c7f39459e044245aa70fe`；原件中的凭据模式扫描为阴性。审批所需两套源码、配置、Dev 收据与 entry lock 精确 SHA 见 `candidate_review.json`，本节不代签用户决定。

## 首版冻结配置执行结论：未通过

用户随后明确批准 `candidate_review.json` 中两套精确 SHA 候选。外部 `approval-robosuite.json` 与 `approval-robocasa.json` 为实际 M2 批准记录；两套 Graph 均通过正式 Bridge 派发，正式 Runner 和对应独立 Service 生成原生结果、独立 Safety 以及 `trace`、`safety`、`sandbox_receipt`、`native_result` 四类 SHA 可复算产物。Eval 已完成；完成后 Graph 分别重启，`/attention/auto-start` 均返回 `spawned=[]`，没有重复 attempt。UI 从持久 store 重建双套 run 的 attempt、请求、Trace、预算与 Eval，原始快照保存在外部 `ui-*-after-restart.json`。所有运行 `formal_eligible=false`。

Robosuite seed 103 正式 run 有两个原生失败 attempt，独立 Safety 均为 0。公开 Trace 先决定 `inspect_trace`，再决定请求 hint。真实 Advisor 一次回复用了 **3226 tokens**，超过冻结的 3000 上限，请求在响应持久使用前取消，run 停止于 `advisor_error:StateConflictError: token budget exhausted`。这次 run 未产生可信 Memory 的新授权或使用；已有 trusted v1 及历史有效使用保持原状。因此本次要求的 Robosuite 限定范围可信检索与实际使用未获证明，不能以历史用例替代。

RoboCasa seed 101 正式 run 有三个原生失败 attempt，独立 Safety 均为 0。一次真实 Advisor hint 回复使用 2648 tokens，形成新 candidate 和待批准修复记录；原有和新 candidate 均未晋升，0 新 grant／0 use。RoboCasa 不要求新的原生正向效果，此处原生失败如实保留。

本版的跨层身份、批准 SHA、四类产物、Service 回收、Eval、UI 重启和受控故障关口已有证据；但固定通过条件要求**双模拟器同版全链及 Robosuite Memory 使用全部成立**。由于上述 Robosuite 两个阻塞点，结论为 `failed_acceptance`。完整逐项审计见外部 `final_audit.json`，原件哈希见 `sha256-index-final.json`。本版冻结参数与门槛不追改；`STATUS.md`、模块进度页和当前功能分支提交均不更新。若另起候选或调整策略、预算，必须保留本版失败原件并先冻结新版本，不能拼接跨版本结果。

M3 历史 depth 500 的根因风险仍单列为**未解决**；本次正常退出与故障拒绝均不构成根因修复证据。
