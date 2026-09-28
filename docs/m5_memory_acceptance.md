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

## 修订验收条款（2026-09-28；原冻结门槛和结论保持不变）

本节只修订 **M5 工程机制关闭口径**。上文 B 的 ≥3 个 treatment 成功、成功率 ≥0.75、有效增益、零 Safety 退步等晋升门槛，以及旧 RoboCasa 0/5→1/5、seed 105 和新候选 0/5→1/5、seed 104 的反例，均不变。上述“未通过、未关闭”是原口径历史结论，不回写为通过。本轮不寻找 RoboCasa 成功策略，不运行第三轮十臂配对；旧配对、Safety、原始 SHA 只读复核。

| 修订工程关口 | 通过标准 | 证据要求 |
| --- | --- | --- |
| R1 Robosuite 正向链路 | 已有五对有效配对达到原门槛，Memory Service 权威状态为 trusted v1；与配对分离的独立正式 run 对匹配范围检索、颁给该 attempt 的精确 v1 grant、实际使用及原生结果都有持久记录。范围外不返回或授权。 | 旧配对归档、权威 SQLite、整合版本正式 run、Trace／Safety／native／sandbox SHA 和审计；历史配对不得冒充本轮重跑。 |
| R2 RoboCasa 负向链路 | 已有完整失败配对及 Safety 反例复核；候选仍为 candidate v1、Service 拒绝晋升。与配对分离的正式 run 在匹配范围也不能把该候选作为 trusted Memory 检索或授权；强制精确版本授权须拒绝。记录 run 的原生判断和独立 Safety。 | 两版旧审计和权威库、独立正式 run、拒绝收据、四类产物 SHA；不得表述为 RoboCasa 正向效果成功。 |
| R3 Dev Agent 自动验证 | 经正式结果与批准验证配置自动派发 RoboCasa 验证任务，核对来源、审批策略和五组四轴计划；每个实际执行臂由独立 RoboCasa simulator 与 Agent Server 两个 Service 完成并留终止收据。已持久化臂／配对在重启时只复用，缺收据或遭篡改时 fail closed，不重复执行。不得要求第三轮配对。 | Graph／dispatch 状态、真实双 Service 臂收据、进程组回收、重启复用及拒绝测试。单臂 Service smoke 与完整 Graph 派发须分别陈述。 |
| R4 完整性和回归 | 来源与批准文件 SHA、防篡改、范围外拒绝、expiry／disable／rollback、独立 Safety、原生 evaluator、旧七项及相关调度／Service 回归均有可核对证据。所有新工程运行 `formal_eligible=false`。 | 不覆盖原始索引；另存本轮审计、命令／结果和 SHA。 |

仅 R1–R4 全部通过时，标记 **“修订口径下工程完成”**，并在本包、`STATUS.md`、模块进度页留下独立待办 **“RoboCasa 正向 Memory 效果未验证”**。任何一项未过则列出未过项、M5 继续开放。此修订不授权 M6、held-out 或正式效果矩阵。

### 修订执行结果（2026-09-28）

版本核对：U `c7580cde06ce3b2a890298d32469ccbfecc569ed`（本修订前工作树干净）；独立 M `24d414638ad2cdd6557f09f042e8b6ef2b597b0f`、R `081cd57ec9383895dc150050ca5d05c24628e7fa`、C `320020a0c94434af31ec02df3413229576490fef`、A `4cf4daaba61d4cbbb0ca6daaa4ff28165c9daf1b`、T `b18bbf1585c42e370ae45ababdddad700cc2c71d` 均为干净工作树。修订证据根 `/home/truares/桌面/attentionbench-m5-revised-20260928/`；独立索引 `revised-evidence-index.json` SHA-256 `5f306ef83cc9c0f6a6c0bf516c34be386f6af9db82e0af6526511778f9e6751d`，104 文件；`revised-audit.json` SHA-256 `a677f6d3db2e509c5ad0a5fe0d73f8cc2fb7cecd591e14fa79dd050e85368ff3`。未运行新的配对臂。

| 关口 | 结论与证据 |
| --- | --- |
| R1 正向 | **通过。** 原 Robosuite 五对、Service trusted v1 与整合版本独立正式 seed 103 的先失败、后限定检索／精确 v1 grant／实际使用／原生成功，由原 `robosuite-current-v3/audit.json`、本轮逐件四类 SHA 复核。范围外和 expiry／disable／rollback 拒绝沿用原隔离副本审计，不把历史五对称作本轮重跑。 |
| R2 负向 | **通过。** 旧 RoboCasa 767 文件 archive 再验证，新候选 176 文件索引逐件 SHA／长度复核；旧 seed 105 和新 seed 104 Safety 反例仍在。新候选 candidate v1，Service 原拒绝晋升不变。隔离权威库的独立 seed 101 正式 run 在匹配上下文完成两次真实 Runner attempt，均原生失败、独立 Safety 0、双 Service 回收；两次均无 trusted Memory ID／retrieval event，Service 检索不返回该候选，强制授权报 `memory is no longer trusted or applicable`，新 grant/use 均为 0。冻结 `robocasa-negative-freeze.json` SHA-256 `fc163a23a31ba2cc9198b227ead9927767fd468454a739f61826224da0670d2d`；CLI exit 1 为原生失败，运行 `formal_eligible=false`。**这是负向隔离证据，不是 RoboCasa Memory 效果成功。** |
| R3 自动验证 | **未通过完整集成验收。** `attention_memory_dispatch.py` 的 RoboCasa 分支已接通；单臂执行器 `robocasa-dispatch-smoke-v5/receipt.json`（SHA-256 `59b3e71310a7e22a41d62b720a32fe1eb14141d7bbdede101610bfc2aa34f3e0`）证明真实 simulator／Agent Server 运行、独立 Safety 与两进程组回收，本轮重启只复用该收据。持久任务对五对已登记配对的自动 dispatch 和重启均为 blocked、复用 seed 101–105、0 新臂；本轮用来源正式 run 的**明确标注派生隔离 fixture** 驱动派发，`dispatch-reuse-fixture/fixture-provenance.json` 记录改写的路径字段。该 fixture 不能冒充原始正式来源；真实双 Service 单臂与自动派发复用分属两次验证，尚无一次由未改写正式来源的自动派发直接执行真实双 Service 臂并重启复用的完整收据。按冻结 R3 标准保持未过。 |
| R4 完整性／回归 | **已通过已覆盖项。** 新 run 四类 SHA、原生 evaluator 与独立 Safety 逐 attempt 复核，R1 范围外及生命周期拒绝沿用隔离审计；来源／批准文件与臂收据篡改、未落盘臂 fail closed 由 `test_memory_validation_task.py` 覆盖。独立 Memory Service **22 passed**，相关 Harness／dispatch **30 passed**，全 Harness **387 passed、10 skipped**；旧 767 文件归档验证通过。测试原始日志及命令在修订证据根。R3 的端到端缺口不由这些回归替代。 |

**修订结论：M5 仍开放，未达到“修订口径下工程完成”。** 唯一未过工程项为 R3：缺未改写正式来源触发的自动派发→真实 RoboCasa 双 Service 执行→重启收据复用的同一链路证据。原 B 成功率／Safety 门槛仍未通过，两个 RoboCasa 候选继续未晋升。独立待办：**RoboCasa 正向 Memory 效果未验证**。不进入 M6、held-out 或正式效果矩阵；所有本轮工程运行 `formal_eligible=false`。

### R3 补验与最终修订结论（2026-09-28）

上段是补验前的阶段性结论，保留。补验复用**原候选**的既有五对，不新增 arm：原 Graph 正式来源 `attention_run.json` SHA-256 `3f4ced4fd046b059e60c27b67ab36be1dd30a6d4c8d47b61bdb2b34df31e90f3`、权威库初始 SHA-256 `6cd822f5e39f0e8feb8d369044ee51ead8a961e9cdf5fa68bc96f95c08e62cdf` 和 52 个原文件先逐字节复制，再用私有挂载映射回原路径。生产 `attention_orchestrator.py` 的恢复入口从**未改写的正式来源**读取获批验证配置，调用真实 `dispatch_memory_candidates`；诊断 Eval 文本明确使用测试 fixture，不参与 Memory 判断。Graph 状态和派发函数各由独立进程重启，均保持同一 blocked 收据、五对已登记配对、0 新臂，Service 再次以 seed 105 逐对 Safety 退步拒绝晋升。原 Graph 与来源目录补验后没有文件增删或摘要变化。

十个复用的 arm 逐一与 Graph 任务的 pair ID／attempt ID、原 `robocasa_pair_audit.json` 的 result／Trace／Safety SHA、原生 evaluator 和 simulator／Agent Server 双进程组回收收据对齐；当前派发执行器的另一次真实双 Service 单臂 smoke 及收据复用单独列证，不称作本次 Graph 新执行臂。`memory_validation_task.py` 和 `attention_memory_dispatch.py` 现于 blocked 重启时重新核对 Service pair payload 和每对 Safety 原件；隔离副本中改 pair 记录、只改 Safety 文件两种篡改均在复用前拒绝，原件不变。`exact-source-overlay/r3-exact-audit.json` SHA-256 `4cafd58946d0a747bd4c91aedb477d59d42c1aee8610dfa5604c3bdf381e34c3`；补验冻结 `freeze.json` SHA-256 `7967358f4136fa75636c95bf4dd321777a49e786af8af168dcd4c41f44e7e579`。当前全 Harness **388 passed、10 skipped**，Graph 定向 **25 passed**，配对完整性定向 **31 passed**，独立 Memory Service **22 passed**。

另以独立权威库副本模拟 seed 105 pair 尚未登记、但两份**旧真实臂收据**已落盘的重启检查点；Graph 恢复时逐件验收旧 result／Trace／Safety／trial config SHA 和双 Service 停止收据，再从这两份收据登记与原库**字节相同**的 seed 105 pair。attempt 总数前后均为 14，pair 从 4 恢复为 5，0 新臂；下一独立进程重启时 pair 数及状态／Graph 摘要不变，Service 仍因原 Safety 反例拒绝晋升。`arm-receipt-resume/audit.json` SHA-256 `3b206a968ced48edd4185cc17f3a9fb1626ace5b20de0b80a9d1892c5811eb86`，预运行冻结 `freeze.json` SHA-256 `d395d1c70c695036a54c65b625ab10aa61931ba1a5305bcefb6da4217d67a8aa`。仅隔离副本删除并恢复 pair；原权威库、十臂、Safety 反例和原始 SHA 均未改写。

**R3 按上述既有臂与检查点收据复用口径通过，R1／R2／R4 保持通过；M5 仅标为“修订口径下工程完成”。** 这证明自动派发、双 Service 既有臂和重启复用的工程机制，不声称 Graph 新启动了十臂或 RoboCasa 候选晋升。原 B 门槛及两版 RoboCasa 未通过／Safety 反例不变；独立待办：**RoboCasa 正向 Memory 效果未验证**。先前最终审计 `/home/truares/桌面/attentionbench-m5-revised-20260928/revised-final-audit.json` SHA-256 `a80fae78133838ba39fff25d188defa7b23bce1aeec57ec06de68583d78677fd` 保留；纳入臂收据补验的最终审计 `revised-final-audit-v2.json` SHA-256 `010e65e6bb0717b26d9506a688f48207c70ad030c7f84415005805f30b359de0`。最终独立索引 `revised-final-index-v2.json` 覆盖 433 件证据及当前三处代码 SHA，SHA-256 `c8aca0969aa8b250d689df79dc43a31bbee4639f1717014a13d7c036e509e995`，逐文件复核通过。所有工程运行 `formal_eligible=false`；M6、held-out 和正式效果矩阵仍未启动。

### 后续独立正向风险验证（2026-09-29）

上述两版 RoboCasa 候选失败结论均为当时原件，继续有效。[新 v3 风险审计](/home/truares/桌面/attentionbench-depth-memory-risk-20260929/risk_status.md)另建候选及隔离库，在结果前冻结同一策略和 seed 101–105 五种四轴变体；十个真实双 Service 配对臂 control 0/5、treatment 5/5，Safety 0/10，达到原 B 门槛，Service 晋升 trusted v1。配对外独立正式边界工程 run 在首个无 Memory 失败后，第二个 attempt 获得匹配范围的精确 v1 grant、Raw Trace 检索事件和 `native_success` 使用记录，原生成功、Safety 0；范围外及隔离副本的 disable／rollback／expiry 均拒绝新授权。配对审计 SHA-256 `67a0834b390ce6ece11f0e75e521a8f6e2985e6e79c673a98b7dbba6ae82b7dd`，独立使用审计 SHA-256 `8828e45c78bdc07a795005b738c2c823412306aeefc5f6fe54245ac8e4408058`。这些是后续工程证据，不倒填 2026-09-28 的 R2 负向检查或 Graph 自动派发链路；所有运行仍 `formal_eligible=false`，未用 held-out，未启动七策略正式效果矩阵。
