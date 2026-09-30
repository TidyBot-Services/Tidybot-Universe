# 七策略实验冻结规格书（参数已定稿）

> 本文档是"设计并冻结七策略实验"的定稿参数。由协议 v2.2 固定的项不再重复决策；下列 8 项为本轮拍板，全部采用推荐。

## 已固定（来自 formal_admission_v2_2，不变）

- 七条件：autonomous / demo_first / reactive_help / retry_k_then_ask / budget_matched_random_escalation / trace_aware_hint_only / full_trace_aware_attention_planner
- 主任务：Robosuite cube_lift + RoboCasa counter_to_sink
- 感知：sim_gt；评估：原生 evaluator；Developer/Advisor：parcc/GLM；assistance_mode：benchmark_proxy
- 单 run 预算：4 attempt / 120s / 300s wall / 1 credit / 4096 tokens / 200 sdk calls
- 开发 seed 101–125；held-out 1001–1100

## 关键设计约束

- **七条件共用基础机器人策略**：每个任务只用同一份获批基础代码，七条件仅改变"求助规则"（何时/如何求助），不改变机器人控制代码——保证比较差异只来自求助策略。
- **Memory 记账统一**：每个 run 从规定的 Memory 初始状态开始；主矩阵运行期间不自动晋升新 Memory；缓存命中与求助费用按统一规则记账。

## 本次拍板的 8 项（全部采用推荐）

| # | 决策 | 定稿 |
|---|---|---|
| 1 | 主矩阵 seed 数 | **25**（101–125） |
| 2 | 每格重复 | **1 次**（靠 25 seed 提供方差） |
| 3 | 成功率聚合 | **按任务分别报告** |
| 4 | frontier 横轴 | **credit（求助次数）为主，token 为辅** |
| 5 | 置信区间 | **Wilson 95%** |
| 6 | 消融项 | **Memory on/off + trace-evidence on/off** |
| 7 | 消融规模 | **子集 5–10 seed** |
| 8 | held-out 规模 | **10 seed** |

## 实验矩阵规模（规划值）

- 主矩阵：7 条件 × 2 任务 × 25 seed = **350 格**（其中 RoboCasa 约 175 格，慢）
- 消融：2 项 × 2 任务 × 5–10 seed
- held-out：10 seed（1001–1100 中取 10；具体条件集在 held-out 阶段再定，主矩阵后执行）

## 待冻结（交给 codex 执行）

- 七条件冻结包 = 基础策略代码字节 SHA + 每任务每 seed 配置 + M1 entry lock + **条件专属输入**（demo 资产、retry 的 k、随机求助预注册、各条件 Memory 可见范围与初始版本）+ review 包
- `formal_entry.py` 已要求 demo、k、随机配置三项，冻结包须覆盖并补齐 Memory 作用域/版本
- 冻结范围**仅限本开发集主实验**；held-out 与消融**另行冻结**
- 已通过的 chain / profile / depth 不重复运行；三类剩余包先核对当前版本，仅对缺失证据做有上限的开发测试；350 格只生成、校验冻结文件，本轮不执行

## 明确禁止（冻结纪律）

- 不跑 held-out、不跑效果矩阵（准入通过前）；不按结果改策略/seed/预算；异常案例全保留不择优；不直接改布尔值；不为翻布尔值放宽条件。
