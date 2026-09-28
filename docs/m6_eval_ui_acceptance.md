# M6 Eval 诊断与 UI 展示／操作：冻结工程验收包

冻结时间：2026-09-29，执行测试和新工程运行之前。最终状态：**M6 封闭工程验收完成**（v4 复验后；下文各阶段失败结论保留为历史记录）。本包的通过条件固定；运行中发现的包内缺陷继续修，外部阻塞单列，下一阶段需求不追加到本包。

## 基线与边界

- Universe：`feature/attention-native-robosuite` @ `d7e6bbe`，冻结前干净。M1、M2、M4 按各自封闭工程口径关闭；M3、M5 按已记录的修订工程口径关闭。M3 depth 500 根因和 RoboCasa 正向 Memory 效果仍为独立待办。
- 独立依赖已核对且工作树干净：Memory `24d4146`，Robosuite Service `081cd57`，RoboCasa Service `320020a`，Agent Server `4cf4daa`，RoboCasa task source `b18bbf1`。本轮如须改动独立依赖，先记录原因、新版本和受影响条款，不扩大门槛。
- 范围：已批准的 M1/M2 配置和策略 → 正式 Runner → Eval 对正式产物验身份、SHA、原生判定、独立 Safety 并诊断 → UI 持续展示与操作。覆盖 RoboCasa `counter_to_sink` 和 Robosuite `cube_lift`，仅开发 seed 101，`sim_gt`，`formal_eligible=false`。
- 排除：真人演练、LIBERO、真实机器人、RoboCasa 正向 Memory 效果研究、held-out、七策略正式效果矩阵、全链正式实验准入、硬件急停。原生策略失败不是工程失败；其状态和证据必须准确。

## 固定输入、模型、预算与证据

- 两套正常主路径使用仓库 `protocol/v2/dev_ui_run_catalog_2026-09-27.json` 中已批准的 seed 101 策略与配置 SHA，`autonomous`、最多 1 attempt、0 求助 credit、0 Advisor tokens；启动前再次核对该 catalog 的绝对路径与精确 SHA。Graph 路径只复用已批准的 M2 正式来源或同 SHA 的私有副本，不进行新的 Dev 生成或审批代签。
- 诊断使用 `parcc/GLM`、单次 provider attempt、客户端 75 秒、最多 2048 输出 tokens、temperature 0、low reasoning；输入投影不超过 12,000 字符，输出不超过 4,000 字符，须引用存在的 attempt ID 与该 attempt 的事件 ID。诊断超时用定向故障注入验证，不依赖外部服务恰好超时。
- 单 suite 正常真实 Service smoke 最多 1 attempt；中断 smoke 每套最多 1 attempt。每套外部墙钟上限 360 秒。独立故障用例使用 fixture/受控注入，避免重复启动真实物理任务。UI 画面只展示公开 RGB；原始证据只保留在受控产物目录。
- 中断从 UI HTTP 请求成功写入到执行确认不超过 **30 秒**，到最终 Service 停止／回收收据不超过 **60 秒**；必须保留请求时间、确认时间、两段时延、cancel/stop 原因及 Service 回收原件。不能仅凭按钮回显判通过。
- 证据根：`/home/truares/桌面/attentionbench-m6-20260929/`。保存冻结版本与 SHA、命令、stdout/stderr、批准配置、正式 Runner 四类产物、SQLite、Eval 诊断或失败收据、UI HTTP/画面快照、Graph 重启记录、中断时间线和逐文件 SHA 索引。源码验收结论回填本文件。

### 冻结输入纠错记录（首次真实运行后、重跑前）

首个 Robosuite UI smoke 因旧 catalog 的 `service_revision=12bc69` 与本包已冻结的独立 Service `081cd57` 不符，在正式边界失败；原日志保留于证据根 `robosuite/normal/launch-509130df8c0f4140a98519b3ac2881f3.log`。这是基线配置错误，不能视为策略失败或验收通过。后续双套运行使用同 seed、task、策略、预算的私有版本锁定 catalog `m6_current_service_catalog.json`：Robosuite config SHA `31ff175cfea8fb7e07097bb78f8293c457b62270ebc5143f3719b3a10adae98a`（仅将 Service revision 改为 `081cd57`），RoboCasa config SHA `fd32f0dfde74c6a754edf8e0b4ba78f94f733762e226bec2af177341edb2cc27`（复用 M5 已核对的当前独立 Service 配置）。这只修正冻结输入版本，不改变 E1–F1 门槛或增加实验范围。

中断用例在启动前另锁定同 seed／task／Service config 的专用长运行测试策略，私有 catalog `m6_interrupt_catalog.json`：Robosuite `formal_robosuite_long_action.py` SHA `89ba8edd68ff710483d5759abc8c6939156c62d373ee8848e94a0e67ecf5daa5`；RoboCasa `robocasa_spin_timeout.py` SHA `b35d7a83d9e68d4019dde1472e18d8009295efd9ced5b5ff19818813b923cdc5`。它们仅用于在运行中保留足够的中断窗口，不用于策略效果测量。

首次 Robosuite 中断用例尚未发出 UI 请求就因历史 depth 异常被独立 Safety 停止；原始异常帧与 SHA 留在 `robosuite/interrupt/attention-robosuite-cube_lift-seed101-a270aa7c341e/`，归属 M3 旧风险，不当成中断时效通过。为测 M6 中断契约，后续 Robosuite 中断用例改用同一已锁定的无动作长运行策略 `robocasa_spin_timeout.py` SHA `b35d7a83d9e68d4019dde1472e18d8009295efd9ced5b5ff19818813b923cdc5`；只改变故障注入输入，不放宽中断阈值或 Service 收据条件。

## 已知缺口与固定通过条件

| ID | 冻结前已知缺口 | 通过条件 |
| --- | --- | --- |
| E1 | Eval 目前截取前三个 attempt；run、attempt、suite/task/seed、批准配置和跨产物身份/状态以及回收语义需补强 | 全部 attempt 均被校验；任一身份、SHA、原生结果、独立 Safety 或 Service 回收篡改会拒绝诊断，不能被模型文字覆盖 |
| E2 | 诊断只有文本与可选文件；引用没有约束到同一 attempt，超时缺持久失败收据 | 正常诊断引用实际同一 attempt/事件；超时、空文、错引用留明确有界状态和收据，原生结果不变 |
| U1 | UI 持久视图已有 run/预算/请求/公开画面/Trace，但无可靠 Eval 和最终原生结果展示 | 运行中及终态 HTTP/页面持续显示对应身份、预算、请求、公开画面、可见 Trace、诊断状态/文本及最终原生结果；UI 和 Graph 重启后从持久证据恢复 |
| U2 | UI 媒体路径已有授权过滤，尚缺 M6 负向闭环 | 未授权原始证据、路径逃逸和未列入公开投影的内容不可从 UI 读取；UI 请求不能修改 Runner 原生判断 |
| I1 | 中断已有持久请求、协作检查及 stop，但未有双真实 Service 的端到端时效和回收收据 | 每套真实 Service 运行中 UI 请求、执行确认、时效及 Service 回收均满足冻结阈值；状态与 run/attempt 精确绑定 |
| F1 | 双套完整主路径及故障矩阵无同版本证据 | 定向测试覆盖正常结束、执行失败、诊断超时、UI/Graph 重启恢复、中断、跨层身份错配；RoboCasa、Robosuite 各有真实 Service 开发 smoke，并按逐文件 SHA 审计 |

全部 E1/E2/U1/U2/I1/F1 在指定验证级别通过，才将本包、`STATUS.md`、`docs/attentionbench_progress.md` 标为 M6 工程完成并提交当前功能分支。任一未过则列出实际证据和未过项，M6 保持开放，不用旧 smoke 代替本轮结论。

## 本轮执行记录：M6 保持开放

证据根的 `m6_audit.json` SHA-256 为 `cc54932e730ce3d4ff54de2f5d7fe16047d0bd55048c35f6a66124f24c30814b`；`sha256-index.json` 覆盖 124 件文件，SHA-256 为 `3d1fea721f9fdeac3b98bf0e7da6fb0c982e40023a750c2605a68ba2293b92be`。Harness 定向／全套 **391 passed、10 skipped**；Graph pytest（排除自带 `main()`、非 pytest 用例的 `test_orchestrator_pipeline.py`）**46 passed**，该脚本独立运行 **84 passed、0 failed**；`git diff --check` 通过。尚未提交。

- **E1/E2 已覆盖部分通过**：两套正常真实 Service seed 101 各 1 attempt，正式 Runner 四类产物 SHA、run／attempt／配置身份、独立 Safety 和完整 Service 回收经当前 Eval 代码复核。`parcc/GLM` 各 1 次真实调用、分别 1247／1263 tokens，诊断均引用对应 attempt 与事件，原生结果均为 `false`，`formal_eligible=false`。故障注入测试覆盖第 4 attempt、回收篡改、诊断超时收据和结果不可改写；UI 重启后两套都重现诊断与原生结果。先前无凭据的失败收据单独保留。
- **U2 定向通过**：HTTP/投影测试拒绝未授权 oracle 证据、路径外引用和 SHA 篡改；结果投影只从验证过的 Runner/Eval 收据取原生判断，诊断收据篡改被拒。
- **U1 未过**：两套真实 UI smoke 未配置实际动态 Service 相机端点，`m6_audit.json` 的 `ui_public_camera_configured=false`；公开画面只有既有 HTTP/媒体定向测试，缺与这两次真实 run 同链连续展示收据。UI 终态重启已测，Graph 在 M6 同链运行后的重启恢复尚无新证据。
- **I1 未过**：Robosuite 长动作在请求前触发历史 depth 异常和独立 Safety 停止；空转策略在请求前沙箱以 137 退出。RoboCasa UI 请求已持久化，但在 Service 启动期被旧实现标为 `startup_failed`，正式边界拒绝，留下 `requested` 而无执行确认或阈值收据。启动期取消已按该包修复并做定向测试，但冻结预算内没有修复后的双真实 Service 复验，故不能宣称中断通过。
- **F1 未过**：正常结束（原生失败）和诊断故障等定向分支有证据；双套运行中中断、同链 Graph 重启、真实公开画面矩阵仍缺。首次 Robosuite 旧 catalog Service revision 错配失败和 RoboCasa 首次 Eval 对双进程回收结构误判均保留原件，已修复／纠正，不删除失败历史。

因此本轮**不是 M6 工程验收完成**。不修改 M1–M5 的原结论，不更新 `STATUS.md`／模块进度页的完成标记，不提交当前功能分支；后续如重试真实 Service 中断，需要先另行明确新的运行预算和精确用例，不能把本轮超预算的尝试倒记为冻结包通过。

## 修复复验批次 v2（2026-09-29，新增真实运行前锁定）

原冻结条件 E1/E2/U1/U2/I1/F1、seed 101、双 Service 版本、诊断模型与预算、中断 30/60 秒阈值均不改；上一批失败证据继续计入审计。此批只复验上一批未过的同链公开画面、Graph 重启及双 Service 运行中中断。每套最多一次新的真实 Service 开发中断 smoke、每次最多 1 attempt、墙钟最多 360 秒、`formal_eligible=false`；不会加入新的效果实验。

- 新证据根：`/home/truares/桌面/attentionbench-m6-20260929/repair-v2/`；两套独立子目录保存 catalog、UI HTTP 快照、相机响应、正式 Runner、Eval、SQLite、中断请求/确认/回收收据和 SHA 索引。
- 专用策略 `sensor_loop_interrupt.py` 仅重复公开 `sensors.find_objects()` 195 次以维持协作取消窗口，不发出动作、不用于原生效果判断。SHA-256：`4168cfadc0f1c40f6c98fb761cf8f2604fff9953d88c29c4d7672e65bd5c1b3b`。私有 catalog `m6_interrupt_catalog.json` SHA-256：`796e8247dbe822db14b382317e972a4cc8c816dbb926501158bb8a7bfbaf5213`，只将中断用例的策略换成该受控策略；配置 SHA 与上一批一致。
- UI smoke 脚本 `ui_interrupt_camera_smoke.py` SHA-256：`2d18d3a6bcd95210238a1f9dd7015748edf5af1a3dcd2474ce7acd7d56479ddb`；同 run 画面在 UI 重启前后各读一次，再经 HTTP 发出中断。
- 真实运行前先过定向测试。若某套在 UI 请求前自然结束或先被 Safety 停止，记录为未过，不自动追加第三次运行。Graph 恢复用上一批已存在且经 SHA 校验的正式 artifact 做进程重启验证，不新启 Service。

### v2 结果与单项修复复验 v3（新 Robosuite 运行前锁定）

v2 RoboCasa 同 run 公开 JPEG 在 UI 重启前后分别取得 SHA `68fbcef271e1ab87476d0235e842b1cf33177fb9374e3081350379b111041710` 和 `5650468e3db0fe446b637a3ea63220ea7af74d4b3ea4ac2e8fd27f76d93d947f`；UI 请求在运行中发出，2.53 秒确认，最终取消，Simulator 与 Agent 进程均回收。v2 Robosuite 同 run 公开 PNG 在 UI 重启前后均取得 SHA `da724598c4c1df0435ea49b778ede2d973bf05239ad2a0877644473c4b3dc495`，但测试策略约 2 秒自然结束，UI 重启后请求前已失败，因此 **I1 仍未通过**。两者的原始记录分别在 `repair-v2/robocasa/` 与 `repair-v2/robosuite/`，失败不从结论中剔除。Graph 新导入进程分别对两套上一批正式 artifact 执行恢复，没有二次模型调用；证据 `repair-v2/graph_recovery_smoke.json` SHA `88db687370cc55d116c8b85b76a9980f1ac7647297a2bdd1c69c5782ff161ab5`。

v3 仅给 Robosuite 一次额外真实 Service 开发中断 attempt，仍用 v2 catalog／策略、seed 101、配置、原门槛和 360 秒墙钟上限，`formal_eligible=false`。修复的是脚本时序：同 run 公开画面读出后立即发 UI 中断请求，再重启 UI 检查持久状态与缓存公开画面。新脚本 `repair-v3/ui_interrupt_camera_smoke.py` SHA `070eb1634310a36a05f61e45681025ea5409cca1beb9cc0138c4a5de44cceeef`；新证据目录 `repair-v3/robosuite/`。若此 attempt 仍在请求前结束，记录未过而不继续增加真实运行。

### v3 当时复核：M6 保持开放

v3 Robosuite HTTP 请求在 run `run:attention-robosuite-cube_lift-seed101-35324264cca3` 运行状态下持久化，UI 重启前后同 run 公开 PNG SHA 一致，旧中断状态随后显示 `stopped` 且有 0.13 秒确认时间。然而正式 Runner 原件记录 **attempt `attempt:attention-robosuite-cube_lift-seed101-35324264cca3:0` 为 `completed`、195 次 SDK 调用 0.064 秒完成、Service 停止原因为 `normal_cleanup`**。这证明请求落在正式执行完成后，旧 `stopped` 是假阳性；**I1 不通过**。RoboCasa v2 的正式 attempt 为 `cancelled`、Service 原因为 `operator_cancel`、请求至确认 2.53 秒且双进程回收，因此仅 RoboCasa 一侧通过。两侧均低于 30/60 秒阈值的数字不能代替 Robosuite 缺失的执行取消。

已修复假阳性：后续这类竞态记为 `too_late`，保留正常回收原因、不写执行确认时延，run 不标为已取消；Eval 对历史上“run 宣称 `emergency_interrupt`、正式 attempt 却完成”的不一致产物拒绝诊断。v3 原始产物不改写，当前 Eval 对它返回 `ValueError: Eval run interrupt differs from formal attempt and Service stop`。同链 RoboCasa 取消产物以及两套正常产物仍通过 Eval 校验。动态公开画面、UI 重启和既有正式产物 Graph 重启恢复已补证；未重新调用诊断模型。

门槛结论：**E1/E2/U1/U2 通过；I1 未过，F1 随 I1 未过**。所有工程运行 `formal_eligible=false`，策略原生失败均按失败状态保留，未把它们当作工程失败。证据摘要 `repair-v3/m6_audit_final.json` SHA-256 `a18aab63fb545aca322c814b99f1232d23ae5c8875bd94ecf02fe1ad318d6c1a`。保持 M6 开放，不更新 `STATUS.md`／模块进度页完成标记，不提交功能分支；仍需在新的明确预算下取得 Robosuite **formal attempt `cancelled` + `operator_cancel` + Service 回收**的同链收据。

最终回归：Harness **394 passed、10 skipped**；Graph **46 passed**；独立 Orchestrator pipeline **84 passed、0 failed**；`git diff --check` 通过。证据根的最终 SHA 索引 `sha256-index-final.json` 共 223 件，索引自身 SHA-256 `e135e81b474556d39ffe069a982615b47c05ee33a64e5a8123c3dcf8b7102836`；源码状态快照 `repair-v3/source_state.json` SHA-256 `0fe877671f1180764ee515f6f1ae2c8f0dc305af0a6bee7abedc3547b6d38792`。

## 修复复验批次 v4（2026-09-29，新增真实运行前锁定）

上一节的“停止真实重试”是 v3 批次上限，v3 结果仍为失败，不追认。为核验唯一未过的 I1，本批明确增加 **一次** Robosuite 真实 Service 开发 attempt，不新增或放宽任何 E1–F1 门槛。仍是 `cube_lift`、seed 101、`sim_gt`、已锁定的当前 Robosuite Service `081cd57` 与配置 SHA `31ff175cfea8fb7e07097bb78f8293c457b62270ebc5143f3719b3a10adae98a`、最多 1 attempt、120 秒 attempt deadline、360 秒外部墙钟、`formal_eligible=false`。RoboCasa 不重跑；所有旧失败和原件继续保留。

专用策略 `repair-v4/bounded_cpu_interrupt.py` SHA-256 `d35a9f93258849a903ecbe4be1300442b822707cb66574f1b4624be8aa6c4dfc`，只执行固定上界的 Python 循环，不读隐藏状态、不发动作或 SDK RPC；仅为正式策略执行期间提供可观测中断窗口，不用于效果测量。含该策略精确 SHA 的私有 catalog `repair-v4/m6_interrupt_catalog.json` SHA-256 `ea11611c0114536bf3c062e569c88617d380f890825b46d050cb61324131dbc7`。定向沙箱探针 `repair-v4/policy_probe.json` SHA-256 `98964835c9550a5da0d72002afe6b2fb857f12d655ef0cf9021241b1a60c5b7c`，0.30 秒返回 `cancelled`，证明此输入可被现有协作取消机制停止。

真实脚本 `repair-v4/robosuite_active_interrupt_smoke.py` SHA-256 `316930499ad2e4441c1fa08c8ebd3a33a55b832911404b52ced3312638f4f90f`。它等到正式沙箱的 `policy.stderr` 创建，延迟 0.25 秒确认执行窗口，再经 UI HTTP 发请求；终态核验同一 run/attempt、正式 Runner 与 worker 均 `cancelled`、Service `operator_cancel` 和进程组回收、请求至确认 ≤30 秒／至回收 ≤60 秒、公开 RGB 与 Eval 状态。证据目录 `repair-v4/robosuite/`。任何条件未过则保持 M6 开放，不自动增加运行。

## v4 最终工程验收：E1/E2/U1/U2/I1/F1 全部通过

- **E1/E2**：两套正常 seed 101 真实 Service 各 1 正式 attempt，当前 Eval 重验身份、配置、四类产物 SHA、原生失败与独立 Safety／Service 回收；`parcc/GLM` 各一次真实诊断分别引用同一 attempt 的 `sdk-3`／`sdk-0` 等实际事件。输出 1247／1263 tokens，均未修改 `native_success=false`。超时／错引用／篡改由定向故障测试和持久失败收据覆盖。
- **U1/U2**：同 run 公开 RGB 已在双真实 Service 运行中及 UI 重启后取得，Graph 对双正常正式产物在新导入进程中复用持久诊断，不重新调用 provider；双正常 run 的新 UI HTTP 进程从持久证据显示诊断、原生结果、预算、attempt 和 Trace。未经授权原始证据、路径逃逸、SHA 篡改和 UI 改写原生结果的负向测试通过。
- **I1**：RoboCasa `attempt:attention-robocasa-counter_to_sink-seed101-2130858cf7f5:0` 与 Robosuite `attempt:attention-robosuite-cube_lift-seed101-6521882f7846:0` 均由 UI 持久请求进入正式 `cancelled`；两套 Service 均以 `operator_cancel` 停止且全部进程回收。请求至确认／回收上界分别为 **2.53 秒**、**0.20 秒**，满足 30／60 秒阈值。Robosuite 正式 worker 同为 `cancelled`，无动作的专用策略不用于效果判断。v3 假阳性原件仍保留且被当前 Eval 拒绝。
- **F1**：正常结束、执行失败、诊断超时、UI／Graph 重启、身份和哈希错配、原始证据拒绝、运行中中断均已按指定验证级别覆盖；定向故障矩阵 **11 passed**，Harness **394 passed、10 skipped**，Graph **46 passed**，Orchestrator pipeline **84 passed、0 failed**，`git diff --check` 通过。

逐项自动复核 `repair-v4/m6_closure_audit.json` SHA-256 `fb60f98c585714a0079062c100da21c8bc0950152f54c524572f51ba3a5521bb`；它读取真实正式产物、Eval、UI、Graph、中断和测试日志并断言六门槛，同时核对独立 Service 版本及干净状态。所有运行 `formal_eligible=false`。此结论只关闭 M6 工程模块，不声称策略效果、全链正式实验准入、RoboCasa 正向 Memory、held-out、真人、LIBERO 或真实机器人完成。

最终证据索引 `sha256-index-m6-closure.json` 覆盖 259 件文件，逐件重验通过；索引自身 SHA-256 `905e2b6af9ce6a520408a3062015e8ccb55beb1ce27c198bda91567ad03666a4`。先前 124／223 件阶段索引及全部失败运行保持原样。
