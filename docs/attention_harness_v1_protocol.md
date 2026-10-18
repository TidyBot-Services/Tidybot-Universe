# AttentionHarness v1 协议（初稿，待冻结）

状态：**初稿**，2026-10-18。事实以主分支 `8ad49ea` 之后的代码为准；目标要求来自 `~/下载/timeline.md`。每个条目标注状态：**已实现 / 部分 / 未实现 / 待决**，并给出出处（文件:行号）。出处由只读核实得到，核实草稿保存在 `docs/_facts_for_protocol.md`（未跟踪，核实用；若与本文冲突，以代码为准并回头修订本文）。冻结前需用户逐条确认第 10 节。

## 1. 范围

- 主路径只有 RoboCasa365 + OpenArm365（Dev v2 引擎），两个注册任务 `pick_place_counter_to_sink`、`open_cabinet`，layout/style 11/34，开发场景 ID 101–125，held-out 1001–1010。Robosuite、LIBERO 不在四周承诺内。
- 实验目标：在固定的 Dev 模型、Eval、AdvisorProxy、任务/场景、初始源码和预算下，比较七种注意力策略的“原生成功 vs 人类注意力”。

## 2. 角色与信息边界

| 角色 | 作用 | 能看到 | 绝不能看到 | 模型/配置 | 状态与出处 |
|---|---|---|---|---|---|
| Dev | 每版写完整策略源码 | 公开任务配置、上一版源码、公开反馈（trace 投影、初/末公开观测）、Eval 的 passed/feedback、已收到的 hint、剩余预算；SDK 提示词 | 图像、原生评估器、仿真 oracle | `parcc/GLM`，温度 0，`max_tokens=8192`，provider retries 0 | 已实现：`dev_tasks.py:14-28,41-42`；`dev_v2_loop.py:211-227,293-298,602-610`；`dev_experiment.py:13`；`dev_configuration.py:11,38` |
| Eval | 判断每个已执行版本是否成功；不耗人类注意力 | 白名单文件：`trace.json`、`policy.py`（前 12000 字符）、`policy-stderr.txt`（后 4000 字符）、初/末公开观测；最多 10 帧、多视角（overview、side、top、左右腕）；任务 ID/prompt | 原生评估器、仿真 oracle、私有字段 | `parcc/Qwen`（视觉），温度 0，`max_tokens=4096`，默认 2 次×55 s，总超时 ≤120 s | 已实现：`v2_evaluator.py:38-42,120-253`；`eval_agent.py:12-16,28-43,53-80`。**默认关闭**（`dev_configuration.py:28`） |
| 策略（注意力策略） | 失败后决定：单独修复 / 求助 / 检查轨迹 / 取 Memory | 公开反馈、Eval 判定与反馈、已收到的答案、剩余帮助 | 原生评估器、仿真 oracle | 规则见第 8 节 | 已实现：`core/policies.py:234-264`；`dev_help.py:66-83` |
| AdvisorProxy | 模拟人类，回答 hint | 与 Eval 相同的公开文本证据 + 上一版 `reason`（截断 1500 字符作为 agent_hypothesis）+ selected_moments + 模板化问题 | 原生评估器、仿真 oracle、Eval 的原始输出 | `parcc/GLM`（**纯文本，占位**），温度 0，`max_tokens=1024`，默认 2 次×55 s | **部分**：`dev_advisor_proxy.py:12-24,59-78` 明确剔除 `frames`；帧的 view/hash 只进缓存键。**不能看图**，与 timeline 要求的视觉 AdvisorProxy 不一致 |
| 原生评估器 | 离线给每个版本打分 | 仿真状态 | — | 任务自带 `_check_success` | 已实现；结果只写入回执并在报告中读取。在 `dev_v2_loop.py`、`dev_help.py`、`eval_agent.py`、`v2_evaluator.py`、`dev_advisor_proxy.py` 中检索 `native_success|native_result`，只有回执引用，没有进入模型输入的读取点（静态检索，不能证明任意运行产物绝无污染） |

- Eval 与 AdvisorProxy 必须使用不同模型（`dev_configuration.py:28` 附近有检查）。
- Eval 的反馈对所有策略都进入下一版 Dev 输入；AdvisorProxy 只经由“agent 自己的假设”间接得到 Eval 信息。

## 3. 格子（cell）

- 定义（timeline）：任务 × 场景 ID × 策略 × 注意力预算水平 × 重复。**当前代码**的 cell key 仅为 `sha256(exp_id|task|seed|policy)`（`dev_experiment.py:20,146`），**没有预算水平和重复字段**（部分/未实现）。
- 选择字段白名单：`request_key, tasks, seeds, policies, help_budget, mode, policy_parameters, trace_visible, configuration, name_suffix, eval_agent, advisor_proxy, human_proxy, budget_tier` 及 16 个配置项（`dev_experiment.py:38-40`；`dev_configuration.py:5`）。
- 默认：`target_depth=3, target_valid_executions=3, max_versions=5, coverage=strict_full_depth`；`max_versions` 只要求正整数且不少于 target，无硬上限（`dev_configuration.py:9-40,72-81`）。
- 所有策略共享同一任务的初始源码：`dev_tasks.py:29-35,65-66` 固定 `openarm365_dev_v2/policies/<task>.py`，创建 manifest 时冻结一次（`dev_experiment.py:134-159`）。不同任务源码不同。
- 模式：`experiment`（正式数据，需场景合约和 Eval）、`interactive`、`diagnostic`（`dev_experiment.py:51-53`）。

## 4. 成功与停止

- 单个版本：执行后由原生评估器离线打分（只用于统计，不回流）。
- 格子成功：**任一版本原生成功**（`dev_report.py:65-117,140-157`，已实现）；同时报告最终版本成功、首次成功版本、到首次成功的秒数。
- 格子停止（`dev_v2_loop.py:580-600`）：Eval `passed=True` 立即以 `completed/eval_passed` 结束；否则达到 `target_depth` 且已结算执行数达标时 `completed/target_G_E_reached`；达到 `max_versions` 仍不完整则 `failed/max_versions_reached`。代码**不读原生成功来停止**（`dev_v2_loop.py:552-653` 未找到该分支）。
- 误判通过（false stop）：Eval 通过而最后版本原生失败，已在报告里统计（`dev_report.py:97-105,145-160`，已实现），作为次要结果必须披露。
- 未评估 / unknown 的版本不计为失败（`dev_report.py:65-117`）。整个格子有未解决状态时不进入已评估分母。
- 计数口径提醒：调度里的 `target_valid_executions` 按“已结算且非诊断的 execute”计，报告里的 E 按“native 已评估”计（`dev_v2_loop.py:590-596`；`dev_report.py:85-88,128-134`）。若出现已结算而 native 未评估，二者会分歧（待决，见第 10 节）。

## 5. 预算

| 档位 | SDK 调用 | 控制步 | 单次尝试 | 用途 |
|---|---|---|---|---|
| `legacy` / 正式档 | 80 | 2400 | 480 s | 旧实验字节复现；正式对比 |
| `dev`（Dev v2 默认） | 320 | 9600 | 1920 s | 开发与试点，不进正式对比 |

- 实现：`diag_budget.py:8-12,26-42,52-69`；`dev_config.py:70-88`；`formal_config.py:17-25,73-97`。`budget_tier` 写入配置与清单并进入哈希；显式 `legacy` 与改动前逐字节一致；缺字段按 `legacy` 读。正式边界拒绝 `dev` 档（已实现）。
- 给 Dev 的提示词数字按档位生成（`dev_tasks.py:16-28,41-42`）。
- 回执留痕 `dev_tier_budget`：worker reset 回执、runner 的 sandbox/native/trace/result、账本回执（`worker.py:28-31,109-112,171-172`；`runner.py:140-153`；`dev_ledger.py:205-208`）。
- 目标级与格子级预算随档位放大（`dev_ledger.py:8-21`；`dev_experiment.py:13-19,137-139`；`dev_configuration.py:60-79`）。
- **正式档的标定：未实现。**方案（待决）：对每个任务族取参考解实际用量（seed 102 参考：69 次调用、1101 步、约 430 s 含启动与证书）乘固定倍数（建议 2 倍），倍数在跑任何对比之前冻结。当前正式上限仍是旧的 80/2400/480。
- 探针专用的环境变量 `ATTN_DIAG_BUDGET_SCALE`（1–8，需 `ATTN_DIAG_GRASP_PROBE=1`）只用于诊断；diagnostic 以外的模式拒绝它（`dev_experiment.py` 的 `diagnostic_budget()` 检查）。

## 6. 请求类型与注意力

- 主矩阵只有 **hint**。枚举有 `hint/approval/interrupt`，**无 `review`**（`core/models.py:33-36`）。常规 Dev v2 通道把非 hint 请求改为 `stop/approval_request_rejected_no_authorization`（`dev_help.py:66-93`；`dev_v2_loop.py:248-276`）；approval 与 interrupt 留给真机和专项场景测试（`dev_planner.py:106-109,173-193,301-342` 的独立 planner 通道支持）。
- 注意力 = AdvisorProxy 的请求单位 + 逻辑人工秒数。一次 hint 预留 `advisor_calls=1, help_count=1, help_credits=1` 与 `hint_human_seconds`；`logical_seconds` = 执行秒数向上取整 + reading_seconds（默认 30）；targeted 请求按所选片段的视频时间跨度计（`dev_advisor_proxy.py:46-52`；`dev_v2_loop.py:508-522`）。
- **缺口：**账本只单列合并后的 `hint_human_seconds`，没有分别记录“实际观看秒数”和“阅读秒数”（`dev_ledger.py:8,28-31`）。timeline 要求两个分量分开记，以便在其它权重下重算前沿。部分。
- `help_budget`：每格 0–2 的帮助次数上限，默认 1（`dev_experiment.py:65-66,137-139`）。
- Eval 的成本是计算资源（`eval_calls`、token、时间），单独报告，不计入注意力。
- 等待人或 AdvisorProxy 的时间作为独立资源记录（timeline 要求；账本有 `help_wait_seconds`，UI 是否显示未验证）。
- Live 模式（真人先答、超时由 AdvisorProxy 接管并标记）：**未在主循环中找到该分支**（未实现）。

## 7. 无效格与重跑

- 场景合约不一致：runner 标记 `condition_invalid`，native `evaluated=False`；Dev loop 将格子置为 `condition_invalid/scene_contract_mismatch`（`runner.py:75-84`；`dev_v2_loop.py:355-370`）。
- Eval 无帧或重试失败：experiment 模式置 `condition_invalid/eval_vision_unavailable`；interactive/diagnostic 才允许 unavailable 回执（`dev_v2_loop.py:414-423,577-586`；`eval_agent.py:53-80`）。
- AdvisorProxy 失去视觉：**无对应分支**，当前它本来就是纯文本；异常进入 `store.unknown`，外层成 `needs_review`（`dev_advisor_proxy.py:1,20-24`；`dev_v2_loop.py:524-526,649-652`）。
- 一个格子 `needs_review/condition_invalid` 会暂停本实验后续调度并把仍 planned 的格子标 `not_started`（`dev_experiment.py:470,492-504`）。
- 报告按 task×policy 统计 `excluded_condition_invalid`，无效格不进入 eligible 分母（`dev_report.py:53,138-155`）。
- **未实现：**自动重跑无效格；“无效后重跑次数”按策略统计；Advisor 视觉缺失无效化。
- 规则（待冻结）：无效格不计入结果，重跑后再入账；按策略报告无效数，以确认无效化不依赖策略。

## 8. 七种策略

统一定义在 `core/policies.py:234-264`；所有策略共享同一 Eval、AdvisorProxy、初始源码、Dev 模型、预算。Eval 报告失败后，每个策略只能选：单独修复 / 求助（以及 inspect / 取 Memory）。

| id | 决策规则（当前代码） | 出处 |
|---|---|---|
| `autonomous` | 有匹配 Memory 取之；无证据先 inspect；否则 retry；从不求助 | `core/policies.py:87-95` |
| `demo_first` | 首次且 demo 可用则用预冻结源码；之后可取 Memory，否则 retry，不在线求助 | `core/policies.py:98-106`；`dev_help.py:48-60` |
| `reactive_help` | 每次确认失败求 hint；额度耗尽改 retry | `core/policies.py:74-84,109-113` |
| `retry_k_then_ask` | 连续失败 <k 时 retry，达 k 后求 hint；默认 k=2 | `core/policies.py:116-127`；`dev_help.py:12,24-31` |
| `budget_matched_random_escalation` | 按 `sha256(seed:slot)` 排序，前 `target_request_count` 个失败槽求 hint；默认 target=1、slots=2、seed=101 | `core/policies.py:130-153`；`dev_help.py:12,24-31` |
| `trace_aware_hint_only` | 共用 trace gate：安全停 / 证据不足 inspect / 局部可修复 retry / 重复无进展求 hint；不取 Memory | `core/policies.py:156-160,170-231` |
| `full_trace_aware_attention_planner` | 同一 trace gate，另可取 Memory、要求 approval、风险时 interrupt stop；求 hint 时若有 event ID 构造 targeted selected_moments 和缩小的 trace | `core/policies.py:163-231`；`dev_v2_loop.py:465-506` |

- “Demo-first”按 timeline 的定义是**预先写好的参考策略源码，不是视频**。
- 完整规划器与只提示门在代码里确有差别（Memory/approval/interrupt，以及只有完整规划器尝试 targeted 证据），但在常规 Dev v2 主通道里 approval/interrupt 不可达，且 Advisor 为纯文本，实际差别主要是 Memory 与 targeted 选帧元数据。这一点需用试点确认，**待试点决定**。
- 随机策略的目标请求数：timeline 要求由完整规划器的实测请求数在冻结前定，**代码默认固定为 1，未实现自动设定**。

## 9. 指标与汇总

- 主指标：原生任务成功对人类注意力（AdvisorProxy 请求单位 + 逻辑人工秒数，两分量分开）。Eval 成本单独报告为计算。
- 已实现（`dev_report.py:11-15,97-117,128-163,169-189`）：按 task×policy 的任一/最终版本成功率与 Wilson 95% 区间、首次成功版本与秒数、Eval 混淆矩阵与 false stop、G/E 与版本数、状态计数、无效格数、资源与帮助成本、wall 秒数、CSV/账本导出；整体 `formal_eligible=False`。
- 已在 worktree 开发中（未合入时以此处为准）：`summarize_experiment.py`、`eval_quality.py`（成功-注意力前沿数据、边际收益、fallback/timeout 计数、按预算水平配对）；与 `dev_report.py` 的重复部分需在合入时去重。
- 仍无：成功-注意力前沿的多预算点与图、边际收益、请求超时计数、按重复配对的噪声分析、私有进度读数（lift 高度、到水槽距离等，只可记录不可给任何角色）。
- 比较在同一场景 ID 内配对；Dev 在温度 0 下仍有波动，重复次数由试点方差决定后冻结。

## 10. 待你决定的设计取舍

1. **Eval 误判通过如何计入。**选项 A：只报告，不改变格子成功判定（现状：Eval 通过即结束格子，成功率以原生为准）；B：给“误判通过”的格子单独标注并在主表同时给出“剔除误判通过后”的结果。推荐 A+B，并预先设定可接受的误判通过率（R7），超过则不允许 Eval 通过结束格子。
2. **Eval 是否看 `policy_source`，是否写明成功规则。**一次真实 Qwen 冒烟（3 次调用，原生 False/False/True）出现 1 次误判通过，原因是 Eval 拿策略代码自己的目标点当真值。选项：(a) 去掉 `policy_source`；(b) 保留，但提示词写明“策略自己的目标点不能作为完成证据，以公开观测里的水槽位置和画面为准”；(c) 提示词写入任务的成功规则文字（物体在水槽内、夹爪远离物体）。不泄漏本次执行的真值，TidyBot 原做法是 Dev 在仿真里可直接读 `/task/success`，我们不能照搬（会使“何时求助”失去意义）。推荐 (b)+(c)，再用同样三次执行复测（需再次授权模型调用）。
3. **AdvisorProxy 的视觉模型。**当前 GLM 纯文本为占位，选帧只进缓存键。选项：先选定一个视觉模型（须与 Eval 的 Qwen 不同），或在冻结文档里把“Advisor 无视觉”写为已知局限并加“Advisor 视觉开/关”消融。推荐先限定为已知局限，试点后再换。
4. **主矩阵的 Memory 初始状态。**默认空，且矩阵内不晋升；也可用一组预先验证的条目。推荐空。
5. **正式档预算倍数。**推荐“参考解用量×2”，倍数冻结前定死，不看结果调整。
6. **held-out 1001–1010 是否做 reset-only 指纹探测。**不含性能信息，但会接触 held-out；若做须在冻结记录里写明。
7. **`target_valid_executions` 的计数口径**：调度用“已结算 execute”，报告用“native 已评估”。推荐统一为后者，或在两处都显示。
8. **cell 中的预算水平与重复字段**：代码没有，需新增才能做多预算点前沿和配对噪声分析。

## 11. 场景门与冻结

- 场景合约：**仅登记 101、102、103**（`scene_contracts/dev-seeds-101-103-v2-20261016.json`，2026-10-16 构建，每个条目 20 个新进程、容差 1e-6；sink 各 40 次 reset、cabinet 各 60 次；`scene_contract.py:35-53`）。experiment 模式必须 `reproducible_reset=True` 并有覆盖每个 task×seed 的 v2 合约（`dev_configuration.py:42-59`）。
- 未实现：104–125 的合约、固定动作 rollout 指纹。
- held-out 1001–1010 在 `dev_tasks.py:8-13,59-66` 登记，开发实验选择会拒绝它们。
- **场景可行性（新增要求，timeline 2026-10-18 已记）。**并非每个登记场景都能被做成：101 的目标（马克杯）两指最大张开 80.5 mm 抓不住；103（勺子）底座起点余量 2.53 mm（低于 3 mm 起点规则）；只有 102 已证明可行。进入试点和主矩阵前，每个场景需通过可行性筛查（目标宽度、起点余量、抓取 IK、搬运 IK 与关节 7 余量），工具 `scene_screen.py`（筛查中）。
- 冻结与签字：冻结前须对以下内容算哈希并签字——任务、场景 ID、held-out 集合、预算档与水平、重复次数、策略及其参数、提示词（含预算数字）、Eval 与 AdvisorProxy 的模型与缓存版本、场景合约。Dev v2 当前所有结果 `formal_eligible=False`，**没有 admission 路径**（`dev_experiment.py:155-225`；`dev_report.py:159-161`；`formal_config.py:85-88`）；冻结流程是 Week 2 的交付物。
- 开发试点（R1–R8）：付费、仅开发场景 ID、不计入结果；用什么场景由第 7 节之外的“场景可行性筛查”决定，不再默认 101–103。

## 12. timeline 与代码的不一致（核实时发现）

1. Eval 在 timeline 里是“每版、每策略都跑”，代码里默认关闭，只在显式启用时成立。
2. timeline 说多视角图供 AdvisorProxy 使用；实际 AdvisorProxy 剔除图像，只收文本。
3. timeline 的 cell 含预算水平和重复；代码没有这两个字段。
4. timeline 把“Eval 对原生评估器的混淆矩阵”“误判通过”列为待做；`dev_report.py` 已实现（97–105,145–160）。
5. timeline 要求阅读秒数与观看秒数分开记；账本只记合并值。
6. timeline 的“Week 2 汇总脚本”：`dev_report.py` 覆盖了其中一部分，前沿/边际收益/超时/回退计数尚缺；新写的 `summarize_experiment.py` 与 `dev_report.py` 有重复，合入时需去重。
7. timeline 说 Live 模式下真人超时后由 AdvisorProxy 接管并标记；主循环里未找到该分支。
8. timeline 写“planner features 与 proxy 同时开会被拒”；实际条件更细（memory-only features 把等待/approval 置零），不能泛称“所有 Memory+Proxy 都被拒绝”（`dev_configuration.py:17-23`；`dev_v2_loop.py:237-245`；`dev_planner.py:24-39`）。
9. timeline 说随机策略目标由完整规划器实测定；代码固定为 1。

## 13. 本文未核实的内容

UI 与 Memory 的功能完成度（只确认了入口：`dev_ui_server.py`、`dev_ui/app.js`、`memory_service_api.py`、`dev_planner_memory.py`）；真实运行中旧配置、v3 与 planner features 全部组合的行为；试点结果；Advisor 视觉可用性；正式签字状态。这些只能靠运行验证，不由静态检索证明。
