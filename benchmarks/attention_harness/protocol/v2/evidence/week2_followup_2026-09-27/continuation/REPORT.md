# Week 2 后续工程续档（2026-09-28）

本目录接续上级 `REPORT.md`。所有试验均为开发工程 smoke，`formal_eligible=false`；未使用 held-out seed，未运行七策略正式效果矩阵。相对文件 SHA 由上级 `archive_manifest.json` 固定；原始 JSON 中的绝对路径是当时的执行收据，迁移后用相对路径及 `verify_continuation.py` 核对。四份含过期 cancel token 的本地调试 JSON 未收入归档，其余原始失败、服务日志和权威库快照保留。

## 工作树、版本与冻结

开始时 U 为 `feature/attention-native-robosuite` @ `1a7c16f`，M 为 `feature/attentionbench-week2-sync-20260927` @ `24d4146`，C 为同名分支 @ `993e175`，R 为 `feature/attentionbench-week2-depth-diagnostics` @ `8fb89e8`，均干净。A Agent Server @ `4cf4daa`、T RoboCasa task source @ `b18bbf1` 也干净。开始时 v1 freeze 自检仍有历史 8 个文件 SHA 不符，详见上级 `freeze_verify.txt`；没有改写冻结清单。

本轮修复后配对实际绑定 U `381b764fa21e42048904dfae0621dc2462f9768b`、M `24d414638ad2cdd6557f09f042e8b6ef2b597b0f`、C `320020a0c94434af31ec02df3413229576490fef`、A `4cf4daaba61d4cbbb0ca6daaa4ff28165c9daf1b`、T `b18bbf1585c42e370ae45ababdddad700cc2c71d`，五份源码树均由 `robocasa_validation_freeze.json` 的 SHA attestation 证明干净。C Runtime 为 Python 3.11.15、ManiSkill 3.0.0b22，Python 与 ManiSkill 源码树 SHA 在五份 `formal_robocasa_counter_to_sink_seed*_rebind.json`。R 继续绑定 `8fb89e8ee7722e37e13530fb019aabd64d5367ba`，运行包 `robosuite 1.5.1`、`mujoco 3.3.0`、`numpy 1.26.4`。

## RoboCasa 候选：找到可运行策略，但未达 Memory 晋升门槛

原始失败、Advisor 答复、候选 package 和前两次当前运行时失败复核在上级 `original_sources/`、`repair_review.json`。本轮进一步定位当前正式配置第一步底盘超时：ManiSkill `reconfigure=True` 会重建机器人，C reset 后仍持有旧 `self.robot`，导致里程计读旧机器人、控制施加于新机器人。C `320020a` 在 reset 后重新绑定；`base_physics_reconfigure_diagnostic.json` 等记录对象替换，真实公开 SDK 单步动作的原生判定 false、Safety 0、四类哈希和服务回收见 `formal_base_one_move_rebind/`。

随后发现 Agent Server 默认 lease 释放时对机器人回中并软 reset，令连续 SDK job 之间的目标位置失效。U `80f9f40` 使独占正式 Agent 使用其已有的 `--no-reset-on-release`，仍保留独立 Safety 和双进程组回收；U `381b764` 在 reset 后核对确切 task prompt。修复后的分段公开 SDK 策略 SHA `41761a6b…` 在 seed 101 试跑原生成功、Safety 0、哈希及回收通过（`robocasa_segmented_preserved_pilot/`）。因此仅**批准开发验证**，绝非批准晋升。探索期的额外底盘动作、低抓取和通用策略失败均留在对应 `*_pilot/` 原件。

验证前已固定 `robocasa_validation_freeze.json`（SHA `d84ac3f307f98d94b91f2a50564ac3a9d1a1274392dd12d950190bb673e8be63`）：`counter_to_sink` 的开发 seed 101–105，现场发现的 scene／object／camera／prompt 逐例锁定；同一策略 SHA `2db223b79a3c6850c49d6d4c5ee8e1f0638cf2fdc5f79d990669f25f63e498c0`；每 seed 先 control 后 treatment，不替换失败 case。control 不接触候选并复现原始无动作基线，treatment 显式 `retrieve_memory(candidate_id)` 后用公开 SDK 抓取、搬运。每 arm 求助 0、SDK 调用上限 200、策略 240 秒、Agent job 90 秒、Service 300 秒；Safety 限制不变。`robocasa_validation_preflight.json` 记录 Memory Service 权威预注册 plan、原始 package 验证和未执行状态。

| seed／目标 | control 原生／unsafe | treatment 原生／unsafe | treatment 结果 |
| --- | --- | --- | --- |
| 101 boxed drink | false / 0 | true / 0 | 成功 |
| 102 cup | false / 0 | false / 0 | 提起时滑落 |
| 103 mango | false / 0 | false / 0 | 动作完成，原生条件未满足 |
| 104 onion | false / 0 | false / 0 | 目标未能唯一辨认 |
| 105 rolling pin | false / 0 | false / 1 | 手臂未收敛，独立 Safety `action_outcome_unknown` 停止 |

`robocasa_pair_progress.json` 和 `robocasa_pair_audit.json` 对 10 个 arm 核对相同策略／配置、原生 evaluator、每条 trace 与 Safety SHA、treatment 实际检索事件（每次 1 条）、control 零检索、双 Service 正常回收且进程组消失；原件在 `robocasa_pair_attempts/`、服务日志在 `robocasa_pair_services/`。两次执行脚本错误产生的不完整 control 尝试也保留原件，未作为预注册 pair、没有替换任何冻结 case。权威 SQLite 快照及原始候选 package 位于 `authority/`，迁移后以 `authority/attempts/attention-robocasa-counter_to_sink-seed101-59f3e6e1b326` 为 Memory Service artifact root；重验得到 candidate v1、5 pairs、plan 存在且 package 可验。

现有门槛要求 5 个预定开发 seed、treatment 至少 3 成功且成功率至少 0.75、无 Safety 退步。实际仅 1/5 且 seed 105 有 Safety 退步，Memory Service 晋升调用以 `paired safety regression blocks memory promotion` 拒绝（`robocasa_promotion_gate.json`）；权威状态仍 `candidate` v1。独立同适用上下文检索为空、version 授权拒绝、没有实际 trusted 使用（`robocasa_candidate_denial.json`）。不能把单例成功当作 Memory 晋升或限定使用验收。

## Robosuite depth 500

原始 500 帧没有保存，旧 Service 仅留下 normalized depth 超出 `[0,1]` 的异常文本（上级 `original_sources/original_depth_500/`）。R `8fb89e8` 已在严格拒绝时记录 dtype、shape、帧 SHA、finite 极值／数量及首个坏像素，仍拒绝越界、NaN、无穷值；未放宽 Safety 停止。原来上级的两任务 × seed 101–103 × 6 次正式 runner 动作：6/6 原生判定、Safety 0、四类产物 SHA 和 Service 回收均核对。

再冻结 `depth_stress_freeze.json`（SHA `9fac57bc…`）：`cube_lift`、`cube_stack` 各 seed 101–105，64×64 `agentview`，horizon 500，每例交替夹爪动作 20 次，共 200 次真实 HTTP step。`depth_stress_progress.json` 保存每一步原生判断、深度有限性／极值／SHA；10/10 完成，无异常帧，`depth_stress_service_stop.json` 记录回收。此额外 HTTP 压力探针不含独立 Safety artifact；独立 Safety、四类 SHA 的边界证据仍以上述六例正式 runner smoke 为准。由于异常没有重现，无法在微小数值越界、无效帧或其他原因间作出确定判断，也**未进行根因修复**。下一次 500 应先保留新增诊断字段对应的原始异常帧，再针对确证原因修改，重跑固定的正式边界与重复动作配置。

## 命令与复核

以下命令从上级归档目录执行；`run_robocasa_pairs.py` 锁定原始 checkout SHA，重放需先取得冻结版本并选择新的隔离权威库，不能直接写入本归档快照。

```text
/home/truares/.cache/tidybot-attention/venv/bin/python continuation/run_robocasa_pairs.py
/home/truares/.cache/tidybot-attention/venv/bin/python continuation/audit_robocasa_pairs.py
/home/truares/.cache/tidybot-attention/venv/bin/python continuation/depth_stress_probe.py
python continuation/verify_continuation.py
python archive_manifest.py verify
```

最后两条是纯读取迁移复核：10 arms、200 depth steps、authority SQLite 完整；上级 manifest 校验所有相对文件 SHA。相关小测试：U 四套目标测试 **28 passed**，M **22 passed**，R **5 passed**；C 的单个 robot rebind 测试用 `/home/truares/miniconda3/envs/maniskill/bin/python` 直接调用通过（该环境未装 pytest），真实 Service 单步和完整 seed 101 试跑提供运行时证据。`formal_eligible=false`。未达到的验收：R 原始异常帧与根因修复；RoboCasa Memory 晋升及可信版本实际使用。
