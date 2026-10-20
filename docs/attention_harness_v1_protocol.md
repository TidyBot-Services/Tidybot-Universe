# AttentionHarness v1 协议（初稿，待冻结）

状态：**初稿**，2026-10-18 起草，**2026-10-19 按当日决定修订**（修订处标「10-19」）。事实以主分支 `8ad49ea` 之后的代码为准；目标要求来自 `~/下载/timeline.md`。每个条目标注状态：**已实现 / 部分 / 未实现 / 待决**，并给出出处（文件:行号）。出处由只读核实得到，核实草稿保存在 `docs/_facts_for_protocol.md`（未跟踪，核实用；若与本文冲突，以代码为准并回头修订本文）。冻结前需用户逐条确认第 10 节。

## 1. 范围

- **10-19 测试版范围（用户决定）：只用场景 seed 102、任务 `pick_place_counter_to_sink`。**104–125 筛查、整机底座预筛分支（`screen-20261018`）、额外种子的合约探针搁置（不取消）。只有一个场景时，所有比较是同一初始条件下 Dev 重复运行的比较，只支持工程演示，不支持“策略跨场景泛化”的结论。
- **10-19 免费模型：**`parcc/GLM`、`parcc/Qwen` 的 token 与评审调用只记录、不限额（`free_models.py`）；人的注意力、版本数、机器人时间、墙钟、AI 思考时间仍受限。
- 主路径只有 RoboCasa365 + OpenArm365（Dev v2 引擎），两个注册任务 `pick_place_counter_to_sink`、`open_cabinet`，layout/style 11/34，开发场景 ID 101–125，held-out 1001–1010。Robosuite、LIBERO 不在四周承诺内。
- 实验目标：在固定的 Dev 模型、Eval、AdvisorProxy、任务/场景、初始源码和预算下，比较七种注意力策略的“原生成功 vs 人类注意力”。

## 2. 角色与信息边界

| 角色 | 作用 | 能看到 | 绝不能看到 | 模型/配置 | 状态与出处 |
|---|---|---|---|---|---|
| Dev | 每版写完整策略源码 | 公开任务配置、上一版源码、公开反馈（trace 投影、初/末公开观测）、Eval 的 **passed 与客观原因 `reason`（10-19：不含 `reference_suggestion`，所有策略一致）**、已收到的 hint、剩余预算；SDK 提示词。**10-19 核实：公开观测的感知来源是 `official_public_sim_gt`（`perception_mode=sim_gt`），Dev 经 `sensors.find_objects()` 拿到物体/水槽的模拟器真实坐标（置信度 1.0）；这是“公开”的一部分，但本质是模拟器真值坐标** | 图像、原生评估器、仿真 oracle | `parcc/GLM`，温度 0，`max_tokens=8192`，provider retries 0 | 已实现：`dev_tasks.py:14-28,41-42`；`dev_v2_loop.py:211-227,293-298,602-610`；`dev_experiment.py:13`；`dev_configuration.py:11,38` |
| Eval | 判断每个已执行版本是否成功并给出**客观失败原因**；不耗人类注意力。**10-19：输出 v3 三项：`passed`、`reason`（只讲事实，含建议用语的句子被移走）、可选 `reference_suggestion`（单独标注，给 advisor）** | 白名单文件：`trace.json`、`policy.py`（前 12000 字符）、`policy-stderr.txt`（后 4000 字符）、初/末公开观测；最多 10 帧、多视角（overview、side、top、左右腕）；任务 ID/prompt | 原生评估器、仿真 oracle、私有字段 | `parcc/Qwen`（视觉），温度 0，`max_tokens=4096`，默认 2 次×55 s，总超时 ≤120 s | 已实现：`v2_evaluator.py:38-42,120-253`；`eval_agent.py`（v3 解析：接受围栏/前后文字，键集合仍严格；失败留原文片段）。experiment 模式必须启用（`require_eval_gate`）。**10-19 核实：Eval 证据里的初/末公开观测含上述模拟器真实坐标，所以判“物体没动”可只靠坐标；与“Eval 看不到模拟器 oracle”不一致，见第 10 节第 9 条** |
| 策略（注意力策略） | 失败后决定：单独修复 / 求助 / 检查轨迹 / 取 Memory | 公开反馈、Eval 判定与反馈、已收到的答案、剩余帮助 | 原生评估器、仿真 oracle | 规则见第 8 节 | 已实现：`core/policies.py:234-264`；`dev_help.py:66-83` |
| AdvisorProxy | 模拟人类，回答 hint；**10-19 规则：它看到的 = 人在界面上看到的，以界面为唯一标准** | **10-19：由共用“请求视图”（`dev_request_view.py`）生成输入**：任务、请求问题（及 Dev 假设/理由，目前没有则为 generic）、五视角录像帧（≤10 帧，记视角与哈希）、公开步骤及停止原因（与界面同一套文案）、本版 diff、Eval 评审（判定、客观原因、参考建议）、展示的录像秒数 | 原生评估器、仿真 oracle | **`parcc/Qwen`（视觉，10-19 用户决定；与 Eval 同一基础模型）**，温度 0，默认 2 次×55 s | 已实现并在真实运行验证（`fadaf8f`）：5 版本中 4 次求助均带 6 帧回答；视觉重试失败按既有规则使 cell 无效。旧 daily 实验仍走旧文本路径（`dev_help.py` 的 `AdvisorProvider`），字节不变 |
| 原生评估器 | 离线给每个版本打分 | 仿真状态 | — | 任务自带 `_check_success` | 已实现；结果只写入回执并在报告中读取。在 `dev_v2_loop.py`、`dev_help.py`、`eval_agent.py`、`v2_evaluator.py`、`dev_advisor_proxy.py` 中检索 `native_success|native_result`，只有回执引用，没有进入模型输入的读取点（静态检索，不能证明任意运行产物绝无污染） |

- **10-19：**Eval 与 AdvisorProxy 允许同一模型，条件是提示词版本与哈希、缓存版本与命名空间、会话命名空间均不同，否则拒绝（`dev_configuration.independent_eval_advisor`，**唯一被放宽的校验**）。这是**已知限制**：两个角色共用基础模型，独立性靠不同提示词、状态和缓存，不靠不同模型；试点规则 R6 同步改为该条件。
- **10-19 角色分工（用户决定）：Dev 迭代；Eval 判断完成度并给客观失败原因（另给单独标注的参考建议）；AdvisorProxy 根据界面上人能看到的全部内容给建议。**Dev 只拿 `passed + reason`；`reference_suggestion` 只给界面与 AdvisorProxy。Eval 的评审卡在界面 03 区显示，AdvisorProxy 经界面同一视图看到。

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
- **10-19：**“Eval 判通过即结束格子”只在假数据里测过；11 次以上真实运行里 Eval 从未判过通过（无原生成功），该分支未在真实运行验证。
- 误判通过（false stop）：Eval 通过而最后版本原生失败，已在报告里统计（`dev_report.py:97-105,145-160`，已实现），作为次要结果必须披露。
- 未评估 / unknown 的版本不计为失败（`dev_report.py:65-117`）。整个格子有未解决状态时不进入已评估分母。
- 计数口径提醒：调度里的 `target_valid_executions` 按“已结算且非诊断的 execute”计，报告里的 E 按“native 已评估”计（`dev_v2_loop.py:590-596`；`dev_report.py:85-88,128-134`）。若出现已结算而 native 未评估，二者会分歧（待决，见第 10 节）。

## 5. 预算

| 档位 | SDK 调用 | 控制步 | 单次尝试 | 用途 |
|---|---|---|---|---|
| `legacy` / 正式档 | 80 | 2400 | 480 s | 旧实验字节复现；正式对比 |
| `dev`（Dev v2 默认） | 320 | 9600 | 1920 s | 开发与试点，不进正式对比 |

- **10-19：**免费模型不限 token 与评审调用，但仍记录；每格 Eval 安全上限 max(2×max_versions,10)；`model_seconds` 仍限。新 UI 对话框默认 5 个版本（SDK 调用 = 版本数 × `task_limits()`）、AI 思考时间 30 分钟、整次运行 6 小时（均为 dev 档，不进正式对比）。
- 实现：`diag_budget.py:8-12,26-42,52-69`；`dev_config.py:70-88`；`formal_config.py:17-25,73-97`。`budget_tier` 写入配置与清单并进入哈希；显式 `legacy` 与改动前逐字节一致；缺字段按 `legacy` 读。正式边界拒绝 `dev` 档（已实现）。
- 给 Dev 的提示词数字按档位生成（`dev_tasks.py:16-28,41-42`）。
- 回执留痕 `dev_tier_budget`：worker reset 回执、runner 的 sandbox/native/trace/result、账本回执（`worker.py:28-31,109-112,171-172`；`runner.py:140-153`；`dev_ledger.py:205-208`）。
- 目标级与格子级预算随档位放大（`dev_ledger.py:8-21`；`dev_experiment.py:13-19,137-139`；`dev_configuration.py:60-79`）。
- **正式档的标定：未实现。**方案（待决）：对每个任务族取参考解实际用量（seed 102 参考：69 次调用、1101 步、约 430 s 含启动与证书）乘固定倍数（建议 2 倍），倍数在跑任何对比之前冻结。当前正式上限仍是旧的 80/2400/480。
- 探针专用的环境变量 `ATTN_DIAG_BUDGET_SCALE`（1–8，需 `ATTN_DIAG_GRASP_PROBE=1`）只用于诊断；diagnostic 以外的模式拒绝它（`dev_experiment.py` 的 `diagnostic_budget()` 检查）。

## 6. 请求类型与注意力

- 主矩阵只有 **hint**。枚举有 `hint/approval/interrupt`，**无 `review`**（`core/models.py:33-36`）。常规 Dev v2 通道把非 hint 请求改为 `stop/approval_request_rejected_no_authorization`（`dev_help.py:66-93`；`dev_v2_loop.py:248-276`）；approval 与 interrupt 留给真机和专项场景测试（`dev_planner.py:106-109,173-193,301-342` 的独立 planner 通道支持）。
- **10-19 暂定注意力公式（用户：先用，之后再改；常数冻结前定死）：**`A = c0·N + Σ(T_view + T_read + T_write)`（逻辑人工秒）。`N` 请求数，`c0` 每次被打断的固定代价（提议 10 s），`T_view` 展示给人的录像/画面秒数，`T_read` 展示的文字词数 ÷ 4 词/秒，`T_write` 答案词数 ÷ 0.7 词/秒。主轴 `A`，次轴 `N`。不计入：智能体等待时间（资源）、Eval 成本（算力）、AdvisorProxy 的模型延迟；Live 模式用真人从请求到回答的实测时间。`T_view` 对通用请求按整段录像、对有针对性的请求按所选片段计，这正是有针对性的请求更省人力的来源；代理实际只收 ≤10 帧是**代理保真度局限**，不改变计费。`T_view` 的倍速系数与上限、读/写速度、`c0` 在冻结前定。
- 注意力 = AdvisorProxy 的请求单位 + 逻辑人工秒数。一次 hint 预留 `advisor_calls=1, help_count=1, help_credits=1` 与 `hint_human_seconds`；`logical_seconds` = 执行秒数向上取整 + reading_seconds（默认 30）；targeted 请求按所选片段的视频时间跨度计（`dev_advisor_proxy.py:46-52`；`dev_v2_loop.py:508-522`）。
- **10-19 已补：**新路径的回执记录 `shown_video_seconds`、`shown_words`、`answer_words`、`read_wps`、`write_wps`，`hint_human_seconds` = 总和；字数按“词”计（曾误按字符数，已修，一次求助由 ~1029 s 降到 ~207 s）。diff 全文计入“读的字数”（建模选择，界面里该项折叠，待你确认）。`c0` 与总值 `A` 的汇总尚未实现。旧路径（旧 daily 实验）仍只有合并值。
- `help_budget`：每格 0–2 的帮助次数上限，默认 1（`dev_experiment.py:65-66,137-139`）。
- Eval 的成本是计算资源（`eval_calls`、token、时间），单独报告，不计入注意力。
- 等待人或 AdvisorProxy 的时间作为独立资源记录（timeline 要求；账本有 `help_wait_seconds`，UI 是否显示未验证）。
- Live 模式（真人先答、超时由 AdvisorProxy 接管并标记）：**未在主循环中找到该分支**（未实现）。

## 7. 无效格与重跑

- 场景合约不一致：runner 标记 `condition_invalid`，native `evaluated=False`；Dev loop 将格子置为 `condition_invalid/scene_contract_mismatch`（`runner.py:75-84`；`dev_v2_loop.py:355-370`）。
- Eval 无帧或重试失败：experiment 模式置 `condition_invalid/eval_vision_unavailable`；interactive/diagnostic 才允许 unavailable 回执（`dev_v2_loop.py:414-423,577-586`；`eval_agent.py:53-80`）。
- AdvisorProxy 失去视觉：**10-19 起对新路径与 Eval 同一规则**（重试后仍失败则 cell 无效）；旧文本路径无此分支（`dev_advisor_proxy.py`；`dev_v2_loop.py`）。
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

- “Demo-first”按 timeline 的定义是**预先写好的参考策略源码，不是视频**。**10-19：用户暂不加入（未测）。**
- **10-19 可执行性（8769，seed 102，各一次真实运行，只验证流程不比较表现）：**

| 策略 | 能跑通 | 失败后决策（v1..v5 之后） | 求助次数 | 请求类型 |
|---|---|---|---|---|
| autonomous | 是 | retry×4 | 0 | — |
| reactive_help | 是 | 每次失败都求助 | 4 | 通用 |
| retry_k_then_ask（k=2） | 是 | 第 1 次 retry，第 2 次连续失败起求助 | 3 | 通用 |
| budget_matched_random_escalation | 是（终态 failed：v5 无效源码） | retry,request,retry,retry | 1 | 通用 |
| trace_aware_hint_only | 是 | retry,retry,request,retry | 1 | 通用 |
| full_trace_aware_attention_planner | 是 | retry×3,request | 1 | **有针对性**（10 帧覆盖五视角，围绕失败事件取前后帧，展示录像约 0.14 s，对比通用请求 44–219 s） |

  全部原生失败、Eval 全部判失败；完整规划器未检索到记忆（`matching_memory_count`=0）、未触发审批（无已验证记忆、无需审批）。随机策略的求助次数仍是默认值，需用完整规划器的实测次数在冻结前设定。
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
2. **Eval 是否看 `policy_source`，是否写明成功规则。（10-19：Eval 改为“客观原因 + 参考建议”，不得含修复建议，见第 2 节；成功规则文字与 `policy_source` 的取舍仍待定，复测需新的 Qwen 授权。）**一次真实 Qwen 冒烟（3 次调用，原生 False/False/True）出现 1 次误判通过，原因是 Eval 拿策略代码自己的目标点当真值。选项：(a) 去掉 `policy_source`；(b) 保留，但提示词写明“策略自己的目标点不能作为完成证据，以公开观测里的水槽位置和画面为准”；(c) 提示词写入任务的成功规则文字（物体在水槽内、夹爪远离物体）。不泄漏本次执行的真值，TidyBot 原做法是 Dev 在仿真里可直接读 `/task/success`，我们不能照搬（会使“何时求助”失去意义）。推荐 (b)+(c)，再用同样三次执行复测（需再次授权模型调用）。
3. **AdvisorProxy 的视觉模型。（10-19 已决定：`parcc/Qwen`，与 Eval 同模型，放宽独立性校验，见第 2 节；仍可加“Advisor 视觉开/关”消融。）**
4. **主矩阵的 Memory 初始状态（2026-10-18 修订，原推荐“空”作废）。**Memory 的目的就是提高成功率、降低对人的依赖，主矩阵里 Memory 恒为空等于没测到它。设计：矩阵内笔记不增长（避免格子依赖运行顺序），但格子**带着一本事先攒好、验证过并冻结的笔记本出发**。三段：(a) 学习阶段：在“训练场景 ID”上经干预产生候选笔记，走完验证，挑出可信条目并冻结（哈希进冻结记录）；(b) 评估阶段：在**不同于训练场景**的场景上，所有策略带同一本冻结笔记，并另跑**空笔记本对照**，比较成功率与注意力；(c) 顺序研究（笔记随运行增长，看人类注意力是否下降）单独做，不进主矩阵。**前提尚未满足**：目前没有任何经真实干预产生并验证过的可信笔记，真实运行中也从未产生过候选笔记，需要先让“干预→候选→验证→可信”链路在真实运行里产出第一条笔记。待你确认：训练场景与评估场景如何划分（例如按种子切分），以及冻结笔记的准入标准。
5. **正式档预算倍数。**推荐“参考解用量×2”，倍数冻结前定死，不看结果调整。
6. **held-out 1001–1010 是否做 reset-only 指纹探测。**不含性能信息，但会接触 held-out；若做须在冻结记录里写明。
7. **`target_valid_executions` 的计数口径**：调度用“已结算 execute”，报告用“native 已评估”。推荐统一为后者，或在两处都显示。
8. **cell 中的预算水平与重复字段**：代码没有，需新增才能做多预算点前沿和配对噪声分析。

9. **（10-19 新增）模拟器真值坐标的暴露。**公开观测的感知来源为 `official_public_sim_gt`：Dev 与 Eval 都能看到物体/水槽的真实坐标，但看不到原生成功判定和安全字段（Eval 证据构建有正则拦截）。Dev 无图像，必须有坐标才能动作，保留。Eval：选项 (a) 保持并在文档如实说明；(b) 增加“Eval 有/无坐标”的消融，把纯视觉判断单独测出来。推荐 (a)+(b)，R7 需要知道 Eval 对原生评测的一致性有多少来自视觉。
10. **（10-19 新增）没有原生成功时怎么办。**至今约 11 次以上真实运行无原生成功，失败主要是 `move_delta` 超时/被拒及一次关节超限。选项：(a) 给 Dev 更高层工具；(b) 增加私有进度读数（抬起高度、到水槽距离等，只记录，不给任何角色）；(c) 接受全部失败，只比较注意力。推荐先 (b)；试点显示工具是瓶颈再考虑 (a)。
11. **（10-19 新增）Dev 是否随每次 hint 写“假设与求助理由”。**完整规划器的有针对性请求需要它；会改变所有策略的 Dev 输出契约。
12. **（10-19 新增）注意力常数。**`c0`、读/写速度、`T_view` 的倍速与上限、diff 是否计入读的字数。

13. **（10-20 决定）Memory 不要求人工审核。**既不逐条审核笔记,也不逐次审核使用:门槛是自动验证(默认规则或 single_scene_v1);人保留的是紧急开关(禁用、设置过期、撤回)和可见性(界面显示一次运行用了哪些笔记),没有必须执行的人工动作。唯一的人工签字是冻结时对主矩阵所用"冻结笔记本"的一次性签字(属于配置冻结)。手工种入的测试笔记由操作者按用户指令创建,标注"测试笔记、不计入结果",不进入正式结果。真机动作审批是另一套规则,与 Memory 无关。若以后增加可选的人工复核提示,其耗时必须计入注意力。
14. **（10-20 决定）开发档 Dev 模型调用截止时间 360 秒**(正式档与 legacy 仍是 120 秒;Eval、Advisor 不变)。依据:GLM 吞吐约 28 token/s,单次上限 8192 token,最坏约 290 秒以上;真实运行中 120 秒与 240 秒都被尾部延迟打断过。每格 AI 思考预算 = Dev 版本数×360 + (hint 数+Eval 数)×120。

## 11. 场景门与冻结

- **10-19：合约已重新登记。**旧合约建于控制增益 300；默认增益 10-17 改为 1500 后，真实运行第一次与合约对不上（归一化哈希不同，物体位姿差 ~1e-5）。新合约 `scene_contracts/dev-seeds-101-103-v2-20261019-kp1500.json`（sha256 `bb28cd3e…c716`，`scene_contract.REGISTERED_SHA256`），在增益 1500 下对 101–103 × 两任务各 20 个新进程重采集，各组指纹唯一；旧文件保留，旧 manifest 仍可按自己的哈希加载。**以后改控制默认值或机器人模型，必须重验合约。**
- **10-19：daily 对比模式的场景门此前形同虚设**（legacy 配置路径不带 `scene_reset_seed`/`scene_contract`，同一 seed 物体随机落在水槽两侧）。已修（`b54d3af`）：新建的 experiment 模式 manifest 每个 config 带两者；interactive/diagnostic 与旧 manifest 字节不变。真实运行 5 次重置均 `verified`，物体位置一致。
- 场景合约（沿用）：登记 101、102、103（旧文件 `dev-seeds-101-103-v2-20261016.json`：2026-10-16 构建，每个条目 20 个新进程、容差 1e-6；`scene_contract.py:35-53`）。experiment 模式必须 `reproducible_reset=True` 并有覆盖每个 task×seed 的 v2 合约（`dev_configuration.py:42-59`）。
- 未实现：104–125 的合约（测试版范围外）、**固定动作 rollout 指纹（timeline 对场景门的定义要求，尚未做）**。
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

10. **（10-19）**“AdvisorProxy 纯文本、剔除图像”已不成立：新路径用 Qwen 视觉并发送画面；旧 daily 实验仍是文本路径。
11. **（10-19）**“Eval 与 AdvisorProxy 必须不同模型”已按用户决定改为“提示词/缓存/会话必须不同”（共用 Qwen）。
12. **（10-19）**timeline 写“Eval 看不到模拟器 oracle”，实际 Eval 与 Dev 都看到 `sim_gt` 物体坐标（第 10 节第 9 条）。
13. **（10-19）**timeline 写“Dev 拿到 Eval 的反馈”，现改为只拿客观原因；参考建议不给 Dev。
14. **（10-19）**timeline 写“界面显示 Eval 判定”，此前界面没有；10-19 起 03 区有 Eval 评审卡，04 区有请求视图面板。

## 13. 本文未核实的内容

**10-19 已在真实运行验证：**Start run 可用、场景门、Eval v3 与 Qwen 视觉 AdvisorProxy、请求视图面板、六个策略（除 Demo-first）能跑通；仍未验证：Eval 判通过的行为、Live 回退、Memory 真实链路与记忆检索、Dev 假设字段、`c0`/`A` 汇总、冻结签字流程、试点 R1–R8。UI 与 Memory 的功能完成度（只确认了入口：`dev_ui_server.py`、`dev_ui/app.js`、`memory_service_api.py`、`dev_planner_memory.py`）；真实运行中旧配置、v3 与 planner features 全部组合的行为；试点结果；Advisor 视觉可用性；正式签字状态。这些只能靠运行验证，不由静态检索证明。
