# depth qualifying 运行：已执行并独立验收（v2.4）

本轮按“冻结新版本 → 5例 → depth独立审计 → 其余20例 → RoboCasa影响核对和总体审计”完成。Service固定为 `19fde8aa7c47c283fce7edcb25348e4ccb2fd905`；本页为执行索引，原草案已保存在证据根 `qualifying_run_plan_before_run.md`。

| 步骤 | 独立裁决 | 证据 |
| --- | --- | --- |
| 跑前冻结 | PASS；5计划和25计划分别冻结，247文件SHA核对 | `independent_plan_pin.json` |
| cube_lift × 101–105 | PASS；5/5有效、20尝试、Safety0、原生成功0/5 | `independent_depth_audit.json` |
| depth独立门 | PASS；attrition_gate与operational_gate均true，仅Robosuite/depth | `independent_qualifying_plan_attrition_gate.json` |
| 新版本25槽 | PASS；五例各复用一次，106–125各新跑一次，100尝试 | `independent_robosuite_profile_audit.json` |
| RoboCasa影响核对及合并审计 | PASS；保留25例、70尝试；合并50唯一槽/170尝试 | `independent_robocasa_impact_audit.json`、`independent_overall_admission_audit.json` |

证据根：[逐项报告](/home/truares/桌面/attentionbench-depth-qualifying-20260929/CONCLUSIONS.txt)。所有计划、账本、独立审计和原始产物的逐文件SHA见同目录 `evidence_manifest.json` 和 `SHA256SUMS`。五计划哈希跑前由独立reviewer提供，最终账本SHA跑后核对；未预知或伪造跑后账本。

[协议v2.4](formal_admission_v2_4_resolution_2026-09-30.json)已按独立审计将历史坏帧分类为“当前冻结版本已控制的保留风险”，历史根因仍unknown。风险依据同时包括工程四项控制和当前新五seed qualifying；工程14网格仍只有工程范围。

总体审计完成，`formal_eligible=false`、`effect_comparison_authorized=false`。未执行held-out或七策略效果矩阵。原c67 Robosuite证据未迁移，RoboCasa25按独立影响审查保留；槽30原始中断仍为interrupted_invalid，授权补测不覆盖原件。Service和控制器已回收，在本轮90分钟截止前完成，无自动新轮。

未来正式效果准入须另行审阅总体审计所列未重裁的任务可解性、完整Safety负控、可信Memory版本/作用域与七条件批准等门。此已完成计划不得自动重启、重跑或替换案例。
