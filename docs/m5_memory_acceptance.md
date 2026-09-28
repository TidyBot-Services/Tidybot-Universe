# M5 Memory：工程验收冻结包

冻结日期 2026-09-28。Universe 基线为双亲合并提交 `0e29a7dd9156af64015420eae2b5fcec651d2bc5`（父提交 `7c6890f`、`dc06af9`），目标仅为 `feature/attention-native-robosuite`。独立依赖起点：Memory Service `24d4146`、Robosuite Service `081cd57`、RoboCasa Service `320020a`、Agent Server `4cf4daa`、RoboCasa task source `b18bbf1`。执行新证据时须再次记录各 HEAD、脏状态、代码和配置 SHA；版本不同不得冒充旧证据。所有运行 `formal_eligible=false`。

## 范围、责任与证据

仅验收 Memory Agent 候选提炼／同 `task_id` 配对调度、独立 Memory Service 权威状态／晋升／检索／授权／生命周期、Harness 双套模拟器运行与原始证据、Dev Agent 的 Memory 暴露和回执接口。M1–M4 锁、正式边界、原生 evaluator、独立 Safety 和预算不降级。不含 M6 UI、held-out、真人、七策略正式效果矩阵或正式实验准入。

| 关口 | 通过标准 | 证据位置 |
| --- | --- | --- |
| A 来源与候选 | 正式失败 Trace 的身份、四类产物 SHA、独立 Safety 和原生失败判断可重算；真实 Advisor 或真实指导的答复、请求及原始引用进入候选，明确 suite／`task_id`／perception 适用范围；伪造或变更原件被拒。Dev 暴露须精确版本授权并另记开发结果，不算运行期使用。 | `benchmarks/attention_harness/{memory_agent.py,memory_validation_task.py,sim_gt_memory.py}`；Memory Service `memory_v2.py`、`dev_use.py`；旧源归档 `protocol/v2/evidence/week2_followup_2026-09-27/continuation/original_sources/`。 |
| B 配对与晋升 | 每候选在结果产生前记录 plan、依赖版本、同 `task_id` 的至少五个不同开发 seed，逐例固定 scene／object／camera／task 表述及真实相机、prompt、同一策略和配置摘要；各 control／treatment 一对完整运行，control 零候选暴露，treatment 精确候选暴露。独立 Safety 和原生 evaluator 均权威，Trace／答复／配对／四类产物及 SHA 逐例一致。仅 Memory Service 可晋升；至少 3 个 treatment 成功且成功率 ≥0.75，有成功增益或成功配对求助节省，零逐对 Safety 退步、零净 Safety 增量，所有预定 case 完成。重启只复用已持久化臂／配对／晋升。 | `memory_agent.py`、`memory_validation_task.py`、双 suite `paired_trials.py`、Service `memory_v2.py`；新候选隔离证据目录需含 freeze、plan、原始 10 arms、权威 SQLite、impact、审计及 SHA 索引。 |
| C 限定使用与生命周期 | 两套模拟器各有与配对分离的独立正式 run；匹配范围内检索、Service 对 attempt 颁精确版本 grant，Trace 中实际使用并记录原生结果；范围外无检索／授权。expiry、disable、rollback 后检索与新授权拒绝，先前已使用的结果仍可审计。正式 run 不借用配对结果。 | 正式 Harness run JSON、SQLite、Trace、grant／use、Safety、native_result 与 SHA；Service `memory_service.py`／`memory_v2.py`。 |
| D 旧失败与边界回归 | 旧七项逐例复核配置、测试代码、运行 Trace、答复、配对、安全、原生结果和 SHA；修复后七项及 M4 相关边界全通过。Robosuite 既有 0/5→5/5 成功证据按整合版本复核必要接口，不把历史真实服务运行写成新版本重跑。 | 旧回归日志、`test_memory_v2.py`、`test_robocasa_generated_policy.py`、M4 定向测试；`week2_graph_live_2026-09-27.json` 及其归档。 |

## 预算与旧反例

旧 RoboCasa 候选和原始配对**不得重写**。`continuation/robocasa_validation_freeze.json` 固定 seed 101–105、每 seed 先 control 后 treatment、每臂 0 求助、最多 200 SDK 调用、策略 240 秒、Agent job 90 秒、Service 300 秒及 Safety 原门槛。已记录 control 0/5、treatment 1/5；seed 105 独立 Safety 1 次退步，Memory Service 拒绝晋升，权威状态 candidate v1。原件、两个不完整尝试和反例留于同目录 `robocasa_pair_attempts/`、`authority/`、`robocasa_pair_audit.json`，以 `archive_manifest.json` 校验。旧候选不可为满足门槛而改结果、删 seed 或收窄已冻结计划。

若提出新 RoboCasa 修复，须另建候选 ID 和隔离权威库，在任何结果出现前冻结新的策略／配置／版本 SHA、上述同等或更严的预算、五个以上不同开发 seed 与全部四轴变体、执行顺序及原件位置；先预检再跑全部 control／treatment。新修复不得借旧候选的 1/5 成功或覆盖其反例。没有可运行有效修复与新冻结时，不执行配对、不请求晋升。

## 关闭条件

A–D 全部通过、双套独立正式使用与拒绝／生命周期成立、全部原始 SHA 可复核，才把本包、`STATUS.md`、`docs/attentionbench_progress.md` 标记为通过并关闭 M5。任一未达，明确记录阻塞并停在 M5；不得进入 M6。历史成功和单测仅按各自证据级别陈述。

## 本轮执行与阻塞（2026-09-28）

M5 代码提交 `820a5e5` 修正旧七项测试的假环境变体：独立 seed 107 需使用与其 reset 后任务表述一致、且已验证的奇数组 seed 103 范围。原断言之前被 prompt attestation 正确拒绝，不能用放宽运行时门槛来让它通过。M5 代码提交 `24975b5` 修正另一处真实接口问题：Service 已对完整 scene／object／camera／prompt 计划做限定检索，Harness 决策此前却用候选仅有的 suite／`task_id`／perception 字段去比较完整上下文，导致已返回的 trusted Memory 不能被选中。现仅复核三项稳定身份，具体变体仍由 Service 独占判断。独立 Memory Service 保持 `24d4146`，没有改变晋升门槛。

旧失败原件 `/home/truares/桌面/attentionbench-m4-20260928/harness-full.log` SHA-256 `a9def67a385373270c479acc45efa5d9662a7e4aefbb09d1cbfc9d07effb836a`，当时为 7 failed。稳定复测 `/home/truares/桌面/attentionbench-m5-20260928/seven-tests.txt` 为 **7 passed、21 deselected**；`seven-audit.json` SHA-256 `c50eb17d9b0526e36eb1bd47ba55b922f0c06988b6e519e32260ca86c28cd494` 逐例列出配置对应的运行 seed／状态、原生结果、Trace ID、答复、5 对 fixture 配对、10 个 Safety fixture、使用记录及 673 个文件 SHA。七项分别覆盖 evaluator 不可用、disable、rollback、expiry、使用后 disable、候选来源与五对晋升、sandboxed generated policy 精确版本授权。**这是确定性回归，不是新的真实 RoboCasa 配对**。当前全 Harness **382 passed、10 skipped**，`harness-final.txt` SHA-256 `da2164bcf4d6744470665c3c6368333bd3b4fd70aee01bd9b58a221a4c70b3bb`；Memory Service 自身 22 passed，M4／调度定向 73 passed。

旧 RoboCasa 续档 `continuation/verify_continuation.py` 只读复核 10 arms 与 candidate 状态通过；`archive_manifest.py verify` 校验 **767 文件**通过。原 `robocasa_pair_audit.json` SHA-256 `73ba8ea00a50bd8334b0a6cebfc715ca1239d5e785c6b2504deb49540d63ba6a` 未改，0/5 对 1/5 与 seed 105 Safety 退步继续阻止晋升。旧 Robosuite 可迁移总归档 SHA-256 `a8342b98b9b2910d62acad092b653de9c7b5fe7cba3b0ea8b83eeb5a3bf1d8f3`（858 文件）复核通过；当前接口只读审计 `robosuite-compatibility.json` SHA-256 `8c48675bf7558e8af9616e1a2cf038be7a0b919d5cb1d24936e2fc05fdd3407f`，历史 5 对、trusted v1 和独立 run 成功仍是**历史版本**的事实。

为复核整合版本，先在隔离权威库副本执行一次正式 seed 103。首轮把来源证据复制到错误的 artifact root，被 Service 以 `raw source evidence is missing or outside artifact root` 拒绝；第二轮修正来源位置后完成两次正式 attempt，却因上述 Harness 决策缺陷没有采用 Memory，原始 `robosuite-current/` 与 `robosuite-current-v2/` 均保留。修复后在结果前冻结 `robosuite-current-v3/freeze.json`，SHA-256 `c2ae413f3ca5b53f0e1fd73f257237f345bdf6c660bfdad38f65672868a35f60`，双 attempt／0 求助／0 provider 调用／总 300 秒，复用旧 trusted 权威库的**隔离副本**，不重跑配对或晋升。此轮两个真实正式 Runner attempt：首个原生失败，第二个匹配范围检索并获得 Service 的 v1 attempt grant、实际使用且原生成功；独立 Safety 两次均 0，四类产物 SHA 均核对。范围外拒绝，以及各自权威库副本上的 disable／rollback／expiry 后拒绝新检索与授权已复核。`robosuite-current-v3/audit.json` SHA-256 `59a4baaba6c957ca47abbf9df80aea8f2fa654929274230b5c122987c2e0d2fe`；两次运行仍 `formal_eligible=false`。

## 新 RoboCasa 候选的预冻结配对与结果（2026-09-28）

在任何新 arm 运行前，另建 `candidate:m5:robocasa-counter-to-sink-public-sdk-v2` 和隔离权威库，并冻结 `robocasa-new-v2/freeze.json` SHA-256 `b678836230eb9eee26c243ea5ddbe28c566b077efcb2509e2d0ec05f26225680`。修复代码 U `10c5e8d`、策略 SHA-256 `2c48d0a864e55de6a8bb0d044262390aa4dae6a5a3a286f367068ec8fbd03c1c`；原正式失败 Trace、真实 GLM 答复／指导及其原始 SHA 被复制到隔离证据根并经 Service 验证。新 plan SHA-256 `15563eaece1283c2bbaa62689ba2216a2ecd5625f7eda24d6df15cc6fad2214e`，固定同一 `counter_to_sink` 的 seed 101–105、场景／物体／相机／任务表述四轴、control→treatment 顺序及旧预算和门槛。旧候选 ID、权威库、0/5→1/5 和 seed 105 Safety 反例未被覆盖。

新候选十个真实 arm 在独立 RoboCasa Service 与 Agent Server 进程组中跑完，按臂保存 result、Trace、Safety、bundle、trial 配置与 SHA。control **0/5**、treatment **1/5**；seed 101 treatment 原生成功，seed 102 杯子公开观测未确认抓起，seed 103 芒果策略执行完成但原生失败，seed 104 洋葱动作产生一次独立 Safety `action_outcome_unknown`，seed 105 rolling pin 安全弃权且原生失败。五对同配置摘要均由 Service 登记；重启逐臂复用十份收据，未再执行配对。`robocasa-new-v2/impact.json` SHA-256 `12c56b82ce10143c9a77f1d43e4181e903bb5ac29003be7706f98ec08bf521aa`；`audit.json` SHA-256 `942069e9cacfe4c55997df6a998038ce3a53d81b212b511793e1f675553d36ef` 逐臂复算源、配置、原生 evaluator、Safety 与配对，并记录 Memory Service 因 Safety 退步拒绝晋升、状态仍 candidate v1。`file_index.json` SHA-256 `b1053b95f23fe67295dc8f1095f4a1787e119e82f138cb9a119fa520bbbc44cf` 覆盖 176 个文件。证据根为 `/home/truares/桌面/attentionbench-m5-20260928/robocasa-new-v2/`。原续档 `verify_continuation.py` 再次通过，旧 `robocasa_pair_audit.json` SHA-256 仍为 `73ba8ea00a50bd8334b0a6cebfc715ca1239d5e785c6b2504deb49540d63ba6a`。

接口审计：`memory_agent.py` 在 Service 来源核验后提炼新候选并持久化同 `task_id` 的四轴五组计划，本次十臂由它生成；`attention_memory_service` 独占配对登记和晋升，实际拒绝两版 RoboCasa 候选，旧 Robosuite trusted 的范围／版本／生命周期检查在整合版本独立 run 复核。Harness 的 `paired_trials.py` 确认双臂同配置摘要与公开 SDK 执行，`sim_gt_memory.py` 的可信候选选择缺陷已修复并复测。Dev Agent 的 `attention_memory_dispatch.py` 对含批准验证配置的 RoboCasa 仍无双 Service 自动派发路径，直接写 `blocked`；本次隔离执行器不冒充自动图闭环。这一接口缺口及 RoboCasa 原生效果／Safety 是 M5 后续修复项。

**关口结论：M5 部分完成且明确阻塞，未关闭。** A、D 的已述范围、Robosuite 的 C 子项及 RoboCasa 的预冻结完整配对已有证据；新旧 RoboCasa 候选均未达到 B 的原生成功与零 Safety 退步门槛，因此不得晋升，也不得声称 RoboCasa 的 C 独立正式使用通过。`attention_memory_dispatch.py` 的 RoboCasa 自动验证分支仍直接记录 `blocked`，是 Dev Agent 接口的待修问题；新候选此次经 Memory Agent 计划调度和独立执行器完成，但不能被自动图代替。若再提出修复，须另建候选并在结果前另冻完整计划，保持本次反例。所有运行 `formal_eligible=false`；不进入 M6。
