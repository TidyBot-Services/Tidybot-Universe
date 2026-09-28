# M4 Attention 决策：冻结工程验收包

冻结于 2026-09-28，基线 Universe `31b02ac850826f32290d2be98aff0f7af479fd3b`。仅开发 seed 101、`sim_gt`、双正式 Runner 工程路径；全部 `formal_eligible=false`。本包不以原生任务成功率为门槛，不运行 M5 晋升效果、M6 UI／真人、held-out 或七策略正式效果矩阵。M1 入口锁、M2 批准摘要、M3 正式执行／Safety／四产物契约不得放宽；M3 的旧 depth 500 未解决风险继续保留。

## 先行审计与依赖

共用 `sim_gt_attention_run.py` 在每个失败 attempt 后从持久化 Advisor 可见 Trace 取证，由 `core/policies.py` 选择动作，`formal_attention_run.py` 将公开输入、Memory grant 交给 RoboCasa／Robosuite `FormalSuiteRunner`。正式 CLI 经 `formal_entry.py` 锁代码、配置、demo 和随机预注册摘要。`AttentionRuntime` 持久化请求状态和求助额度，`SimGTAdvisorProxy` 使用 GLM 或明确标记的测试回复；独立 Safety 来自正式 Runner 的 safety 产物。Agent Server 与 RoboCasa Service、Robosuite Service、Memory Service、PARCC GLM 是跨模块依赖，任务原生 evaluator 和 SHA 产物由 M3 保证。

基线缺口：既有双 suite × 七策略正式链测试只运行一次 attempt、零求助；真实 GLM 仅覆盖 `reactive_help`。因此不能据旧测试判定七种策略完整决策语义通过。验收中须新增多 attempt、失败阈值、决策证据、拒绝和预算边界的针对性测试，并重跑有限真实正式 Runner。`autonomous` 可使用先前已 trusted 的 Memory，但不可在线求助；`demo_first` 仅用运行前获批且双层 SHA 锁定的公开视频／公开 SDK 动作；随机目标数和槽位在首个结果前固化，提前结束差额明示。trace-aware 仅使用投影的 Advisor 可见 Trace，`hint_only` 不检索 Memory；full 可按证据选择 hint、适用 Memory、approval 或本地 interrupt。独立 unsafe 立即停止，不等模型。

依赖快照（本次工作开始时均无脏改动）：Universe `31b02ac`；Robosuite Service `081cd57`；RoboCasa ManiSkill Service `320020a`；Agent Server `4cf4daa`；RoboCasa tasks `b18bbf1`；Memory Service `24d4146`。实际执行记录须再次保存每个 HEAD、状态和可执行命令，出现版本偏移即另记偏差，不复用旧结果。

真实 smoke 的批准输入固定为 `policies/formal_robosuite_normal.py` SHA `fb9f0f...` 配 `m2_robosuite_seed101_config.json` SHA `31ff17...`，以及 `policies/robocasa_noop.py` SHA `4b3ab7...` 配 `m2_robocasa_seed101_config.json` SHA `fd32f0...`，均位于 `benchmarks/attention_harness/protocol/v2/`。旧 `formal_robosuite_cube_lift_seed101.json` 锁的是 `12bc69a`，与当前 Service `081cd57` 不符；首次预检如实被正式边界拒绝，拒绝原件保存，不计入通过样本。后续仅使用上述与当前依赖匹配的 M2 冻结配置。

## 冻结矩阵和上限

| 关口 | RoboCasa `counter_to_sink` | Robosuite `cube_lift` | 通过证据 |
|---|---|---|---|
| A 七策略完整语义 | 每策略多 attempt 确定性正式边界测试 | 同左 | 初始/后续输入、策略动作及理由、可见 event/evidence/prior ID、请求状态与回复、response-use→下一 execution、Memory grant、预算 |
| B 拒绝和边界 | 缺失／篡改 demo、错误范围 Memory、零求助／token 预算、oracle 注入、独立 unsafe | 同左 | 拒绝发生在相应边界，无越权、无静默回退、无多余模型调用 |
| C 真实正式 Runner | `reactive_help` 两次 attempt、一次真实 GLM；其余策略的真实 Runner 单 attempt 有界 smoke 可共享 M3 入口契约 | 同左 | 原始 Trace、决策、请求／回复、Safety、四类产物 SHA、进程回收；GLM guidance 确实进入第二 attempt |
| D 回归 | M1–M3 定向测试 | 同左 | 旧契约不变、`formal_eligible=false` |

每套真实链最多 **2 attempts、1 求助、1 次 GLM HTTP provider 调用、每请求最多 1024 输出 tokens、单 attempt 截止 120 秒、整套最多 240 秒**；整个 M4 在线上限 2 次 GLM 请求，测试回复零线上费用。开发测试每例最多 3 attempts、2 求助、8,000 token 预算；总测试请求为确定性 fixture。若真实环境导致某关口未达，保持 M4 未关闭并保留原始失败证据。测试不依赖任务成功率。

关口 C 必须保存冻结配置／版本／实际 argv、`attention_run.json`、SQLite 与原始 Trace、每个决策和 request/response 状态、Advisor payload 的可见性核对、token／逻辑求助费用、Safety、正式 trace／safety／sandbox_receipt／native_result 原始文件及 SHA。摘要和索引必须能重新计算哈希，不以口头报告代替。关闭条件为 A–D 全通过，且状态页与模块进度页仅引用本包实际证据。

## 首轮执行及结论（原件保留）

原始证据目录：`/home/truares/桌面/attentionbench-m4-20260928/`；`manifest.json` 保存版本和完整 argv，`raw_index.json` 保存 149 个原始文件的 SHA-256，`fixture-audit.json` 和 `online-audit.json` 由 `protocol/v2/audit_m4.py` 重算四类正式产物、独立 Safety、原始 Trace、请求状态／回复使用、下一执行输入和服务进程组回收。审计自身的 `passed` 只表示已经产生的文件完整，不能代替在线闭环关口。

| 关口 | 本轮结果 |
|---|---|
| A | 双套七策略三 attempt 正式边界矩阵，以及 approval、适用 Memory grant、`hint_only` 无检索、独立 unsafe／未完成 unsafe、oracle 注入均通过；最终定向回归 **95 passed**。full 的安全 trace interrupt 与重复失败规则另由现有 `test_attention_policies.py`／`test_trace_assessment.py` 覆盖。 |
| B | 双套零 token 不调用 Advisor、错误范围 Memory 不选择、M1 缺失／篡改 demo 和随机预注册拒绝测试通过；请求／token 使用从 SQLite 与 summary 双向核对。 |
| C | 双套明确标记的确定性测试回复各完成 **2 次真实正式 Runner attempt、1 次请求／答复／采用**，四类 SHA／Safety／进程回收审计通过。双套各 1 次真实 GLM provider 调用均超时：各只完成首个真实 attempt，请求从 pending 转为 cancelled、无 response、0 求助额度及 0 token 记账，没有第二 attempt。**在线 GLM 0/2，关口 C 未通过。** RoboCasa 与 Robosuite 原始超时 summary、SQLite 和日志保留；未在冻结的两次 provider 调用上限之外重试。 |
| D | M1／正式链／trace／策略／M4 定向 **95 passed**；全 Harness **371 passed、10 skipped、7 failed**，7 项仍是 M3 已记录的 6 个 `test_memory_v2.py` trusted context 和 1 个 RoboCasa generated trusted-use 旧失败。`git diff --check` 通过。M1–M3 的锁、审批与正式产物契约未修改。 |

首次使用旧 Robosuite 配置的真实运行被正式边界正确拒绝，原 `result.json`／stderr 保留；RoboCasa 首次手输批准 policy SHA 错字由 M1 preflight 在建 run 前拒绝，stderr 保留。这两项不混入通过计数。M3 历史 depth 500 根因继续未解。**M4 未关闭，所有工程结果 `formal_eligible=false`；需在新的明示预算／配置冻结下补双套各一次成功在线 GLM 回复进入下一正式 attempt 的证据，再重审关口 C。**

## 线上关口 C 补验与最终工程结论

首轮两次 `URLError: timed out` 的原件和 149 文件索引保持不变。补验前核对：客户端实际端点为 `https://litellm.parcc.upenn.edu/v1/chat/completions`，凭据由外部 wrapper 临时传入 Harness、未写入命令或证据；正式 CLI 使用 90 秒客户端超时、`--single-glm-call` 将格式尝试及 provider HTTP 尝试各限为 1。先前同机 DNS 可解析但 TCP 443 超时，故不能把首轮失败直接归因于模型推理。恢复后同一 Harness 环境完成 TCP／TLS，并以不生成模型回复的带凭据 `/v1/models` GET 收到 HTTP 200。连通收据 SHA-256 `dedaa9fc7d5a79b1161d881335ef64f4594afe765afa1ae71652470161c8a144`。

新冻结配置位于 `/home/truares/桌面/attentionbench-m4-c-recheck-20260928/freeze.json`，SHA-256 `bfeac6af70090eacbdedde80382f369a9487733e4729a86c79ae4f110d6a812d`：双套各 1 次运行、最多 2 attempts／1 求助／1 次 GLM provider HTTP 调用、每请求最多 1024 输出 tokens、每 run 8000 token；客户端超时 90 秒、正式 Runner 每 attempt 截止 120 秒、每套外部墙钟上限 360 秒。它只授权本次补验的至多 2 次新 provider 调用，不改写首轮冻结上限或失败证据。实际 argv、代码与依赖 HEAD、批准 policy／config SHA 和新配置 SHA 见 `launch_plan.json`，SHA-256 `1ac9e871f9488abb64e910070b23471ed889ab24a3a78aad1d9b2c013c8a79ef`；U `7e2e8e4`，R `081cd57`、C `320020a`、A `4cf4daa`、T `b18bbf1`、M `24d4146`，执行前均干净。

补验原始证据目录 `/home/truares/桌面/attentionbench-m4-online-c-20260928/`。RoboCasa `counter_to_sink` 与 Robosuite `cube_lift` 均在 seed 101、`sim_gt`、`reactive_help` 下各完成 **2 次真实正式 Runner attempt、1 次未缓存真实 `parcc/GLM` 回复、1 次求助**。首个失败 attempt 的持久 Advisor 可见 Trace 重建出的请求 cache key 与持久回复一致；回复分别使用 2172／2210 总 tokens（其中输出 397／322，均 ≤1024），SQLite 和 summary 记账一致。两套第二次 attempt 的正式 trace `attention_input.advisor_guidance` 均等于该回复 guidance，且 `response.used` 与 `response.execution_linked` 指向第二次 execution。逐套运行／attempt／entry lock／批准 policy 与 config SHA 一致；四类原始 `trace`、`safety`、`sandbox_receipt`、`native_result` SHA 全部匹配，独立 Safety 均为 0 unsafe，原生 evaluator 均明确 `evaluated=true`、`native_success=false`，各次 Service 进程组均回收。原生失败不影响本工程关口。双套各只发生 1 次 provider 调用，未缓存，未超新冻结上限；`formal_eligible=false`。

`gate_c_audit.py` 从原始 JSON／SQLite 重建可见请求、投影 SHA、回复采用、token／求助预算、身份、原生判断、四产物及回收；从本 worktree 设置 `PYTHONPATH` 后以 Harness venv Python 执行该脚本并传入新证据目录即可重算。`gate_c_audit.json` **passed**，SHA-256 `d25aaa61f2a093d049a68a54b277e8e36702512a8f9692fef32b661f8c1be159`。逐文件 `raw_index.json` 覆盖 **100 个文件**，SHA-256 `5e813fc8fbdcd84ada6f9759d4889b026bfb9c96b6cef8df9da85232795d5fce`；新证据目录逐文件扫描未发现传入凭据字节。A／B／D 的首轮 95 项与双套测试回复闭环已先只读复核，旧 149 文件索引全部匹配；仅因本次 CLI deadline 参数从 90 改为 120 秒，补跑受影响的正式入口／正式链／M4 测试 **82 passed**，无受测代码或批准文件改动。

**结论：A／B／C／D 均达到本包工程验收条件，M4 Attention 决策工程模块关闭；`formal_eligible=false`。** 首轮在线失败仍是历史失败，测试回复仍仅是工程测试证据。另一个 Universe 工作树 `/home/truares/桌面/Tidybot-Universe-attention-native` 仍在 `7c6890f`；本 `attention-m4` 分支尚未整合，未合并。M3 depth 500 根因风险继续保留；本结论不准入正式效果矩阵、held-out、真人，也不表示已启动 M5／M6。

整合追记（2026-09-28）：上段“尚未整合”描述的是 M4 关闭当时的状态。现已在 `feature/attention-native-robosuite` 以双亲提交 `0e29a7d` 合并 `attention-m4`；M5 关口另见 [`m5_memory_acceptance.md`](m5_memory_acceptance.md)，不改写本 M4 原始结论与预算。
