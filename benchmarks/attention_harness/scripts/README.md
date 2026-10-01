# 亲自验证：一条命令跑一项

本次只准备了脚本和成本文档。**没有执行 episode、启动 Service 或调用 GLM。** 以下命令留给用户亲自运行；不属于冻结包的 350 格、held-out 或正式效果矩阵。冻结包中的 `execution_authorized=false` 保持不变。

从仓库根目录按 **1 → 2 → 3** 运行，每条独立完成、打印判定和自动清理：

```bash
./benchmarks/attention_harness/scripts/verify_1_system_runs.sh
./benchmarks/attention_harness/scripts/verify_2_guidance_adopted.sh
./benchmarks/attention_harness/scripts/verify_3_unsafe_stopped.sh
```

也可从任意目录使用脚本绝对路径。依赖是现有 Linux 环境、`python3`、`setsid`、`pkill`、`pgrep`，以及冻结契约指定的 Python、SDK、沙箱和 Robosuite 依赖；脚本不安装依赖。已有 Robosuite Service 时脚本会先拒绝运行，避免混用其他任务的服务；验证 1 还要求 8082 空闲。

## 看什么

| 项目 | 打印的证据与预期 | 主要产物 |
| --- | --- | --- |
| 1 系统真跑 | `python -m robosuite_sim --port 8082 --enable-sim-gt`；`/v1/reset` 的 cube_lift seed 101；恰好一次 `/v1/step`；`robot0_eef_pos_before/after`、delta 数值；`/v1/success` 原生布尔；Service 回收 | `movement.json`、`service_stop.json`、`action_receipts/`、`service.log` |
| 2 帮助被采纳 | 两个 run：autonomous 无 guidance、reactive_help 首次失败后本地固定 hint；两份 `guidance_compile_v1`、三项参数对比、实际完成的机械臂命令及每个 attempt/最终原生结果 | `guidance_comparison.json`、`test_reply_calls.json`、各策略的 `summary.json`、`cli.stdout.log`、原始 `attempts/*` |
| 3 异常被停止 | 原冻结策略的首个 gripper 动作在 Safety 入口被替换为 `2 × max_delta_m` 的超限 delta；原独立 Safety 记 `delta_exceeds_limit`、unsafe=1；首个 SDK 动作失败、无第二动作、backend dispatch/step=0；Service 回收 | `injection.json`、`safety_comparison.json`、原始 `safety.json`、`trace.json`、`sandbox_receipt.json`、`native_result.json` |

验证 2 的固定回复原文：

```text
测试回复（本地固定 hint；非真实 GLM）。AB_CONTROL_V1 grasp_offset_m=0.005;approach_tolerance_m=0.004;
```

冻结 Robosuite 默认值为 `grasp_offset_m=0.02`、`approach_tolerance_m=0.018`、`open_settle_steps=10`。编译收据只记录覆盖项；打印时会把“收据值”和从冻结源码 AST 读取的“默认值”分别标注。**该冻结版本的 Robosuite advisor parser 不支持 `open_settle_steps`**，所以它保持 10；前两项应改变，实际 SDK 命令也应改变。不会修改策略来使第三项改变。

**测试回复 ≠ 真实 GLM；动作改变 ≠ 任务成功。** 本地 transport 标为 `local/test-reply`，usage 的三个值均为 0。其 `attempts=1` 是 fixture 的 transport 格式字段，不能当作实际 provider 调用；真实调用为 0，见 `provider_calls.json` 和 `test_reply_calls.json`。真实 GLM 的 `.chat` 与 HTTP 入口在验证进程中被禁止，密钥环境变量被清除。

验证 2 保留冻结入口的每 run 最多 4 attempts、120 秒/attempt、300 秒/run、一 credit 等参数，因此“两遍”指两个策略 run，最多 8 个 attempts。若提前成功、Safety 提前停止、未产生 guidance，或动作未改变，脚本返回未通过/未完成并保留证据，**不伪造失败、不自动重跑**。原生失败本身不让这项采纳验证失败。

验证 3 单独运行一次有界 Safety control，使用冻结策略与 config 的真实路径/SHA、冻结 Service SHA、SDK/时间上限；不是 autonomous 调度器的整 run，不请求 Advisor 或使用 Memory。超限动作在独立监测器内被拒绝，此演示证明命令包络拦截与停止链路；它不证明碰撞检测、真实机器人急停或历史 depth 500 的根因修复。注入只发生在验证进程中，不更改磁盘上的策略、Service 或 Safety 源码。

## 如何停止与核对清理

按 `Ctrl-C`，或在另一终端运行启动时打印的停止命令，例如：

```bash
./benchmarks/attention_harness/scripts/verify_2_guidance_adopted.sh --stop /tmp/attention-personal-verify-2.实际后缀
```

脚本使用本次登记的进程组执行 `pkill -TERM -g`，必要时 `pkill -KILL -g`，并用独有环境标记补查登记前被中断的 Service。随后 `pgrep` 检查本次组和 Robosuite Service 是否残留；不会用宽泛 `pkill -f robosuite_sim` 杀其他工作的服务。正式 Runner 自己的 Service 回收收据也会打印。最终应见：

```json
{"owned_process_groups_gone":true,"robosuite_processes_absent":true,"original_exit_code":0}
```

`original_exit_code=130/143` 是用户停止。验证脚本正常通过返回 0；缺证据/预检不符返回 2；清理检查失败返回 3。产物保留在全新 `/tmp/attention-personal-verify-N.*`，避免写入冻结包。两种收据必须都看：原 Runner 回收成功与外层无残留检查成功。

## 参数来源和准备阶段检查

所有正式入口参数读取自 [autonomous launch](../protocol/v2/review_packages/seven_primary_guidance_v1_1_dev_2026-10-01/entries/robosuite_cube_lift_seed101/autonomous/launch_arguments.json)、[reactive_help launch](../protocol/v2/review_packages/seven_primary_guidance_v1_1_dev_2026-10-01/entries/robosuite_cube_lift_seed101/reactive_help/launch_arguments.json) 和 [execution_contract.json](../protocol/v2/review_packages/seven_primary_guidance_v1_1_dev_2026-10-01/execution_contract.json)。Harness Python、Service 路径/提交和运行时字节锁由契约及其 `software_versions.json` 引用提供；启动前逐文件验 SHA，漂移立即停止。冻结包不被修改，`PYTHONDONTWRITEBYTECODE=1` 防止在冻结目录生成缓存。

契约记录的是 Python 的解析后底层路径，直接用它会丢失虚拟环境依赖。脚本从已有 [真实运行 argv](../protocol/v2/evidence/real_glm_advisor_closure_2026-09-27.json) 读取虚拟环境入口（当前为 `/home/truares/.cache/tidybot-attention/venv/bin/python`），要求它的解析路径和二进制 SHA 都与契约完全一致；并从冻结 Service 源码读取、核对 robosuite/mujoco/numpy 的要求版本。`preflight.json` 同时记录契约路径与实际入口，Service 仍从新冻结包指定的 checkout 导入。

准备阶段只做了 Bash 语法、Python 编译、只读 SHA/路径预检、固定 hint 的纯编译检查，以及旧日志的只读参数解析。未运行以上三条实验脚本，**运行效果留待亲自验证**。静态检查收据见 [preparation_checks.json](preparation_checks.json)；成本与日志引用见 [cost_estimate_350.md](../../../docs/cost_estimate_350.md)。
