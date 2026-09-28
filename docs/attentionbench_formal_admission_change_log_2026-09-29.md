# AttentionBench 正式准入协议变更记录（2026-09-29）

本记录描述从[原冻结协议](../benchmarks/attention_harness/protocol/v2/formal_admission_freeze_2026-09-29.json)到[新 v2.1 协议](../benchmarks/attention_harness/protocol/v2/formal_admission_v2_1_2026-09-29.json)的明确修订。原冻结协议 SHA-256 为 `59bf8b0c7e22112ade57702888ea1e71ec38482f996e9c6244a287f5304e5939`；[旧感知协议](../benchmarks/attention_harness/protocol/v2/perception.json) SHA-256 为 `3fb9b51158569fed014d11bea7453abd5be495f1cd1310bc546d3b50810a26ce`。两份旧文件保持原样，旧审计和失败证据不追认。

| 项目 | 原条款 | v2.1 本轮主实验条款 |
| --- | --- | --- |
| 任务范围 | `perception.json#/robocasa_dev_gate` 要求 RoboCasa `counter_to_cab` 与 `counter_to_sink` 都过 25/25；原冻结协议标记其与双主线范围冲突。 | 主实验和七策略矩阵只含 Robosuite `cube_lift`、RoboCasa `counter_to_sink`。v2.1 对本轮主实验明确取代旧 RoboCasa 双任务关口；`counter_to_cab` 不计准入、不入矩阵，其任务实现、历史协议和证据均保留。 |
| 系统链路 | 两主线任务各 seed 101–105 完整链检查，并要求之后 25/25 原生成功。 | 两主线任务各 101–105 均须有同版、同批准策略／配置的完整 M1–M6、原生布尔判定、独立 Safety、Trace、四类哈希产物及 Service 回收。原生任务失败但可评价且证据完整，可算链路合格；五个 seed 均须完成。独立故障注入检查安全中止。 |
| 任务可解性 | 参考／特权探针不得充作策略稳定性成绩。 | 单独标注参考技能或基础设施可解性证据；特权探针仍不能计入基础策略结果、五 seed 链或七策略效果。 |
| 基础策略表现 | 同一锁定策略／配置在 101–125 上原生成功 25/25。 | 同一锁定公开基础策略／配置在预定 101–125 上形成 25 seed 描述性档案；逐 seed 保留成功、失败和无效原因，报告以 25 个预定 seed 为分母的计数，不设最低成功数。符合身份和证据条件的 101–105 可计入，绝不因结果补跑或换 seed。无效性若可能使七策略比较产生偏差，仍阻塞准入。 |
| 正式资格 | 旧冻结协议未准入，候选 SHA 空缺，depth 500 未解。 | `formal_eligible=false`。新协议本身不批准新运行或七策略矩阵；候选策略／配置等仍需独立审阅和锁定。depth 500 的根因及选择性缺失风险仍须单独解决并审计。 |

未列出的 seed 划分、held-out 禁令、版本锁、七条件、预算、独立 Safety、Memory 授权边界、异常留证和无效配对报告沿用新协议中的原有规则。没有为本次修订启动模拟器、held-out 或正式七策略矩阵。
