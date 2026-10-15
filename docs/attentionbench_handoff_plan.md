<!-- d-ui-acceptance-20261016-0310 -->
## D 前端进度补记（2026-10-16）

`outputs/handoff-execution-20261016/D/RESULT.md` 是 D 冻结包的本轮核验结论：真实本地浏览器对 disposable fixture 完成合成 Ignore/Defer、Advisor response/Memory lifecycle 持久化；两份 viewport 图请求1280×900与390×844 CSS尺寸，实际1265×889与375×812，哈希吻合，仅为synthetic rolled_back Memory布局证据。全量 `test_live_ui.cjs` 两边均279项且测试SHA一致，current 173 pass/105 fail/1 cancelled，baseline 189/89/1；失败集合19 current-only、3 baseline-only，`DECISION-06`两边均cancelled。该比较已完成但并非回归通过；current-only请求操作断言与旧fixture缺少当前 renderer 要求的服务端 `permitted_actions` 相符，未改测试。没有真人回复或Memory有效性证据。D仍部分完成且明确阻塞；详见`outputs/handoff-execution-20261016/D/live-ui-failure-comparison-20261015T191736459569Z/REPORT.md`。
<!-- /d-ui-acceptance-20261016-0310 -->

<!-- c-host-acceptance-20261016-0304 -->
## C host 验收完成：8769 已条件式切至主工作树（2026-10-16 03:04 +08:00）

本次仅 C host 启动兼容、临时端口与条件式切换；仓库仍 `attentionbench/baseline-20261015@aef9ca86af7663ffcf2bb23e4d3057f37944daa1`，原有 dirty 源保留。以下此前“host待验/未重启”条目是历史，本段为本次 C host 最新证据；不改变其他板块、M1–M6或正式资格。

- **源与临时服务通过**：643个代码/测试/UI文件与 `C/bridge-compat/checks-metadata.json` 完全匹配；旧12+331 Python（1 deselected）、36+14+6 Node成绩只按原源绑定引用，未把旧28 socket结果转签。新临时主代码 `127.0.0.1:18769` / PID215134于03:00:22–03:00:23实际运行，25项GET通过、exit0且端口释放。按HANDOFF exact argv仅改port/cwd/PYTHONPATH，复用真实路径；先只读Online Backup23库，临时前后155表schema/行完全一致。
- **新空闲守卫与8769切换通过**：03:01:42重新核验原PID905496/candidate-eval-agent的真实argv/cwd与94项完整环境（仅内存，不落盘凭据）；10条run全终态，62条launch PID/PGID均不存在，26 sessions含1 planned、117 jobs仅113 settled/4 needs_review、无活动owner/worker。03:01:42.715向原UI发SIGINT，消失后原argv重启为主工作树 **PID220418**；仅cwd/PYTHONPATH变化，其他应用参数/环境逐项相同。旧UI非本执行器子进程，exit code不可wait，已如实记录；新UI持续服务中。
- **API与账本通过**：新服务25项GET：15×200、5×206（原回放及4路视角Range）、5×404（预期terminal_saved/no_frame）；覆盖 `/ui/`、`/api/run-options`、Dev options/experiments、runs、既有snapshot/cell view/live/多视角/录像、planned只读view。dev_v2保持enabled、2 profiles、7 policies、per_run_budget；额外 `/ui/live.js` 200且SHA与主树一致。紧邻pre/post **23库/155表/1335行** 的schema、行数、逐行SHA列表/逐表row hash完全一致；planned实验/cell原始payload、API session和该SQLite文件字节SHA相同，goal/预算/用量原样保留。
- **保全与边界**：旧candidate目录4265个manifest范围文件、1600个既有C证据文件、643源码文件及原脚本/catalog未改。切换成功，**回滚未触发**，旧目录保留可回滚。两个验收helper的字段/pycache范围错误及原失败证据均保留并在RESULT解释，不是部署失败；未改业务源或历史manifest。未POST、审批、启动planned/新实验/模型/模拟器，未操作8082/8772/8775、Git refs/commit/push。

证据入口：`outputs/handoff-execution-20261016/C/host-acceptance-20261015T185736Z/RESULT.md`；实际进程/退出与切换 `attempt2/{temporary-lifecycle.json,switch-result.json}`，新守卫 `immediate-pre-switch/`、`post-switch/`，统一算法及155表对照 `attempt2/{switch_common.py,database-comparison.json}`，原API/临时/正式响应与保全记录同目录。**C本次host验收完成，整个handoff goal仍未完成**；下一步交回主聊天按既定验收包处理后续板块，本执行不进入D–H，不宣称真实任务/正式实验验收。
<!-- /c-host-acceptance-20261016-0304 -->

<!-- handoff-A-independent-20261016-final -->
## A 第 3 步独立全量复验：验收完成（工程回归，2026-10-16）

本轮两仓库仍为 `attentionbench/baseline-20261015`：主 `aef9ca86af7663ffcf2bb23e4d3057f37944daa1`、Memory `15968a37fb55b0f7ad323dcd265729dfa13621ab`。实际接手时已有后续 B/C 等未提交代码与新增测试，全部保留；不把当前 dirty 源称为原提交。用户指定的两条原命令已在原工作目录完整结束：harness **1387 passed / 56 failed / 10 skipped，364.47s，exit 1**；失败 node ID 与固定 56 项基线精确相同，**新增 `[]` / 基线不再失败 `[]`**。Memory collect-only **77 项 / exit 0**，全量 **77 passed / 56.73s / exit 0**，与历史 77 项口径无差异。当前 harness 1453 项比历史 1375 多 78 项，均是已有后续新增测试，无原测试移除。

另以原提交的干净临时 worktree 独立复验：普通 `/tmp` 路径首轮 **82 failed / 1283 passed / 10 skipped**，额外 26 项是本地产物/绝对路径依赖与一次 0.5s 墙钟测试波动；原日志保留。随后只在测试子进程挂载命名空间中保留原路径，原 `openarm_phase*`、launch_configs、outputs **只读挂入**，源码仍取临时 worktree；26 项全部通过，再完整复跑得到 **1309 passed / 原 56 failed / 10 skipped，330.33s，exit 1**，两向 node ID 差集均 `[]`。6183 个 tracked 文件测试前匹配原提交、测试后无漂移；引用该 worktree 的 Memory 全量亦 **77 passed / 52.89s / exit 0**。未改源码、断言、历史锁或失败基线。

证据：`outputs/handoff-A-independent-20261015T183243Z/RESULT.md`、`comparison.json`、两套完整失败 node ID 清单、完整日志/命令/退出码、`preservation.json`；固定基线 SHA256 仍 `f48c51fad1ccb35678b1cbfc780936841a967042669548e462fcfbe15cb195f9`。本轮 tracked 修改仅此三份状态文档的新增说明；旧文档字节与历史证据保留，暂存区/两个 HEAD 不变，未 commit/amend/push。临时 worktree `/tmp/attentionbench-A-step3-20261015T183243Z` 保留供复查。

本结论只关闭 A 的独立工程回归，不代表真实运行服务、机器人/模拟器或正式实验验收；未操作 8769、凭据、远端、付费模型、审批、M1–M6 或 formal_eligible，也未执行 B–H。原沙箱 EPERM/中断说明保留为历史；其他板块原有阻塞不因本轮解除。下一步将 A 的完整证据交回主聊天，后续范围沿用其原验收包。
<!-- /handoff-A-independent-20261016-final -->

<!-- c-bridge-compat-20261016 -->
## C 启动兼容代码补缺已验证；总验收部分完成且被明确阻塞（2026-10-16）

主仓库仍 `attentionbench/baseline-20261015@aef9ca86af7663ffcf2bb23e4d3057f37944daa1`，Memory 仍 `15968a37fb55b0f7ad323dcd265729dfa13621ab`。本轮仅 C，不启动 E/F/G/H、模型/模拟器或浏览器任务，不提交/推送，不写 Git refs，保留原工作树与候选目录。

- **代码和无 socket 回归通过**：`ui_server.py` 接受并初始化原服务的 `--dev-v2-root` / `--dev-v2-per-run-budget` 及可选 authorization，保留显式共享 goal bridge/独立 human 审批；两路配置互斥。Daily POST 兼容通用 bridge 参数。实测又发现首个实验的 materialization CLI 无 experiment 可推断独立 goal，已按 selection 的原有限额显式绑定，避免误读/创建默认 GOAL。preview、Qwen→GLM review 和多视角保留，预算/用量/时钟/截止不重置。
- **实际证据**：`outputs/handoff-execution-20261016/C/bridge-compat/`：新增测试 **12 passed / exit0**，Dev/审批/恢复/旧goal/evaluator/多视角组合 **331 passed / 1 deselected / exit0**（仅原有 Service-manifest CLI epoch 外部依赖未跑）。Node workspace **36**、evaluator **14**、Attention **6**，语法检查均 exit0。`checks-metadata.json` 给出精确 argv/env/exit 与源码前后 SHA，`source_unchanged=true`。是临时 SQLite + 内存 HTTP 合同及离线 create 子进程，无真实监听/API/模型/模拟器验收；不将 task-03 的529或调用者旧28 socket成绩转签给本轮新源。
- **host 已可见，尚待新源临时端口验证及切换**：调用者 `C/host-pre-switch.json` 已核实 PID905496/candidate-eval-agent、原 argv/cwd/selected env、10终态run与62条launch的pid/pgid均无活动worker；保留1条planned/owner null/jobs空/used=0，不启动或修改。8082模拟服务及8772/8775独立UI勿动。host原socket回归28passed/exit0；主树原argv probe exit2是本轮修复输入，原失败日志保留。
- **C 尚未全项通过**：本轮business未操作真实8769。调用者须先在host临时端口以除port外同一参数组合（cwd/PYTHONPATH指主树、其余原环境保留）成功启动并读原API，验证新源实际transport；再按已有授权重新核对紧邻切换空闲/worker、逐表行哈希和回滚守卫，才能有条件切换。旧22表哈希和4265文件清单原字节保留，不刷新/补签。步骤和调用者原事实见 `C/bridge-compat/HOST_HANDOFF.md`。

`C/bridge-compat/preservation.json` 核对旧候选4265文件、host证据和历史基线无变化；可审增量 `bridge.delta.patch`，累计 `task.patch` / `task-source-manifest.json` 延续task-03原基准（三份dirty文档用before-*.md、tracked源码用HEAD、新文件用空基线）。C整体仍为**部分完成且被明确阻塞**，整个goal未完成；D真实浏览器交互、M1–M6和正式准入状态不因本轮代码通过而改变。
<!-- /c-bridge-compat-20261016 -->

# AttentionBench 剩余工作交接计划（2026-10-16）

### B1：Run 崩溃回收工程测试通过
新增 `run_recovery.py`，UI 启动和列表触发原子清理；新 launch 记录 PID namespace/boot 身份，仅本命名空间中确认死亡才回收，旧/跨 namespace/权限未知不猜测。pending/fallback 取消、未消费 credit 退还、资源释放、等待结束、run/launch scheduler_lost 终态在同一事务中提交。`B1-final.log`：14 passed / exit 0（含既有 store 8 项）；重启幂等、存活/未知不动、已消费 credit 保留和失败回滚通过。历史 launch 缺少 namespace 时保持原状态，真实宿主崩溃恢复未核实。固定范围见 `outputs/handoff-execution-20261016/B-acceptance.md`。下一项 B2 缓存只读重放。

本文件交给下一位执行者（GPT/Codex）。目标是把 `timeline.md` 的 “System components to add” 做完整。**先读 `AGENTS.md` 和 `STATUS.md`**，其中的“单板块封闭验收”“不得把工程通过说成正式实验验收”等规则继续有效。

**2026-10-16 恢复执行更新：** 用户已明确授权本地代码/测试/服务、开发集仿真与模型、8769 条件式重启及专用新分支 push；原文保存于 `outputs/handoff-resume-20261016/AUTHORIZATION.md`，该授权取代下文旧的“尚待授权”说明，但不解除实际沙箱限制，不代写具体策略审批或正式准入，不授权默认分支 push、PR、发布或提前 held-out。执行顺序 A→H 与 I 边界不变。A 已经由 unrestricted 完整复验闭合，详见 `outputs/handoff-execution-20261016/A-audit.json`；此前只读 Git 和 EPERM 报告保留为历史，继续 B。


### B2：Advisor 只读缓存重放工程测试通过
`SimGTAdvisorProxy(replay_only=True)` 对共享缓存用 SQLite mode=ro，未命中直接 `advisor_cache_miss`，不调用 provider、不创建缓存/目录；本地缓存同样只读。两 CLI `--advisor-replay-only`，formal scheduler_config 和 run scheduler_options/summary 均留账，重放不初始化默认认证 client。`outputs/handoff-execution-20261016/B2.log`：13 passed / exit 0（含既有 Advisor 回归）。下一项 B3 消融开关。

### B3：Memory / trace-evidence 消融工程测试通过
`memory_enabled` / `trace_evidence` 默认 true，两个 CLI 提供 `--no-memory-enabled` / `--no-trace-evidence`，进入 scheduler_config、scheduler_options 和 summary。Memory off 覆盖两个 native binding、planner 选择与 formal executor；trace off 不执行 assess_trace，规划器仅基于失败摘要/次数，Advisor 用字段白名单移除事件/证据/assessment/图像（包括历史包），独立安全监控与审批保持。`outputs/handoff-execution-20261016/B3.log`：19 passed / exit 0，实际包无对应内容及 formal executor 门槛通过。

### B4：随机次数匹配工具工程测试通过
`random_matching.py` 函数与 CLI 从同 suite/task/seed 完整规划器的终态 run 派生，交叉核对原 summary 和只读数据库请求集合，排除 interrupt；输出 policy_config.json、精确字节 SHA 与 match.json 来源账本，拒绝错误 cell、非终态、遗漏请求及覆盖已有产物目录。`outputs/handoff-execution-20261016/B4.log`：6 passed / exit 0。匹配请求目标，不保证提前成功/资源终止后的实际请求数；没有执行实验。

### B5：请求生命周期时间线工程测试通过；B 定向组合通过
新增 `request_lifecycle.py`，只读 CLI 和快照 `request_timeline` 共用持久事件，记录七类时刻、回复延迟、采用回复、execution/candidate 链；新状态转换持久化时刻，历史缺钟明确 null，不回填旧事件。`B5.log` 18 passed / exit 0。五项组合 `B-combined-final.log` **116 passed / 1 deselected / exit 0**（仅排除需 socket 的 endpoint）；首轮 2 failed / 114 passed 保留，已修复默认 scheduler_options 的兼容形状，以及新增重放测试误用了两份不同源码导致的合法 miss；未改任何既有测试断言或基线。新开关全量写 formal scheduler_config / run summary，非默认值才扩展原 scheduler_options。B1–B5 实现与定向工程包通过；HTTP endpoint、完整 unrestricted B 全量和真实服务未验收，不沿用 A 的全量成绩冒充 B。

开始 C 静态分叉合并，冻结包 `outputs/handoff-execution-20261016/C/ACCEPTANCE.md`。当前 socket EPERM、仅可见隔离 PID，宿主空闲/worker/启动任务未核实，禁止重启 8769；继续静态三方分析和代码合并。`candidate-fontfix` 为未版本化快照，不能把目录存在视作 Git 公共祖先；差异表与原 operator 前像 merge 结果已存档。

### 本轮最终验收索引：部分完成且被明确阻塞

- **A 验收完成（工程回归）**：unrestricted 1309 passed / 固定 56 failed / 10 skipped，exit 1；node ID 差集新增0/修复0；Memory 77 passed / exit 0；12 源/测试字节绑定已核验，旧 EPERM 报告与基线不改。
- **B1–B5 已实现，定向工程验收通过**：崩溃回收（本 namespace 身份守卫、原子清理及自定义 UI 启动/列表接线）、缓存只读重放、Memory/trace 开关、同 cell 随机次数预注册、持久请求时间线都有新增测试。最终组合 **529 passed / 3 deselected / exit 0，296.84s**，源码运行前后完全一致，详 `outputs/handoff-execution-20261016/C/final/regression-metadata.json`。该组包含 Dev v2、旧审批/恢复、评审、B 全部新增和相关既有回归；不是完整 harness unrestricted 复跑，不把 A 的完整成绩转签给新源。
- **C 静态合并已实施；服务验收外部阻塞**：原 operator 前像11/11 SHA吻合；快照目录无独立 Git，不能证明 fontfix 为 Git 公共祖先。逐冲突保留主树共享 goal/审批/恢复、并入只读评审/缓存与多视角，legacy goal 用只读解析兼容，不迁移已有账本；缺少新 eval 额度按0，不能增加旧额度。候选旧目录未改。扩展测试历史失配/超时原件保留：独立 Memory 当前4个源码字节与历史 Service manifest 不符、历史 SQLite 缺失、HTTP/socket EPERM；不改旧 manifest 造通过。最终组合排除这些明确外部项，未宣称 C 整包通过。宿主 PID/net 不可见，run/launch/worker 空闲未核实，**8769 未重启**，无本轮切换前后表哈希可声称。
- **D 界面已实现；截图与真实持久链未验收**：请求 explanation/优先级/deadline、按服务端 permitted_actions 过滤操作、ignore、独立 disposition/时间线、Memory内容/版本/候选验证队列和有 actor/reason 的生命周期表单、reserved/GPU null/coverage、锁与自主阻塞、普通固定策略的独立 execution/assistance/request mode 及 GPU 预算。最终 Node **6+36+14=56** 个渲染合同及语法检查通过；529回归后仅 live.js 与其新增 cjs 测试变化，Python未变，精确差异见 `final-test-binding.json` 与 D 的 post-regression-ui-delta。正常启用 Chrome sandbox 的启动在 setsockopt EPERM/SIGTRAP 失败，真实截图0，未把无图渲染测试当D端到端验收。旧 test_live_ui 全套失败名差集未能在此浏览器环境取得，不声称无新增浏览器失败。
- **E 仅当前 M1 复核**：完整 `test_m1_entry.py` **32 passed / exit 0**（纠正最初26项集合过窄的分类，补齐6项受控 UI 启动测试）；真实双 Service 交接受 socket/宿主限制阻塞，未推进新 M2–M6。修正旧 v35 现状：已核对 v112 **50/50有效槽、170 attempts、原生成功0/50、comparison_bias_gate=false**；原包明确不要求最低 native 成功数，不新增门槛、不改历史签名。每套两任务仅入口已注册，不能视为均已实跑稳定。
- **F/G/H 未越过前置验收**：F 仅新源/审批/冻结准备审计，未伪造350锁、策略批准、execution_authorized 或 formal_eligible。G 预算预估及认证/账户未核实项已留账，真实调用/仿真/实验消费均0；主矩阵保守上限1,433,600 tokens / 350 credits / 1400 attempts / 105000s，未获当前冻结准入前不启动。真人要实际参与，held-out另行冻结授权。H只读审计独立旧checkout `benchmark-test@433bcefc` 的未跟踪LIBERO bridge、SDK/原生判定/reset/产物和seed分区差异，未在主 benchmark 稳定前迁移/运行。

精确命令/退出码/差集/源码 SHA/补丁/分支错误与逐项报告入口：`outputs/handoff-execution-20261016/RESULT.md`。主分支仍 `attentionbench/baseline-20261015@aef9ca86`；Memory `15968a37` 未修改。专用分支创建 exit128（linked Git refs 只读），没有 add/commit/push/PR。已有用户改动与旧证据保留。当前收尾是**部分完成且被明确阻塞**，不宣布整个 goal、真实服务或正式实验验收完成。

## 0. 当前状态（接手前必读）

仓库：主仓库 `Tidybot-Universe-attention-native`，分支 `attentionbench/baseline-20261015`；Memory 服务 `~/桌面/attention_memory_service`，同名分支。**均未 push。**

| 提交 | 内容 |
| --- | --- |
| 主 `60cb9c0`、`824ffd4` | 基线：固定当时未提交的代码、测试、文档（不含 `outputs/`、`openarm_phase*`、`openarm_bimanual/launch_configs`） |
| 主 `98db33a` | 请求机制 v2（设计见 `docs/attentionbench_request_mechanism.md`） |
| 主 `be7c2f0` | Memory 生命周期 v2（见 `docs/attentionbench_memory_lifecycle.md`） |
| Memory `d76c6ed`、`15968a3` | 基线；验证裁决、enable、目录/使用记录/历史接口 |
| 主 `aef9ca86` | UI 后端：资源预留、GPU 计量、执行目标选项、run `lock`、自主工作阻塞原因、`permitted_actions`、请求/Memory 快照字段（`gpu_meter.py`、`tests/test_resources_and_lock.py`） |

测试怎么跑（用现成 venv）：

```bash
cd ~/桌面/Tidybot-Universe-attention-native
PYTHONPATH=~/桌面/attention_memory_service:$PWD ~/.cache/tidybot-attention/venv/bin/python -m pytest -q -p no:cacheprovider benchmarks/attention_harness/tests
cd ~/桌面/attention_memory_service && PYTHONPATH=$PWD:~/桌面/Tidybot-Universe-attention-native ~/.cache/tidybot-attention/venv/bin/python -m pytest -q -p no:cacheprovider tests
```

回归标准：**基线有 56 个固定失败**（OpenArm、`test_agent_actions_v2`、`test_fixed_ui`、curobo 部署等，清单 `outputs/baseline-failures-20261015.txt`）。对比失败集合，**不新增失败**才算通过，不要比数量。最近一次完整结果（第 3 步提交时）：**1309 通过 / 56 失败（与基线完全相同）/ 10 跳过，无新增失败**；Memory 服务仓库 77 通过。

### 0.1 执行环境限制（第一次交接后补充）

第一次交接时，执行端沙箱**禁止创建 socket**（`EPERM`），导致所有 HTTP/ASGI/监听/预览类测试失败甚至挂起，所以那次报告里“41 个新增失败”和 Memory 服务测试挂起是**环境造成的，不是代码回归**。在不受限环境里的完整结果已存档：

- `outputs/step3-regression-20261016/`：harness 全量日志；失败集合 `harness-failures-after-step3.txt` 与基线 `harness-failures-baseline.txt` **逐字相同**（SHA256 前缀 `f48c51fa…`），`new-failures-vs-baseline.txt` 为空；Memory 服务 77 通过（`memory-service-run.log`）。
- **归档内容的回归比较通过，但不再将“有归档”直接等同于本轮 A 闭合。** 2026-10-16 恢复任务要求把成功运行绑定到 `aef9ca86` 与匹配的 Memory commit；六个归档文件没有原命令、退出码、commit/source manifest，旧 `handoff-A-step3-20261016/{scope.json,test-results.json}` 只绑定另一次中断的沙箱测试。若补齐可核验的原运行材料，可采用归档，无需重复完整测试；材料不全则按授权环境复跑。不得事后编写原运行的版本/退出码来补签。

历史沙箱处理说明（A 已由新的完整证据闭合；本轮不重跑相同全量）：
1. 不要用“56 个固定基线”去比（沙箱里会多出 socket 类失败）。先在**同一个沙箱**里对基线提交 `824ffd4` 开一个临时 worktree 跑一遍，得到“本沙箱基线”，再用它做对比。
2. 对不需要 socket 的单元测试可以照常跑；需要 socket 的测试（`test_ui_server`、`test_ui_memory`、`test_async_requests` 里的 HTTP 用例、Memory 服务 API 测试等）必须在不受限的环境里由用户或另一位执行者跑，并把日志放进 `outputs/` 作为证据。
3. 出现挂起就用超时终止并保留栈，不要把“未跑完”写成通过。

## 1. 已经做出的设计决定（不要推翻，除非用户明确要求）

1. 请求默认**异步**（`request_mode=async`），同时只允许 1 个未完成请求；`blocking` 保留为消融基线。approval 和“只剩 1 次尝试”时强制阻塞。
2. “被采用 / 被忽略”是 **disposition**（`response.used` / `response.ignored` 事件），**不是新的请求状态**。现有前端大量判断 `request.state === 'answered'`，不要改状态枚举。
3. approval 信号来自独立安全监控的**软限位**，规则在已批准 config 的可选键 `approval_rules`（默认不配置，所以默认不触发）。interrupt 是先停机再入收件箱的 CRITICAL 请求，不调 Advisor、不占 credit。
4. 每个被采用的 hint 都生成候选 Memory（带指导后的结果和 `effective` 标记），由配对验证筛选；入库不晋升；验证触发保持人工。
5. 资源预留表 `resource_reservations` **只在首次使用时创建，不在 `_initialize` 里创建**，否则打开冻结的 Memory 快照会改动其字节。
6. GPU 时间 = 主机 GPU 利用率加权秒数（含同卡其他进程），预算 0 表示“不设预算但照样记录”，无读数时是 `null`（未追踪），不是 0。
7. `scheduler_config` 已加入 `request_mode`、`approval_rules`、`gpu_seconds_limit`，所以**旧七策略冻结包的摘要和源码字节锁都已失效**。

## 2. 任务清单（按顺序做；每项都要有测试，并更新 `STATUS.md` 与 `docs/attentionbench_progress.md`）

### A. 收尾第 3 步 —— 工程回归门槛通过（unrestricted 完整复验）
- 已核验完整日志、原 argv/cwd/PYTHONPATH、开始结束时间、退出码及两仓库 HEAD：主 `aef9ca86`，Memory `15968a3`。
- harness 1309 passed / 56 failed / 10 skipped / exit 1；失败 node ID 与固定 56 项基线精确相同，新增 0、修复 0。Memory 77 passed / exit 0。
- 12 个第 3 步代码/测试 SHA 与提交及运行前后完全一致；三份原有未提交文档解释整体 tracked_source_unchanged=false。核验结果 `outputs/handoff-execution-20261016/A-audit.json`。
- 受限沙箱中断、EPERM、只读 Git 与旧归档绑定缺失均保留为历史，原报告和基线不修改；旧 A 段另存 `outputs/handoff-execution-20261016/historical-A-plan.md`。A 已完成，继续 B；不等于真实服务/正式实验验收。

### B. 后端五项 —— 已实现并通过定向工程验收（新源完整 unrestricted 回归未核实）
1. **Run 崩溃回收**：新增 `run_recovery.py`。launch 的监督进程已死、run 仍是 RUNNING 时：取消挂起/fallback 的请求（退还 assistance credit、释放 `resource_reservations`、结束等待窗口），run 转 FAILED（原因 `scheduler_lost`），launch 记终态。在 UI server 启动和 `/api/runs` 列表时调用；必须幂等，且不动存活的进程。测试用 `populated_store`（见 `tests/test_attention_store.py`）造一个带挂起请求的 run。
2. **Advisor 只读缓存重放**：给 `SimGTAdvisorProxy` 加 `replay_only`（缓存未命中直接报错 `advisor_cache_miss`，不调用 provider），CLI `--advisor-replay-only`，使相同条件可以逐字复现。记录进 `scheduler_config`。
3. **消融开关**：`memory_enabled`（关闭时完整规划器不检索、不使用 Memory）与 `trace_evidence`（关闭时规划器不用 trace assessment，Advisor 包只含失败摘要，不含 events/evidence）。两者进 `scheduler_config`，在 `AttentionRuntime._advisor_packet` 与 `formal_attention_run.execute` 里落实。测试要证明“关掉后 Advisor 包里确实没有对应内容”。
4. **随机升级的次数匹配工具**：现在的 `target_request_count` 是人工预注册的常数。新增函数/CLI，从同任务同 seed 的完整规划器 run 的 `requests`（不含 interrupt）数量派生随机策略配置，并写出可审的 JSON 与 SHA。
5. **请求生命周期时间线**：`request_lifecycle.py`，按请求列出 pending/answered/timeout/fallback/cancelled/ignored/deferred 的时刻、回复延迟、实际采用的回复、关联的执行和候选 Memory；放进 run 快照 `request_timeline`，并提供 CLI 输出 JSON。这是 live-human 验证（要求记录响应延迟、状态转换、最终采用的回复和 Memory 产物）的记录格式。
- 验收：各自的新测试通过；不新增失败。

### C. 合并 8769 分叉 —— 代码桥补缺与无socket回归通过；host临时端口/切换待验
- 8769 实际运行 `outputs/dev-live-monitor-20261014/candidate-eval-agent/`（非 git 仓库）；主工作区的 Dev v2 后端（`dev_v2_loop`、`dev_experiment`、`dev_ledger`、`dev_report`、`dev_planner*`、`dev_wait_recovery` 等）更新，8769 版本独有 `v2_evaluator.py`、`daily_dev_v2.py`、`dev_preview.py`、多视角相机。**不要整体覆盖任何一边**，需要三方合并，先核实 `candidate-fontfix` 是否是公共祖先。
- 已有的约束：不要动 `candidate-fontfix`、`candidate-final`；重启 8769 必须先确认没有活动 run，并保留回滚办法（旧目录保留）；自动模式的权限分类器会拦截重启和写切换脚本，需要用户自己添加 allow 规则，不要绕过。
- 验收：合并后 `test_dev_workspace.cjs` 与 Dev v2 相关 Python 测试全过；数据库切换前后逐行哈希一致。

### D. 前端 —— 界面与渲染合同已实现；真实截图/持久化端到端被明确阻塞
接口清单（均已有测试）：
- 请求：`explanation`、`permitted_actions`、`request_dispositions`、`waiting_activity`、`request_timeline`（B5 完成后）；`POST /api/requests/{id}/ignore`。
- 资源：每项都有 `reserved`；GPU 有 `budgeted`、`coverage`。
- run 上下文：`lock`（条件、`policy_switchable`、`ranking_eligible`）、`autonomous_work`（running / continuing_while_waiting / blocked 的 resource|failure|dependency）、`/api/run-options` 的 `execution_targets`、`request_modes`、`limits.gpu_seconds`。
- Memory：快照 `memory`；`GET /api/memories[?status=]`、`GET /api/memories/{id}`、`POST /api/memories/{id}/{disable|enable|rollback|expiry}`（需要 `actor`、`reason`）。
- 要做的界面：收件箱显示 explanation/优先级/截止/状态与忽略按钮（按 `permitted_actions` 显示，不要在页面里硬编码）；请求详情显示检索到的 Memory 内容和版本；候选/验证队列；生命周期按钮；资源卡片显示 reserved 与 GPU 覆盖说明；执行目标与请求模式作为独立选项，真机显示“不可用”及原因；run 锁定条件和是否可参与排名；自主工作的阻塞原因。
- 测试环境（来自之前的记录）：`NODE_PATH=/home/truares/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules`，`CHROME_PATH=/usr/bin/google-chrome`，`UI_TARGET_TIMEOUT=6000`；`test_live_ui.cjs` 有 85 项固有红灯，比较失败名集合而不是数量；必须用真实截图验证，不要把“被阻塞的测试”报成通过。
- 验收：用真实 UI（候选目录先试，不直接上 8769）走通：收到请求 → 忽略/回复/延后 → 看到采用或忽略 → 看到 Memory 候选 → 禁用/回滚 → 刷新后状态保留。

### E. 基础 benchmark 稳定性（需要模拟器，按 `AGENTS.md` 的 M1→M6 一次只开一个模块）
- 当前核验到 v112（原 v35 留作历史，不覆盖）：`/home/truares/桌面/attentionbench-fixed-base-profile-20260929/engineering_v112_isolated_continuation/final_audit.json`，50/50 有效槽、170 attempts、原生成功0/50，comparison_bias_gate=false / formal_eligible=false。原包无最低 native success 数，不新增成功率验收条件；depth 原始异常材料缺口未闭合。当前仅 M1 复核，完整入口32项自动测试通过。新鲜双 Service 复核中，Robosuite 一次真实 Service handoff 完成、native_success=false；RoboCasa frozen entry 被当前 V2-only 守卫在 simulator/agent Service 启动前拒绝，停止且未重试。M1 未通过，不推进 M2–M6；详见 `outputs/handoff-execution-20261016/E/M1-current-service/20261015T221513.598849Z/RESULT.md`。
- timeline 要求每套至少 2 个稳定任务，目前冻结主实验每套只有 1 个任务（`cube_lift`、`counter_to_sink`）。先审计再冻结验收包，不要边做边加需求。
- 不要为了翻布尔值放宽门槛，不要改历史签名。

### F. 重新冻结并准入
- 改动已使旧冻结包失效：重新生成 350 个配置/锁（2 任务 × 25 seed × 7 条件），重新计算源码字节锁，重写 `REVIEW.md`，重新做总体准入审计。
- 批准必须由用户发出，不能由执行者代写；`execution_authorized` 在用户明确授权前保持 false。

### G. 实验（开发集前四项已获本轮授权，仍须依次通过前置验收与预算核算）
1. 开发集短验证：确认冻结配置无集成错误，不计入结果。
2. 350 格主矩阵：两任务 × 25 seed × 7 条件，同一 Advisor、同一预算。运行中只修明确的基础设施 bug，不按中途结果改任务/策略/提示/预算。
3. 三项消融（子集 5–10 seed）：blocking/asynchronous、Memory on/off、trace-evidence on/off（B3 提供开关）。
4. 在真实服务上跑至少一个候选的 5 seed 配对验证，各覆盖一次晋升和一次拒绝路径（目前拒绝路径只被单元/集成测试覆盖）。
5. live-human 验证：内部用户在真实 UI 里处理 hint / approval / interrupt，覆盖及时回复、延迟/无回复（fallback）、忽略、取消；用 B5 的时间线记录。这些数据只用于系统验证和案例，不进主 benchmark 排名。
6. held-out（1001–1100）最后做，且需要单独冻结。

### H. LIBERO 迁移（主 benchmark 稳定之后）
- 现状：attention 分支里**没有任何 LIBERO 代码**；旧 bridge 在另一个 checkout `~/桌面/Tidybot-Universe/benchmarks/libero_native`（分支 `benchmark-test`）。
- 步骤：审计旧 bridge 的 SDK 边界、成功判定、重置、产物路径 → 选一个 transfer suite 和 3–5 个任务 → 接入同一套 trace/request/artifact/result 管线（实现 `attempt_executor` 即可复用 `run_sim_gt_attention`）→ 只跑 Autonomous、Reactive 或 Retry-k、完整规划器。

### I. 不在范围内
真机执行目标（需要经认证的真机 runner 和独立硬件急停）、Deploy 在线发现、把 LIBERO/真人任务混进主线模块。

## 3. 红线（延续项目规则）
- 仅在全部相关本地验收通过后允许 push 专用新分支；不推/合并默认分支、不建 PR、不发布正式实验结果、不改历史证据和历史签名、不动 held-out 原件、不动 `candidate-fontfix`/`candidate-final`。
- 模型/模拟器与条件式 8769 重启已有本轮授权，仍须满足固定验收、每 run 累计预算、主矩阵冻结、真实用户批准及重启空闲/原参数/逐表哈希/回滚守卫；权限或认证限制不得绕过。live-human 需要实际内部用户参与，不能伪造；held-out 仍须单独冻结。
- 区分四个层次并如实汇报：代码已存在 / 自动测试通过 / 真实服务跑通 / 正式实验验收完成。失败和未覆盖项保留原文，不得改写。
