# 三类剩余准入包核对结论

本页按独立审计报告索引证据；总体准入以独立 overall_admission_audit.json 为准。范围仅为本开发集主实验，350格只生成/校验，矩阵、held-out、消融均未执行。执行代码固定Universe `03f07ded72b936c57e08ce22050f06b34ee46a0e`，各Service commit见冻结包software_versions.json。

| 准入包 | 结论 | 复用与缺失验证 |
| --- | --- | --- |
| 任务可解性 | PASS | 对原生任务/evaluator成功的标记reference/基础设施证据核SHA，并按当前R/C/T版本做函数/AST影响桥接。不计为基础策略成功，不重跑任务。 |
| 完整Safety负控 | FAIL | 复用旧七类负控与当前R14工程负控，核当前版本影响；唯一新增当前C未知动作case在station身份校验被拒绝，未进入注入。不能用monitor_not_initialized的unsafe=1替代action_outcome_unknown注入检测；检测/故障即停时延不可测。双Service回收与四产物留存通过，但coverage失败。 |
| 可信Memory版本/作用域 | PASS | 保留两任务现有可信v1来源、晋升/生命周期与使用证据。独立离线Service在副本上核50次检索、50次grant、4次初态隔离、10次生命周期拒绝；六个隐藏条件300次不探测验证。仅每任务101/103/105匹配，其余22个seed拒绝；无新增覆盖率门槛、无自动晋升或作用域扩展。 |

关键证据（完整SHA-256）：

- [当前版本复用审计](independent/evidence_reuse_audit.json)：`6ef4ca36b03ccd18f6e62cf02d508f67308906f522183fab715928702946b37d`；787个证据SHA核对无漂移。
- [当前R负控复用审计](independent/current_robosuite_negative_reuse_audit.json)：`cfe6d17877aa32b8eb0e66d95f8ece3f7d51e52c25e702e20f012d800f36b28d`；113个证据SHA核对无漂移。
- [唯一新增C负控后审](independent/negative_control_post_run_audit.json)：`a1aefe83a26d76d794714c5f724ec5bc1686c128193f1f82d69b8dab91d8c972`。
- [新增C负控原始result](negative_control_run_v2/result.json)：`91cb527206a772f68d4a8b8f23ace18a1bd15863424b1efe5eeba06f494d1985`。
- [Memory/七条件最终批准](independent/seven_condition_freeze_approval.json)：`9605061df5350de9c18537c8c0bc9bc04e01d03bd8d54a6d7db158b153413ff6`；批准冻结输入，执行授权仍false。
- [最初Memory权威库核对](independent/memory_inventory.json)：`d7111849ee646b89b2007538666cf52d8d3289ef2d7df85586921a1d32aca985`。
- [最终旧50入口与SDK总量影响核对](independent/final_legacy_impact_audit.json)：`7cb7d8e66630357fc78383272cfec44829c1a11c4b7cd3318fb0b626d9c04374`；50/50历史M1同身份，170尝试累计每run最大69 SDK，全部≤200。
- [本轮执行台账](execution_ledger.json)：`46f59b65492e1548c9b2198b97e795bf1056f7147621376bbb1728011a7c9705`。

实际新增live开发case上限1，已消耗1（1attempt）；两个Service启动前launcher错误全部保留。没有择优、替换或补跑。Safety后审确认双Service group已gone，正常清理0.478秒；这仅证明回收，不能证明注入后及时停跑。本轮不自动启动新一轮。

[固定控制代码的输入消费范围](../../review_packages/seven_primary_dev_2026-09-29/semantic_limitations.md)也构成结论边界：两份基础策略不读取attention_input。Memory grant/use仅证明Harness授权与曝光，不能推出控制代码消费、动作改善或任务收益。

下一步仅是未来另行授权并预注册一例当前C未知动作负控：修正run/attempt与station目录身份，再要求真实动作回执、预注册注入、安全检测、及时停跑及双Service回收均成立。原失败保留；chain/profile/depth不重跑，仍不运行350格或held-out。总体准入不得在该缺口闭合前签true。
