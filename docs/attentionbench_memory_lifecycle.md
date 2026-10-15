# AttentionBench Memory 生命周期 v2（入库、验证裁决、检索、禁用/回滚/过期、UI 读模型）

实现：Memory 服务仓库 `attention_memory_service`（`memory_v2.py`、`memory_service.py`、`memory_service_api.py`、`operator_cli.py`）；harness 侧 `memory_ingestion.py`、`memory_view.py`、`ui_server.py`、`memory_agent*.py`。测试：服务仓库 `tests/test_validation_decision.py`，harness `tests/test_memory_ingestion.py`、`tests/test_ui_memory.py`。

## 1. 候选怎么产生（事件驱动，一次且只一次）

```
请求已回复 → response.used(guidance)（被采用）→ response.execution_linked（用在某次执行上）
          → 该执行有评测器记录的结果 → 生成候选 → 记录 memory.candidate_ingested
```
- **Advisor 回复和人类回复都入库**。人类回复的 attention 成本 = 对方从请求打开到回复所花的时间（上界，最小 1 ms），写进 provenance。
- 候选带上“指导后那次执行的结果”：`guided_outcome ∈ native_success | failed | timeout | cancelled | evaluator_unavailable`，`effective = (native_success)`。**每个被采用的 hint 都成为候选，是否有效由后面的配对验证来筛选**，不是只收成功的。
- 被忽略的回复（`ignored`）不入库。服务拒绝某个来源时（例如源 attempt 没有评测器确认失败）记录 `memory.candidate_skipped` 和原因，不让 run 失败，也不静默丢弃。
- 决定持久化在 `memory-ingest:{request_id}`，重启/重复 sweep 不会重复创建。命令行补跑：`memory_agent_cli sweep --store-path … --run-id …`。
- 入库不晋升。晋升仍是 Memory Service 的事。

## 2. 验证与裁决

候选必须按**预先冻结的变体计划**做配对验证（≥5 个互不相同的 dev seed，场景/物体/相机/任务四个维度都要变化）。计划完成后调用 `evaluate`（服务 `POST /memories/{id}/evaluate`，agent `finalize`，CLI `evaluate` 或 `validate --decide`）：

| 结果 | 条件 |
| --- | --- |
| `trusted` | 全部门槛通过：配对数≥5、同一份策略代码、无安全回归、成功不下降且有可测收益、treatment 成功≥3 且≥75%、反例排除后仍有可验证范围 |
| `rejected` | 任一门槛不通过。状态原因 `validation_gate_failed: <gate,…>`，门槛代码见下；整份影响报告（成功数、置信区间、支持成功、反例、安全回归）与决定一并保存，可 `GET /memories/{id}/decision` |

门槛代码：`insufficient_pairs`、`mixed_policy_versions`、`safety_regression`、`success_regression`、`no_measured_gain`、`insufficient_treatment_successes`、`treatment_fraction_below_0.75`、`no_validated_scope`。计划没做完不是结论（报错且状态不变）。原来的 `promote` 仍在，不通过时抛错而不改状态。验证的**触发**仍是人工（冻结纪律：主矩阵期间不自动晋升）；本步没有在 UI 里加“启动验证”。

## 3. 检索与使用

只有 `trusted` 且适用条件（suite/task/perception、场景/物体/相机/任务变体、相机名与任务 prompt）与已验证范围匹配的 Memory 才会被检索；每次使用由服务签发 attempt 绑定的 grant，使用记录带 `memory_version` 与评测器结果并与原始 trace 交叉核对。`GET /memories/{id}/uses` 列出全部使用。

## 4. 禁用 / 重新启用 / 回滚 / 过期（需要 operator 权限）

- 服务路由：`disable`、`enable`、`rollback`（永久）、`PUT expiry`，必须带 `actor` 与 `reason`，operator key 与读 key 分开；每次都写审计事件，`GET /memories/{id}/history` 给出完整状态历史（含操作人和原因）。
- 修复了一个潜在问题：同一条 Memory 禁用→启用→再禁用会命中第一次禁用的事件键而回放旧结果。现在每个循环有独立键，重复提交同一个操作是幂等的。
- 回滚、过期后的 Memory 永远不会被再次启用。

## 5. UI 后端

`ui_server` 新增（`--memory-service-url` 用服务；`--memory-local` 读 run 自己的 store，`--memory-local-operator` 才允许在本地操作）：
- `GET /api/memories[?status=]`：目录，含验证进度（计划 seed 数/已完成配对/裁决/失败门槛）与使用次数。
- `GET /api/memories/{id}`：记录、来源（trace/request/回复者/证据 ID+SHA，不暴露文件路径）、验证与影响报告、状态历史、使用记录。
- `POST /api/memories/{id}/{disable|enable|rollback|expiry}`：control token + `actor`/`reason`（expiry 另需 `expires_at`）；没有 operator 权限 403，非法转换 409。operator key 只在服务端环境变量里，不进浏览器。
- run 快照新增 `memory`：本 run 的入库决定、各 attempt 检索的 `memory_id@version` 与 grant、以及这些记录的服务端详情。agent、UI、产物引用的是同一个 `memory_id + version`。

## 6. 边界

- 正式主矩阵期间的 Memory 初始状态、可见范围仍由冻结包规定；本步没有改变。
- 前端已有候选/验证队列、请求关联 Memory 内容/版本、详情和有 actor/reason 的生命周期表单；生产渲染合同通过。真实浏览器在当前沙箱因 setsockopt EPERM/SIGTRAP 无法启动，截图及真实持久化端到端尚未验收，不能把代码存在当作真实服务跑通。证据 `outputs/handoff-execution-20261016/D/`。
- 没有重新跑配对验证（每个仿真器 10 次真实运行），裁决逻辑由单元/集成测试覆盖，真实服务上的拒绝路径未跑。
