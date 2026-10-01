# 350 格成本估算（只读已有日志，2026-10-01）

串行基线约 **10.61 小时**。五个求助型策略若各 cell 平均求助一次，共 250 次 Advisor 调用，对应约 **50万–55万 tokens**；未测出新七条件的实际求助概率，零求助到满额求助的宽界为 **0–55万 tokens**。这是规划估算，不是本轮实验结果或正式效果比较。本轮新增 episode=0、Service 启动=0、provider 调用=0。

## 已观察的数据与可复核口径

耗时来自 [v110 profile_ledger.json](/home/truares/桌面/attentionbench-fixed-base-profile-20260929/engineering_v110_profile/profile_ledger.json) 中 `role=new_once` 的已完成槽 11–29：Robosuite seed 106–115，共 10 格；RoboCasa seed 106–114，共 9 格。排除复用槽 1–10 与中断槽 30，不把未完成槽当完整耗时。已有 [CHECKPOINT.md](/home/truares/桌面/attentionbench-fixed-base-profile-20260929/checkpoint_20260930_v110/CHECKPOINT.md:63) 报告约 10.3 / 207.9 秒；本次逐格读取 `process_result.json` 的 `ended_utc - started_utc`，重新算得 10.275537 / 207.946056 秒，支持该四舍五入口径。

例如 [Robosuite seed106 process_result.json](/home/truares/桌面/attentionbench-fixed-base-profile-20260929/engineering_v110_profile/new_runs/robosuite-cube_lift-seed106/process_result.json)、[RoboCasa seed106 process_result.json](/home/truares/桌面/attentionbench-fixed-base-profile-20260929/engineering_v110_profile/new_runs/robocasa-counter_to_sink-seed106/process_result.json)。全部 19 格路径、原始 SHA、逐格墙钟和 attempt 汇总保存在 [cost_estimate_350_sources.json](cost_estimate_350_sources.json)。已核对 ledger → case audit → process/attention_run → 四类 attempt 原始产物的 SHA；未重跑产生这些日志的代码。

| 已观察的完整 cell | Robosuite cube_lift | RoboCasa counter_to_sink |
| --- | ---: | ---: |
| 样本数 | 10 | 9 |
| 平均墙钟（秒/格） | 10.275537 ≈ **10.3** | 207.946056 ≈ **207.9** |
| 已见墙钟最小–最大（秒/格） | 10.131786–10.539977 | 189.583556–269.090940 |
| 平均 attempts/格 | 4.0 | 2.555556 |
| 平均 Service 占用时间（秒/格） | 9.897496 | 207.623575 |
| 平均 SDK 动作调用耗时（秒/格） | 1.064472 | 73.344333 |

**一格是一个策略 × 任务 × seed 的完整 run，已包含多次 attempt**；不能再把 10.3 / 207.9 乘 4。以上是旧 v110 autonomous 的实测，19 格原生结果均失败；它们的策略、版本、Memory/guidance 状态不等于新 guidance v1.1 七条件。这里只借用其耗时作预算基线，不推断新条件的成功率。

Service 占用来自每格 `sum(sandbox_receipt.elapsed_seconds)`，包括启动、reset、动作、观察与回收。SDK 动作来自 `sum(trace.sdk_events.duration_ms)/1000`，只计 `robot_sdk.arm/gripper/base`；它包含动作调用中的服务和观察等待，不能等同机械臂实际运动秒数。两个计时区间重叠，**不能相加**。没有物理机器人工作时间、关节运动时长或磨损计量。

## 网格、机器人时间与墙钟

按 [冻结 matrix_plan.json](../benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_guidance_v1_1_dev_2026-10-01/matrix_plan.json) 的 7 条件 × 2 任务 × 25 开发 seed（101–125）× 1 repeat = **350 格**：Robosuite 175、RoboCasa 175。

| 估算项 | Robosuite 175 格 | RoboCasa 175 格 | 合计 |
| --- | ---: | ---: | ---: |
| 基线墙钟（用 10.3 / 207.9 秒） | 1,802.5 秒 ≈ 30.0 分钟 | 36,382.5 秒 ≈ 10.11 小时 | **38,185 秒 ≈ 10.61 小时** |
| Service 占用/模拟机器人资源时间（用未舍入实测均值） | 1,732.1 秒 ≈ 28.9 分钟 | 36,334.1 秒 ≈ 10.09 小时 | **38,066.2 秒 ≈ 10.57 资源小时** |
| SDK 动作调用时间代理（用未舍入实测均值） | 186.3 秒 ≈ 3.1 分钟 | 12,835.3 秒 ≈ 3.57 小时 | **13,021.5 秒 ≈ 3.62 调用小时** |

Service 与 SDK 时间分别报告其自己的口径；这不是额外加在墙钟上的 14.19 小时。并行会压缩墙钟，保持相同 workload 时资源小时总量并不因此减少。

| 调度口径 | 基线墙钟 | 假设 |
| --- | ---: | --- |
| 单 worker 串行全部 350 格 | **10.61 小时** | 不含新 Advisor 等待、人工审计或额外重试 |
| 两个隔离 worker，各固定负责一个 suite | **max(0.50, 10.11) ≈ 10.11 小时** | 独立模拟器/Agent/端口/Memory/产物；RoboCasa 是长尾 |
| 2 个隔离 worker，可跨 suite 均衡分派 | 理想工作量下界 **10.61 / 2 ≈ 5.30 小时** | 两个 worker 都能执行 RoboCasa；考虑整格不可分、尾部、启动和 GPU 争用后实际更长 |
| 4 个隔离 worker，可跨 suite 均衡分派 | 理想工作量下界 **10.61 / 4 ≈ 2.65 小时** | 足够独立算力和内存；未做并发验证 |

同一 GPU 同时跑多套不是已证明的提速能力。上表的并行值是调度假设，不能承诺同一台当前机器能达到。将每个 suite 的实测最小/最大值全量外推，串行约 **9.71–13.59 小时**；这是观察范围的敏感性口径，不是置信区间或绝对上下限。

## Advisor token 与延迟假设

真实调用依据是 [real_glm_advisor_closure_2026-09-27.json](../benchmarks/attention_harness/protocol/v2/evidence/real_glm_advisor_closure_2026-09-27.json) 的两次未缓存、各 1 provider attempt 的调用：Robosuite **2190 tokens、2.426 秒**；RoboCasa **2061 tokens、2.332 秒**。token 包含 prompt+completion。按用户指定观察近似，每次预算用 **2000–2200 tokens**。后续 [M4 真实运行记录](m4_attention_decision_acceptance.md:47) 还有 **2172 / 2210 tokens**，其中 2210 略超近似上沿，因此 2200 不是保证上限。

求助型五策略是 reactive_help、retry_k_then_ask、budget_matched_random_escalation、trace_aware_hint_only、full_trace_aware_attention_planner；各有 2 × 25 = 50 格。autonomous 与 demo_first 不在线求助。按 [execution_contract.json](../benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_guidance_v1_1_dev_2026-10-01/execution_contract.json) 的一 credit/run、每请求单次 provider attempt、4096 token/run，五策略最多 250 个获答求助。

定义 `q_i` 为策略 i 的每格平均未缓存 Advisor 调用数，假设 `0 ≤ q_i ≤ 1`，不额外重试 provider。预期调用数 `N = 50 × Σq_i`；token 规划区间 `[2000N, 2200N]`。每个求助型策略单独的满额规划值为 50 次、100,000–110,000 tokens。以下对五策略使用同一个 q 只为方便预算，不声称它们触发率相同：

| 假设每格平均调用 q | 预期总调用 N=250q | token 下限 | token 上限 |
| --- | ---: | ---: | ---: |
| 0（未触发/本地或缓存） | 0 | 0 | 0 |
| 0.25 | 62.5（期望数） | 125,000 | 137,500 |
| 0.50 | 125 | 250,000 | 275,000 |
| 1.00（满额规划） | 250 | **500,000** | **550,000** |

冻结 Memory contract 要求 Advisor cache 每 run 初始为空，预算不假设跨 run 免费命中。提前成功、Safety 中断、retry 阈值、随机槽未到、Memory 修复可能减少求助；失败率高也不保证每个 trace-aware 策略一定求助。没有新七条件的实际 q。最保守的**配置 token 预算上限**另列为 `250 × 4096 = 1,024,000`；它比 55 万更宽，是配置规划包络，不能保证 provider 超额响应或异常失败不会产生额外计费。本轮不会调用 provider 去测这些概率或费用。

新 Advisor 延迟尚未知；串行墙钟可按 `10.61h + N × (L + 2s)/3600 + 审计/资源争用/额外工作` 规划。2 秒来自冻结 contract 的 proxy 逻辑延迟，L 为真实 provider 等待，二者不重复当成机器人动作时间。若 N=250，且所有等待都落在整 run deadline 内：

| 假设 L | 额外串行等待 | 基线加等待 |
| --- | ---: | ---: |
| 约 2.4 秒（上述两次正常实测附近） | 约 18.3 分钟 | 约 10.91 小时 |
| 10 秒（规划假设） | 50 分钟 | 约 11.44 小时 |
| 90 秒（客户端上限情景） | 6.39 小时 | 约 17.00 小时 |

这几个相加值保持 v110 的 attempt workload，实际 300 秒/run 的总期限可能截断后续 attempt，故超时情景并非预测的完成用时。冻结上限的另一种容量规划是 `350 × 300s = 29.17h` 的串行 run 时间包络，另需启动/退出与审计；不是保证任务成功或每格必跑满 300 秒。无已核实的单价或账单，**不换算货币**。

## 不确定项及使用边界

| 不确定项 | 本表假设与影响 |
| --- | --- |
| 新策略的失败率、重试与 guidance 效果 | 沿用旧 autonomous 的完整 cell 耗时；新成功早停会缩短，失败/动作增多会延长。没有用 19 格全失败来推断新七条件。 |
| 新版本、seed 与异常路径 | 只观测了 v110 的 19 格，且排除了中断槽。新冻结策略、seed 101–125、历史 depth 500、Service 启动失败均可能偏离均值；不含异常重跑成本。 |
| 求助触发率与 provider 失败 | q 从 0 到 1 做敏感性表；2000–2200 是近似。既有 2210、3455 等更大调用说明不是硬上限；超时/格式拒绝也可能耗 token。 |
| GLM 时延及系统期限 | 只用已有 2.332/2.426 秒举例，另列 10/90 秒假设；300 秒整 run 期限会改变 attempt 数。 |
| 并行能力、GPU、Agent 与 Memory 隔离 | 并行仅作理想工作量下界；未启动并发验证，也不默认当前机器线性提速。 |
| “机器人时间”的含义 | 10.57 小时是模拟器/Service 资源占用代理，3.62 小时是 SDK 动作调用代理；两者重叠，均非物理机器人运动时长。 |
| 审计、视频、人工、Memory 配对、Dev/Eval | 基线包含当时单 cell 自动产物成本；额外人工审计、排障、重新生成、晋升配对和另行模型评估不计入。350 格不含 held-out 或其他效果矩阵。 |

本表的计算证据与三条验证脚本是准备产物。冻结包和既有实验日志保持原样；这里的估算不提供运行授权，也不将工程日志宣称为正式实验验收。
