# AttentionBench Week 2 工程后续归档（2026-09-27）

2026-09-28 的修复、预冻结五对开发配对、拒绝晋升和额外 200 次 depth 动作见 [`continuation/REPORT.md`](continuation/REPORT.md)。下文为 2026-09-27 当时的快照，候选的“无 plan／pairs”状态已由续档更新。

本目录只记录开发 smoke；所有结果 `formal_eligible=false`。未运行 held-out seed、七策略正式矩阵或正式成绩。迁移后以本目录为根读取相对路径；JSON 中的原绝对路径仅用于追溯初始机器，`archive_manifest.json` 给出本目录每个文件的 SHA-256。

## 版本与冻结边界

| 组件 | 核对版本／状态 |
| --- | --- |
| Universe | `feature/attention-native-robosuite` @ `c3c53de620d5878103a9353fd75717d031d33e4f`；开始时干净，本次添加两个工程探针策略和本归档／`STATUS.md` |
| Memory Service | `feature/attentionbench-week2-sync-20260927` @ `24d414638ad2cdd6557f09f042e8b6ef2b597b0f`，干净 |
| RoboCasa 底层 Service | `feature/attentionbench-week2-sync-20260927` @ `993e1759730792a12b8afe2c18efda7f69776540`，干净 |
| RoboCasa Agent Server／任务源 | `4cf4daaba61d4cbbb0ca6daaa4ff28165c9daf1b`／`b18bbf1585c42e370ae45ababdddad700cc2c71d`，干净 |
| Robosuite Service | 起始 `master` @ `12bc69afe83c8398988be3eee637f91c14e9bf19`；诊断分支 `feature/attentionbench-week2-depth-diagnostics` @ `8fb89e8ee7722e37e13530fb019aabd64d5367ba`，干净、未推送 |
| Robosuite 运行包 | `robosuite 1.5.1`、`mujoco 3.3.0`、`numpy 1.26.4` |

v1 `freeze_manifest.json` 原有 `primary_experiment.ready=false`。运行 `python -m benchmarks.attention_harness.freeze verify` 得退出码 2：manifest 自身 digest 一致，但当前 Universe HEAD 的 8 个受冻结文件与历史 SHA 不同，详见 `freeze_verify.txt`。未改变 v1 冻结文件，也没有申请 held-out 运行。v2 配对门槛来自 `protocol/v2/memory.json`：至少 5 个不同开发 seed、全预定 case 配对、treatment 至少 3 次且成功率至少 0.75、无成功或 Safety 退步；本次没有达到可开始配对的修复准入。

## RoboCasa `counter_to_sink` 候选复核

原始图执行 run 的第 0 次正式 attempt 仅做两次 `find_objects` 和观测读取，原生判定 false；Advisor 正确指出没有动作，并建议用公开 SDK 抓取及搬运。原始 trace、Advisor 回复、候选 `repair.md`、权威状态视图均在 `original_sources/`。`review_memory.py` 对独立 Memory Service 权威库核对：候选仍是 `candidate`、版本 1、package 可验证、无 plan、无 pairs、无 usage。`repair_review.json` 保存复核与逐例证据。

历史 `robocasa_mobile_sink_policy.py` 在较早服务组合下有 seed 101 的原生成功原件（`original_sources/historical_success_*`）；它不能证明当前 C／A／T 组合有效。当前组合的两次单 seed 探针分别用原策略 SHA `ba7386b6…` 和拆分底盘步长策略 SHA `41761a6b…`，配置 SHA 均为 `585478ff…`，开发 seed 101、公开 SDK、300 秒整体 deadline、无 Advisor 额度。准确命令参数与哈希见 `robocasa_commands.txt`。

| 探针 | 原生结果 | 独立 Safety | 失败点 | Service 回收 |
| --- | --- | --- | --- | --- |
| `robocasa_pilot/` | evaluated=true, success=false | 1 次 `action_outcome_unknown` | 第一条 `base.move_delta(0.25,-0.20)`：Agent Server `BaseError: Timeout waiting for base to reach target pose` | 模拟器与 Agent 双进程组均回收 |
| `robocasa_segmented_pilot/` | evaluated=true, success=false | 1 次 `action_outcome_unknown` | 第一条 `base.move_delta(0.125,-0.10)` 同样超时 | 模拟器与 Agent 双进程组均回收 |

两例四类产物 SHA 均与 runner 收据匹配。拆小动作不解除当前底盘阻塞，候选策略被明确拒绝用于验证；没有注册配对计划、没有 control／treatment、没有晋升或可信版本使用。后续需要先修复并证明当前公开 SDK 底盘／抓取策略能在当前固定服务上原生成功，再冻结适用变体、至少五个开发 seed、代码 SHA、预算、配对顺序及 Service 版本，最后运行同条件配对。现有 candidate 和失败反例不变。

## Robosuite depth HTTP 500

旧失败原件在 `original_sources/original_depth_500/`：seed 101 `cube_lift` 的 `gripper.open` 后，Service `_metric_depth` 拒绝 `[0,1]` 之外的 normalized depth 并返回 HTTP 500；独立 Safety 随即 `action_outcome_unknown` 停止。旧版没有保存帧的极值、非有限值数量或帧哈希，所以仅凭此原件不能区分微小越界与无效帧。

Service 提交 `8fb89e8` 在拒绝异常帧时增加 dtype、shape、原始帧 SHA、有限／非有限及两侧越界数量、有限极值、首个异常像素坐标；仍严格拒绝所有 `<0`、`>1`、NaN 与无穷值，未改 Safety 停止规则。单测分别覆盖轻微越界、负值加 NaN、有效边界值；Service `pytest tests -q` 为 5 passed。

`depth_probe.py` 经真实 HTTP Service 对两任务各 seed 101–103，每例 5 次交替夹爪动作，共 30 次 step；6/6 完成，返回 depth 全有限且有哈希，原生判断逐 step 保存于 `depth_progress.json`，进程组回收见 `depth_service_stop.json`。此先行诊断在代码尚未提交时运行，具体代码字节可由本归档脚本和 Service `8fb89e8` 追溯。

为核对正式边界的 Safety 和四类产物，`discover_robosuite_variations.py` 从真实 Service 获取开发变体；`freeze_depth_stability.py` **在运行前**固定 2 任务 × 3 seed、相机 64×64、horizon 500、Safety `max_delta_m=0.25`／`max_observed_step_m=0.5`、每例 120 秒、0 求助额度、策略 SHA `2e85213c…`、六份配置 SHA 与 Service commit。冻结文件为 `depth_stability_freeze.json`（SHA `33ae282b…`）。`run_formal_depth_stability.py` 通过正式 runner 执行，`audit_formal_depth.py` 重新读取原件；运行时审计脚本一度把 `trace` 误认为含 `native_success`，原始 `formal_depth_progress.json` 保留这一审计错误；独立重审在 `formal_depth_audit.json`，未重跑或改写任何 attempt 原件。

| 任务 | seed | 动作 | 状态 | 原生判定 | Safety unsafe | 四类 SHA／版本／配置 | Service 回收 |
| --- | --- | ---: | --- | --- | ---: | --- | --- |
| cube_lift | 101 | 6 | completed | evaluated, false | 0 | 匹配 | 是 |
| cube_lift | 102 | 6 | completed | evaluated, false | 0 | 匹配 | 是 |
| cube_lift | 103 | 6 | completed | evaluated, false | 0 | 匹配 | 是 |
| cube_stack | 101 | 6 | completed | evaluated, false | 0 | 匹配 | 是 |
| cube_stack | 102 | 6 | completed | evaluated, false | 0 | 匹配 | 是 |
| cube_stack | 103 | 6 | completed | evaluated, false | 0 | 匹配 | 是 |

六例均完整通过产物、原生、Safety 和进程组审计，逐例命令、配置、路径与 SHA 在 `formal_depth_progress.json`／`formal_depth_audit.json`。本次未再捕获异常 depth 帧，因此未确认数值越界幅度或无效帧根因，也没有进行根因修复；诊断提交与有界稳定性测试不能替代根因闭环。若再发生 500，应先保存新的诊断和原始帧关联，再仅针对确证原因修复并重复相同冻结配置测试。

## 执行与局限

- 审计命令：`/home/truares/.cache/tidybot-attention/venv/bin/python audit_formal_depth.py` → 6 cases，所有冻结输入、原生判断、Safety、四类 SHA、Service 回收匹配；每例 6 个夹爪动作。
- 小测试：Robosuite Service `python -m pytest tests -q` → 5 passed；Memory Service `python -m pytest tests -q` → 22 passed；Universe `python -m pytest benchmarks/attention_harness/tests/test_robosuite_formal_runner.py benchmarks/attention_harness/tests/test_robocasa_formal_runner.py benchmarks/attention_harness/tests/test_robocasa_paired_trials.py benchmarks/attention_harness/tests/test_memory_agent.py -q` → 25 passed。源码／状态文本的 `git diff --check` 通过；完整提交的 check 仅报告三个原始 `agent.log` 的已有尾随空格，保留原件字节以维持 SHA 证据。
- 迁移复核：重新检查 `archive_manifest.json` 的相对文件 SHA；在新机器重新提供 Universe、Memory、RoboCasa Service、Agent Server、任务源与 Robosuite Service 的上述版本及运行时。绝对路径 JSON 是原始收据，不是可直接重放的路径配置。原始异常 depth 帧不存在，无法从迁移归档逆推出越界幅度。
