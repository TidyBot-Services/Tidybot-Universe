# AttentionBench v2 `sim_gt` 正式实验准入审计（仅开发集）

审计日期 2026-09-29；同日按[变更记录](attentionbench_formal_admission_change_log_2026-09-29.md)更新任务范围和成功门槛。当前结论：**未通过，`formal_eligible=false`**。没有启动新的模拟器运行，没有使用 held-out，也没有运行正式七策略效果矩阵。工程验收与正式效果门槛分开判断；原冻结协议与本审计更新前的结论可由原提交和旧 SHA 复核。

## 顺序、版本和逐项裁决

1. **通过｜工作树与风险记录。** `feature/attention-native-robosuite` 起点 `a55416b`，三份未提交风险文档经 `git diff --check`、原件及 SHA 复核后提交为 `65e9347456d16a811dcc0cd47ec0827b9d123575`，提交后工作树干净。独立 R／C／M／A／T 当前分别为 `081cd57ec9383895dc150050ca5d05c24628e7fa`／`320020a0c94434af31ec02df3413229576490fef`／`24d414638ad2cdd6557f09f042e8b6ef2b597b0f`／`4cf4daaba61d4cbbb0ca6daaa4ff28165c9daf1b`／`b18bbf1585c42e370ae45ababdddad700cc2c71d`，检查时均无未提交改动。风险索引 `sha_index_v2.json` 675/675 件 SHA 正确。
2. **通过｜M1–M6 同版跨模块工程链。** v7 逐项审计 58/58，通过索引 340/340 件 SHA；双套真实 Graph／Bridge／Runner／独立 Service／Safety／Eval／UI 的结论仅限选定开发 seed 的工程链。Robosuite 候选 A 首次成功而未触发 Advisor/Memory 的失败验收仍保留；其候选 B 与 RoboCasa A 是不同 run，不合并为稳定性成绩。原生失败也保留。
3. **通过｜协议范围冲突已按新版本解决。** [原冻结协议](../benchmarks/attention_harness/protocol/v2/formal_admission_freeze_2026-09-29.json)提交 `0ba944d`，SHA-256 `59bf8b0c7e22112ade57702888ea1e71ec38482f996e9c6244a287f5304e5939`；当时指出[旧感知协议](../benchmarks/attention_harness/protocol/v2/perception.json)的 RoboCasa 双任务 25/25 关口与主线范围冲突。[v2.1 新协议](../benchmarks/attention_harness/protocol/v2/formal_admission_v2_1_2026-09-29.json)现明确取代该关口在**本轮主实验**中的适用性，只纳入 `cube_lift`／`counter_to_sink`；`counter_to_cab` 不作准入门槛且不入七策略矩阵，任务实现及历史证据保留。范围冲突不再是当前阻塞项。候选策略／配置 SHA 仍为空，尚未批准锁定，故新准入运行仍不得启动。
4. **未通过｜双主线任务五开发 seed 链。** 现有 v7 只选 Robosuite `cube_lift` seed 103 与 RoboCasa `counter_to_sink` seed 101。RoboCasa v3 五 seed 是同策略 Memory 对照／处理配对，证明限定效果与 Service 晋升，不能替代每 seed 的完整 M1–M6 正式链检查。Robosuite 旧五对同理。缺双任务各 101–105 的同版、同批准策略／配置、正式入口锁、真实 Graph/Runner、原生判定、独立 Safety、Trace/Advisor/Memory 边界、四产物 SHA 和 Service 回收逐 seed 证据。
5. **未通过｜两主线任务 25 seed 基础策略档案。** v2.1 不再要求 25/25 原生成功，但仍缺每任务在同一锁定公开策略／配置、精确软件版本下逐 seed 101–125 的真实正式 Runner 结果及失败／无效原因报告。v1 non-oracle 成绩、25 seed reset/variation discovery、reference/teleport、单 seed smoke 与 Memory 配对处理臂均不替代该档案。预定 25 seed 全数列入分母；符合身份和证据条件的五 seed 链 case 可计入，不能按结果换 seed 或补跑。策略／配置 SHA 尚未批准锁定，本轮新执行数为零。原生成功率是待测结果，不是准入最低门槛；可能使比较偏差的无效样本仍可阻塞正式矩阵。
6. **通过（限定范围）｜RoboCasa trusted Memory。** v3 的五个预冻开发变体 control 0/5、treatment 5/5、独立 Safety 0/10，十臂及双 Service 收据逐件复核；Service 晋升 `candidate:m5:robocasa-counter-to-sink-public-sdk-v3` 为 trusted v1。配对外 seed 101 独立正式边界工程 run：先无 Memory 原生失败，后匹配 scene／object／camera／variant／task／`sim_gt` 的 attempt 获精确 v1 grant、真实检索/使用事件和原生成功；范围外及 disable／rollback／expiry 拒绝通过。该结论只覆盖预冻的 `counter_to_sink` 变体与当时版本，不替代 25 seed 基础策略档案或其他任务／相机／模式的证据。旧两批失败候选与 Safety 反例未改写。
7. **未通过｜Robosuite depth 500 准入。** 历史 HTTP 500 的异常帧缺失，归一化 depth 越界的具体值／像素／上游原因仍未知。新冻结 10 个开发 case、60 动作零复现与 `>1`／NaN 注入的保存、拒绝、安全中止，只证明固定窗口和故障隔离，**不是修复**。现有立即停 attempt、未知动作记 unsafe、受影响配对无效且不替换、保留原件是必要措施；故障可能依赖策略动作或渲染时序，排除受影响配对会造成条件性缺失，无法凭现有证据界定正式策略比较偏差。因此不准入；任何再现须留 `.npy` 与 HTTP/Service 原件、定位根因并针对性修复，随后按冻结条件重审。不得将零复现改写为已修复。
8. **阻塞｜正式资格标记和进度。** 第 3 项范围冲突已解决；第 4、5、7 项仍未通过，且基础策略／配置尚未批准锁定，因此不实现 `formal_eligible=true` 或发布资格锁。所有现存运行保持 `false`；进度页与 `STATUS.md` 记录本次未通过和精确缺口。

## 可复核命令与原件索引

以下命令为本轮只读核对或 Git／协议记录；风险目录内的真实 Service 运行命令与结果见 `risk_status.md`，不得为复核而重跑已完成臂。

```bash
git -C /home/truares/桌面/Tidybot-Universe-attention-native status --short --branch
git -C /home/truares/桌面/Tidybot-Universe-attention-native log -2 --format='%H %s'
git -C /home/truares/桌面/robosuite_sim-service status --short --branch
git -C /home/truares/桌面/attention_memory_service status --short --branch
sha256sum /home/truares/桌面/attentionbench-depth-memory-risk-20260929/{depth_audit.json,sha_index_v2.json}
sha256sum /home/truares/桌面/attentionbench-m1-m6-cross-v7-20260929/{candidate_b_completed_audit.json,sha256-index-final.json}
python -m json.tool /home/truares/桌面/Tidybot-Universe-attention-native/benchmarks/attention_harness/protocol/v2/formal_admission_freeze_2026-09-29.json
python -m json.tool /home/truares/桌面/Tidybot-Universe-attention-native/benchmarks/attention_harness/protocol/v2/formal_admission_v2_1_2026-09-29.json
```

| 原件 | SHA-256 | 作用 |
| --- | --- | --- |
| `/home/truares/桌面/attentionbench-depth-memory-risk-20260929/sha_index_v2.json` | `e34ea495eca9492411361faf6e43d1ff20f11fcf160952a0e96f61209ac3d99d` | 本轮风险原件 675/675 |
| `/home/truares/桌面/attentionbench-m1-m6-cross-v7-20260929/sha256-index-final.json` | `288dce9f270c86555dd316c137d352cffea2cf55a8926c34b56b4a6d3d98957a` | 跨模块 340/340 |
| `/home/truares/桌面/attentionbench-m1-m6-cross-v7-20260929/candidate_b_completed_audit.json` | `892cf1c0ddc574734285fa2357caa3ad496099bd0a21fff1cfd2672c75d6aedd` | 58/58 工程项 |
| `/home/truares/桌面/attentionbench-depth-memory-risk-20260929/depth_audit.json` | `124823ef6e8c1f2d9da1a596a0e7998645523653ef6b9ce2dafff079b1c2a12d` | 10 case／60 动作，根因未解 |
| `/home/truares/桌面/attentionbench-depth-memory-risk-20260929/robocasa-memory-v3/pair_audit.json` | `67a0834b390ce6ece11f0e75e521a8f6e2985e6e79c673a98b7dbba6ae82b7dd` | 新 v3 五配对 |
| `/home/truares/桌面/attentionbench-depth-memory-risk-20260929/robocasa-memory-v3/promotion_receipt.json` | `edcadea3e7942cd23399c0e9a2bbc05d0bdfd98c6c9399cbdea4e350366e53ac` | Service trusted v1 晋升 |
| `/home/truares/桌面/attentionbench-depth-memory-risk-20260929/robocasa-memory-v3/postpromotion/audit.json` | `8828e45c78bdc07a795005b738c2c823412306aeefc5f6fe54245ac8e4408058` | 独立限定使用和拒绝 |
| `/home/truares/桌面/attentionbench-m5-20260928/robosuite-current-v3/audit.json` | `59a4baaba6c957ca47abbf9df80aea8f2fa654929274230b5c122987c2e0d2fe` | Robosuite 限定 Memory 使用 |

**确切后续缺口：**预先审阅并锁定两主线任务公开基础策略、配置、demo／随机配置及 SHA；在协议上限内取得各任务五 seed 完整链和 101–125 全量基础策略档案，并单列任务可解性证据；查清并修复 depth 500 或给出能独立证明无选择偏差的替代证据。任何一项未过，资格仍为 false。`counter_to_cab` 的旧范围冲突已由 v2.1 明确解决，不再列为缺口。

本审计、协议修订及所引用的关键文件另有可机读的 [`SHA 索引`](attentionbench_formal_admission_sha_index_2026-09-29.json)；索引不包含自身，以免循环摘要。原审计 SHA `107f885ff2b5565afb01eda2184171b9831af6607411968f5541c5545c9603f0` 保存在索引修订记录中，原文本可从更新前提交恢复。
