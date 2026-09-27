# M3 双模拟器执行：冻结工程验收包

冻结于 2026-09-28，Universe 基线 `e1b76bdaf475c54c0847ac57e9dc24f98efc2c6c`。本包只评价 M3 工程执行，不评价策略成功率或正式实验成绩。所有运行保持 `formal_eligible=false`。

## 边界与版本

输入沿用 M1 的 task、seed、`sim_gt`、批准的代码／配置 SHA、预算及 entry lock，沿用 M2 的批准后 Bridge 交接契约；不修改其锁格式、拒绝条件或审批语义。M3 从 `AttentionHarness` 建立 run 开始，覆盖 Formal boundary 按任务分派、RoboCasa／Robosuite `FormalSuiteRunner`、Shared SDK 到各自 Service（RoboCasa 另经 Agent Server）的动作交接、原生 evaluator、独立 Safety、Raw Trace、`trace`／`safety`／`sandbox_receipt`／`native_result` 四类 SHA 产物、整体超时／取消与进程组回收。M4 决策、M5 Memory、M6 展示，以及真人、held-out、七策略效果矩阵和正式实验均不在本包。

版本钉住：U `e1b76bd`；R `081cd57ec9383895dc150050ca5d05c24628e7fa`（异常帧捕获）；C `320020a0c94434af31ec02df3413229576490fef`；A `4cf4daaba61d4cbbb0ca6daaa4ff28165c9daf1b`；T `b18bbf1585c42e370ae45ababdddad700cc2c71d`。实际 Python／ManiSkill 树、工作树及配置 SHA 随原始 manifest 再核对；如修复代码，另记新 SHA 并全量复跑受影响的冻结矩阵。

## 冻结矩阵和逐项通过条件

| 关口 | 固定案例 | 通过标准 |
| --- | --- | --- |
| A：正常执行 | 两套冻结任务：Robosuite `cube_lift`、`cube_stack`；RoboCasa `counter_to_sink`、`counter_to_cab`；各开发 seed 101–103；每例批准的公开 SDK 夹爪动作，Robosuite 每例 3 对开／关并读 depth | 每例真实 Service reset／动作／原生判断完成；任务、seed、场景／对象、相机／语言及代码、配置、Service SHA 与冻结值一致；Raw Trace 有 SDK 和 backend 动作；独立 Safety 来源且 0 unsafe；四类文件 SHA 与回执一致；Service leader 与全进程组回收。原生 success 可以为 false。 |
| B：Harness 与分派 | 四任务各 seed 101，经 `formal_attention_cli`、`autonomous`、1 attempt、0 credit 建 run | run／attempt／entry lock 身份在请求、结果、trace、配置快照及汇总中一致；suite-matched runner 被调用，错配被拒；四产物与 A 同标准；`formal_eligible=false`。 |
| C：故障路径 | 两套 Service 各在 seed 101 做整体 deadline、执行中取消、独立 Safety 拒绝；RoboCasa 另验证动作 job 取消 | 终态及原因准确，原生 evaluator 未完成时不得伪报成功；Safety 独立记录拒绝或未知结果；仍写完整四类产物及 Raw Trace；Service／Agent 进程组回收，无遗留。 |
| D：历史 depth 500 | Robosuite `cube_lift` seed 101 的旧动作序列（两次 GT 后 `gripper.open(settle_steps=1)`）及 A 的两任务×三 seed 重复动作 | 使用已加入的 `TIDYBOT_INVALID_DEPTH_DIR` 原帧捕获。若重现，保存原始 `.npy`、HTTP／Service 日志、诊断和 SHA，确认原因后只按证据修复，再全量复跑 A–C 与重复动作。若旧帧缺失且仍未复现，则此根因关口**不通过**；只报告固定窗口内未复现及残余风险，不宣布 M3 封闭。 |
| E：回归 | M1／M2 锁与相关自动测试 | 原有 lock 结构与既有同条件 SHA 不变；相关测试通过，任何先存的包外失败单列。 |

原始证据放在 `/home/truares/桌面/attentionbench-m3-20260928/`：先写冻结配置／代码和 SHA，再运行；保存逐例命令、stdout／stderr、完整 run／attempt、Service 日志、四类产物、独立审计、进程回收及总索引。旧 depth 原件在 `benchmarks/attention_harness/protocol/v2/evidence/week2_followup_2026-09-27/continuation/original_sources/original_depth_500/`；其中没有原始异常帧，只有 Service 越界异常引起 HTTP 500、随后 Safety `action_outcome_unknown`。此前 20 次精确重放和 6 次正式边界重复动作未复现，不构成根因修复。

配置冻结修订记录：初版 manifest SHA `fce1d7c6e0b5a4e869c62fe8d0ff7a00c2c6159d09b1d442cd7b31ca85347e9b` 错把 RoboCasa seed 101 的 `task_prompt` 复制给 102／103；`counter_to_sink` seed 102 在动作前被原生语言核验拒绝。旧配置、失败产物和进程回收保留于 `freeze.v1.json`、`normal_pre_amendment_progress.json`、`runs/normal_pre_amendment/`。通过真实 Service 对两任务 seed 101–103 重新发现语言后，仅修订四份 102／103 配置；任务、seed、矩阵、通过标准、代码和 Service 版本均未改变。修订后的 `freeze.json` SHA 为 `e8ce4278ef3ac1f8c26e2dbe48e3d76da623447a9ce14f2a7b61bebb65edd141`，本轮 A–D 均按修订版从头运行。

## 验收结论

**M3 未通过，不封闭。** 当前工程运行仍 `formal_eligible=false`。修订版 `freeze.json` SHA `e8ce4278ef3ac1f8c26e2dbe48e3d76da623447a9ce14f2a7b61bebb65edd141`；独立审计 `audit_final.json` 与逐文件 SHA 索引 `raw_index.json` 位于上述原始证据目录，后者涵盖 536 个文件、自校验 0 错误，索引自身 SHA `2f17fe6e9e9a0d883323f59f2d27a9913408bf6f28f5460bcbf735214340f25f`。

| 关口 | 实际结论与原始证据 |
| --- | --- |
| A 正常矩阵 | **12/12 通过**：两套各两任务 × seed 101–103。Robosuite 每例六次夹爪动作并读 depth；RoboCasa 每例真实 SDK 夹爪动作。每例 `result`／Raw Trace、原生 evaluator、独立 Safety（0 unsafe）、四类文件 SHA、Service 版本／配置身份与进程组回收逐项通过；本次原生任务成功均为 false，非此工程关口的失败。见 `normal_progress.json`、`runs/normal/` 和 `audit_final.json`。 |
| B Harness／分派 | **4/4 通过**：四任务各 seed 101 经 `formal_attention_cli` 建 run，1 attempt、0 credit、`autonomous`；run／attempt／entry lock、批准代码与配置 SHA、Formal result 和四类产物一致。错 suite runner 在执行前被拒，见 `harness_progress.json`、`runs/harness/`、`dispatch_mismatch_rejection.json`。 |
| C 故障路径 | **7/7 通过**：双 suite 的 timeout、cancel、Safety reject，另 RoboCasa 动作 job cancel。真实终态、原生 `evaluated`、独立 Safety、四类 SHA 和 Service／Agent 进程组回收均通过，见 `fault_progress.json`、`runs/faults/`。超时案例的 RoboCasa worker 报 `episode deadline reached during policy execution`，不是仅启动超时。 |
| D Robosuite depth 500 | **未通过**：旧原件无异常帧。核对旧 R `12bc69a` 的 `_metric_depth` 守卫可确认：首次夹爪动作后至少一个 normalized depth 值满足 `<0` 或 `>1`，才会抛出该异常、经 HTTP 500 导致 Safety `action_outcome_unknown`；单独的 NaN 不会触发旧守卫，有限越界值与无穷值仍无法区分。当前 R 的严格拒绝及 `.npy` 原帧捕获已在测试中验证；修订版 A 中 Robosuite 六例共 36 次动作无异常帧，另按旧序列 20 次新 Service 进程重放均完成，0 帧被捕获，初始／动作后 depth 各只有一个 SHA。见 `depth_investigation.json`、`runs/depth_exact_replay/`、`normal_progress.json`、R `tests/test_service_api.py`。这只限定了本次重复窗口，**没有确认具体异常值、像素或上游成因，也没有做针对性根因修复**。仍可能在其他渲染／时序条件再次出现 HTTP 500 并触发 Safety 中止；一旦重现，必须先保留 `.npy`、日志和 SHA，定位后修复，再完整重跑 A–C 及重复动作。 |
| E M1／M2 回归 | **通过**：旧 M1 双 lock SHA 重算分别仍为 `512796d49f6930f8a5ea8ccff749041abe6f88f2842577a827862be99bbbcd42`、`cf83204db5d8eea543f1f8b8b4196ea534c747b4aaef035427fc373f822107ab`；M1／Formal Runner 65 passed、M2 gate／Bridge 22 passed、R Service 5 passed。见 `m1_contract_recheck.json`、`test_commands.json` 和对应原始 stdout。 |

`audit_final.json` 对 A／B／C 的每例重新读四类文件 SHA，并用进程组 ID 检查当时的回收收据与审计时无存活进程；`service_process_scan.json` 无匹配残留。R／C／A／T 均保持冻结提交，U 从 `e1b76bd` 仅新增本验收文档与进度记录。RoboCasa 首版 seed 102 的失败为验收配置错误，源于任务语言跨 seed 变化；其拒绝原件与旧配置 SHA 保留，不能混算到修订版 12/12。M4–M6 不因 A–C 工程通过而启动或改变状态。
