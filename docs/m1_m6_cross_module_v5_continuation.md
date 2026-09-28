# M1–M6 跨模块工程验收续验：版本隔离记录

本记录仅跟进首版 Robosuite Advisor 预算超限和本次 trusted Memory 未使用。M1–M6 既有模块验收门槛不重开；所有续验均为 `formal_eligible=false`，不运行 held-out、正式七策略效果矩阵、真人或 LIBERO。M3 历史 depth 500 根因风险仍未解决。

## 首版与失败修订

- 首版冻结及真实运行原件：`/home/truares/桌面/attentionbench-m1-m6-cross-20260929/`。Robosuite 真实 Advisor 一次回复为输入 2677、输出 549、合计 3226 tokens，超过冻结 3000；响应未在本次 run 中持久采用，trusted v1 未获得本次 grant/use。首版 `final_audit.json` 保持 `failed_acceptance`，不得改写。
- v2：`/home/truares/桌面/attentionbench-m1-m6-cross-v2-20260929/`。冻结 4096 tokens 后，Robosuite 两份 Dev 候选均在两次格式调用后输出非纯 Python，M2 批准前失败，0 正式 attempt。`failure_audit.json` 与 SHA 索引保留。
- v3：`/home/truares/桌面/attentionbench-m1-m6-cross-v3-20260929/`。两份 Dev 候选收到 HTTP 200 但无最终内容，0 正式 attempt。独立模型探测显示 `finish_reason=length`、4096 个生成 tokens 全为 reasoning，没有可批准源码；原件另存。
- v4：`/home/truares/桌面/attentionbench-m1-m6-cross-v4-20260929/`。真实 Dev 产出源码，但调用受限 worker 不提供的 `next()`；静态预检在批准前拒绝，0 正式 attempt，原件及 SHA 索引保留。

不同冻结版本不能拼接为一次通过。上述修订的源码、预算与执行文件快照分别锁定，历史失败原件不回写。

## v5 冻结和待批准候选

外部根：`/home/truares/桌面/attentionbench-m1-m6-cross-v5-20260929/`。`freeze.json` SHA-256 `2d5cc2c3c30b4ff1bb77724bb0fc1413cad5f47377d6f21fcf1057ff3ba5fc45`；`execution_lock.json` SHA-256 `9a96e9bed17310c19059171f9cdb4e2686e988121b61942386b4ad2077954a3b`。U/M/R/C/A/T 提交与首版相同；U 跨链适配器新增 opt-in 公开进度投影，只比较公开 SDK 在抓取／抬升前后的 cube 高度，绝不读取原生 evaluator。原生结果、独立 Safety 与 M4 判定门槛保持各自权威。相关回归 29 passed，v4 投影回归 54 passed。

每套至多两份 Graph 候选、每候选两次 Dev 格式调用、每次一个真实 provider attempt；每正式 run 最多四次模拟器 attempt、一次 Advisor credit、4096 tokens、总截止 300 秒。4096 比首版真实回复 3226 高 870 tokens（约 27%）；四次 attempt 为同 run 内的 inspect → hint → 有公开依据的进度检查 → Memory 授权使用留出机会。此序列是预检条件，不是成功保证。独立 Memory Service 在 seed 103 精确范围可检索到 trusted v1；首版公开 Trace 证据不足使所选 full planner 选择 inspect/request。v5 无 Service 预检证明：若真实后续公开 SDK 前后位置证实未抬升，原策略会选择 Memory；人工构造的条件样本仅验证代码路径，不算本次运行证据。

两套真实 Graph Dev 已生成源码并停在 `awaiting_approval`；批准前各自独立重启，`/attention/auto-start` 均返回 `spawned=[]`，0 正式 run。待审精确源码、模拟器配置、Dev 收据、任务锁和入口锁 SHA 见外部 `candidate_review.json`（SHA-256 `4bd8440a130368def8f93c9768c6619e9e825ff0723430e839602fcb59ff13d6`）；`preapproval_audit.json` 与 `sha256-index-preapproval.json` 保存逐项审计和原件哈希。由于入口预算及 SHA 已变化，首版批准不可复用。正式运行只能在用户对 v5 两套精确 SHA 明确批准后继续。

补充无 Service 范围预检见外部 `scope_preflight.json`（SHA-256 `efa91079cc0aa657b1b5db9734945b3ca370c4517afcc8509abd879a40e483a1`）：Robosuite 在精确 seed 103 场景检索 trusted v1，改变 scene、object set、camera、task variant 或 task 后均返回空；RoboCasa 两份 candidate 的授权请求均被拒绝。此预检没有启动 Service 进程或模拟器 attempt，不替代本轮正式 grant/use。扩展 SHA 索引见 `sha256-index-preapproval-extended.json`。

完成后审计脚本 `audit_completed_runs.py` 已预置在 v5 外部根，要求新 run 的 Graph／批准身份、四类正式产物、Service 回收、真实 Advisor 回复持久采用与 token 预算、随后本次 trusted v1 精确 attempt grant/use、RoboCasa candidate 拒绝、UI 投影、重启去重和隔离故障记录全部通过才输出 `passed`。该脚本尚未针对新正式 run 执行，也不能把上述预检算作通过。

只有 v5 双模拟器同版全链、Robosuite 本次真实 Advisor 响应持久采用后 scoped trusted v1 的 exact-attempt grant/use、RoboCasa candidate 不授权、故障关口与重启复用全部通过，才更新 `STATUS.md`、模块进度页并提交当前功能分支。当前尚未通过，三者均保持不动。

## v5 批准后真实运行与最终判定

用户批准 `candidate_review.json` SHA-256 `4bd8440a130368def8f93c9768c6619e9e825ff0723430e839602fcb59ff13d6` 后，两个独立批准文件绑定各自 Dev 源码、配置、生成收据和入口锁。批准审计见外部 `approval_audit.json`。两个 Graph 各只启动一次正式 run，`formal_eligible=false`；未运行 held-out、正式七策略效果矩阵、真人或 LIBERO。

Robosuite `cube_lift` seed 103 的正式 Runner 完成四个 attempt。真实 Advisor 回复为 3325 tokens，处于冻结的 4096 上限内，随后 attempt 使用了该回复；控制链从公开 Trace 的 inspect/request 进入 scoped trusted v1 检索，并产生本次 exact-attempt grant/use。四类正式产物、原生评估、独立 Safety 和 Service 回收均通过身份与 SHA 复核。原生任务结果仍为失败，符合本次仅要求工程链路而不要求策略成功的冻结条件。

该 run 在 Bridge 的 Eval 输入上触发 `Eval evidence packet exceeds size limit`：四个 attempt 的重复绝对产物路径使投影超过 12000 字符。已在 `attention_eval.py` 将**已验证**的产物引用投影压缩为 SHA，保留对原始四类产物的逐项读取、身份、哈希、原生评估、Safety 与 Service 回收校验。32 个相关回归通过，Eval 投影为 8596 字符。通过落盘 Bridge stdout、退出码、批准身份、正式 run 和全部 attempt 复核后，Graph 从既有 run 检查点恢复完成 Eval/UI；没有重跑模拟器 attempt。恢复原件见 `robosuite-eval-checkpoint-recovery.json`。

RoboCasa `counter_to_sink` seed 101 的正式 run 在一个 attempt 后由独立 Safety 停止；原生结果为失败，Eval 与 UI 完成。两套 Graph 完成后分别重启，`/attention/auto-start` 均返回 `spawned=[]`。隔离的内存副本故障验证拒绝跨层 attempt 身份错配与产物哈希错配；Robosuite 已认领的中断 dispatch 由落盘 run 恢复，没有产生第二个 run。RoboCasa 未晋升 candidate 没有获得本次授权或使用。M3 历史 depth 500 根因风险仍未解决。

**最终审计为 `failed_acceptance`。** `completed_audit.json` 共 56 项，其中 55 项通过；唯一未过的是冻结的 `skill-agent-setup/claude-code/attention_eval.py` SHA。冻结值 `85759388850b09dc8dd071b034c146b74d15f7d10c782cc1f868d92b92161598`，恢复时修订值 `f1edb3f373dcd5e039b1e0839394df833a1def4eed4c734d680546b60677336e`。虽然同一原始 Robosuite run 已成功恢复后续链路，此修订发生在 v5 冻结和运行之后，不能宣称“双模拟器同冻结版本全链通过”。失败证据、批准记录、命令、两个真实 run、逐项审计与 SHA 索引保存在外部 v5 根。按预先通过条件，`STATUS.md`、模块进度页均不更新，功能分支不提交。
