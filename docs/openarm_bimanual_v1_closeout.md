# OpenArm 双臂（bimanual-v1）阶段性收尾

> 收尾类型：**降级目标收尾**（用户 2026-10-09 明确授权）。本轮以 Stage 1 为完成边界，
> “双臂物理交接 + 闭环连续运输”从本轮拆出，单列为下一 goal。本文是阶段性交付，不是正式实验结论。

## 本轮完成边界（降级后）

- 完成口径 = **单臂抓稳（左臂）+ 双臂同步抬升 / 搬运 + 原生任务成功**。
- 判定依据：Stage 1 已证，`run:attention-robocasa-counter_to_sink-seed101-316e6d2cb191`
  - `native_success = true`（原 `PnPCounterToSink.evaluate`，未改）
  - `unsafe_attempts = 0`
  - 动作 `53/53 完成、0 失败`
  - 生命周期 `9 case / 81 检查` 通过
  - 测试：Harness `852 passed / 10 skipped`、DOM `237/237`、合同 `68` 通过
- 证据：`/home/truares/Documents/Codex/2026-10-09/goal-openarm-attentionbench-openarm-sdk-runner/outputs/bimanual-v1/stage-one-acceptance.json`

## 降级说明（对冻结边界的修订，已获用户授权）

原冻结 `acceptance_freeze.json` 将“物理左右手交接（receiver 双侧接触 ≥12 物理步后 donor 才释放）”列入 Stage-1 pass，并把“14-DOF 联合规划 + atomic commit + 闭环共享运输 + 底盘转移”列为 Stage 2。本轮实际 10 小时迭代集中在交接与连续运输，完整任务始终未过。

按用户决定，本轮**降级收尾**：完成口径只到 Stage 1（单臂抓稳 + 双臂同步抬升，已证）；“闭环交接 + 连续运输”不再作为本轮完成条件，拆为独立下一 goal。此修订不放松任何力 / 速度 / 间隙 / 关节 / 动作 / 成功阈值，`formal_eligible=false` 不变。

## 保留的 Stage 2 部分进展（下一 goal 的起点，不丢弃）

- 迭代量：`work/` 181 文件，其中 `cooperationN.log` 40 份、`probe_cooperation_N.py` 20 份、`audit_*.py` 23 份、交接 / 接收区 / 规划屏障探测 16 份。
- 关键修复已落地：**左夹爪中心补偿方向修正**——原 URDF 实际指间中心在 TCP 正 Y 方向，移动 TCP 应计算“物体横向位置 − 指间中心”；新增 8 项失败测试后修正，当前合同测试 **205 通过**。
- 已过但尚未在连续运输下复验的环节：同步抬升成功、服务超时看门狗（11 项真实检查）、每步关节记录与底盘降速控制（红绿）。
- 仍失败的环节（下一 goal 重点）：底盘后退时失去夹持、夹爪速度门槛处失败、连续运输中夹持稳定性不足——完整任务仍未通过。

## 下一 goal（单列，见下）

目标描述另附，核心是：在 Stage 1 基础上完成**双臂物理交接 + 移动底盘连续运输**，使 `counter_to_sink` 在双臂闭环下 `native_success=true`、`unsafe=0`，并继承本轮全部失败证据与左夹爪补偿修正。

**时间限制：90 分钟硬截止。** 开工即计时；提前约 10 分钟停止新派发、只做收尾；到点无论交接／运输是否通过都封存当前 candidate，逐项列“已完成 / 未完成 / 阻塞原因”，不自动续轮。此时间盒是为了避免重演本轮 10 小时无界迭代。

## 边界与纪律

- 包含：双臂交接、14-DOF 联合规划、atomic commit、闭环共享运输、连续运输夹持稳定、整组中止负控。
- 排除：硬件、轮胎牵引动力学、held-out、RoboCasa365 新任务、真人 Hint 自动提交。
- 纪律：下一 goal 开工前重新冻结验收包（边界 + 逐项通过标准 + 证据位置）；每项先红后绿 / 独立证据；不把 PENDING 写成 PASS；全程 `formal_eligible=false`。
