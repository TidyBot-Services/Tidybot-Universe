# Dev v2 UI 真实浏览器验证（2026-10-18，轨道 B4）

**范围与方法。**用内置浏览器（真实 Chromium 页面，不是单元测试）对隔离的 Dev v2 UI（`benchmarks/attention_harness/dev_ui_server.py`）做点验。**没有碰 8769**（systemd 用户单元，未重启）。两个只读数据拷贝、两个备用端口：

| 端口 | 数据 | 内容 |
|---|---|---|
| 8772 | `wt-memaccept/.../memory_audit/real_controlled_ledger.sqlite`（拷到 `/tmp/claude-1000/ui-b4/`） | 2026-10-15 受控实验：1 个格子、2 个版本、2 次人工动作审批、Memory 检索事件、等待期间工作。**回复者是 `controlled_test_subject`（测试替身），不是真人**；该数据没有录像 |
| 8773 | `outputs/carry-102-run8-20261018/store.sqlite`（拷贝）+ 该目录下真实 artifacts | 一次诊断实验：1 个版本，带 359 秒真实录像和 `camera_recording.json` |

没有调用任何模型。这批数据**不含** Eval 判定、AdvisorProxy 回答、带 `selected_moments` 的定向请求、进行中的实时帧，所以相关项只能标“无法验证”。

## 逐项结果

| # | 项目 | 结果 | 看到了什么 |
|---|---|---|---|
| 1 | 页面加载，01–05 模块与“版本源码与 diff / 公开执行反馈 / 独立评测”区块 | **通过**（8772、8773） | 页面完整渲染，实验列表来自服务端账本，无控制台错误提示 |
| 2 | 预算预览按档位显示 | **通过**（8772） | 表单下方显示“OpenArm365 · both · SDK 320 calls / 9600 control steps / 1920s”；预览里每格 960 次 SDK 调用、28800 控制步（3 次执行×开发档） |
| 3 | 多版本源码切换 | **通过**（8772） | 版本下拉有 v1/v2；展示 v2 源码 |
| 4 | 版本间 diff | **通过**（8772） | 统一 diff：`move_delta(0,0,0.01)` → `(0,0,0.02)`，`--- v1 / +++ v2` 头与行内增删正确 |
| 5 | 公开执行反馈 | **通过**（8772） | 显示 action_trace（`IK planning rejected`，status failed）与 `execution_status` |
| 6 | 独立评测面板标注为“仅展示／离线分析” | **通过**（8772、8773） | 面板标题带该提示；内容含 `native_success:false` 等原生结果（以 JSON 展示，对操作者可见，不回流模型） |
| 7 | 人工审批请求、范围、状态、回复者 | **通过**（8772），但只有原始 JSON | v1、v2 各一条：状态 `consumed`、范围（动作、SDK 80 调用、2400 步、480 s）、回复者 `controlled_test_subject`、被哪次执行消耗 |
| 8 | 等待期间的实际工作 | **通过**（8772） | 两条 human 工作记录（耗时、“frozen public evidence only; pending response excluded”）和“查看实际准备成果”按钮 |
| 9 | 回放：加载真实录像 | **通过**（8773） | 点“加载所选版本回放”后 `video` 元素 readyState 4、时长 359.088 秒、无错误 |
| 10 | 回放：定位（seek） | **通过**（8773） | 请求 30、120、300 秒，`currentTime` 依次为 30、120、300 |
| 11 | 无录像时的降级 | **通过**（8772） | 提示“此版本尚无可定位视频”，不报错 |
| 12 | 实时视图（live） | **无法验证** | 两份数据都没有进行中的执行，页面显示“当前没有在途仿真；历史请使用执行回放”；真实在途帧未验证 |
| 13 | 选定时刻（selected moments）标记与一键跳转关键帧 | **无法验证** | 数据里没有带 `selected_moments` 的定向请求；此前 10-17 的真实浏览器检查已通过，本次未重验 |
| 14 | Eval 判定展示 | **无法验证** | 两份数据都没有 Eval 记录 |
| 15 | AdvisorProxy 回答展示 | **无法验证** | 两份数据都没有 AdvisorProxy 回答（只有人工审批） |
| 16 | 刷新后状态 | **部分通过**（8773） | 刷新后实验列表仍在（状态在服务端账本）；**选中的实验不会自动恢复**，要重新点击。未验证服务重启后的恢复 |
| 17 | “等待人工回答的时间作为单独资源显示”（timeline 要求） | **不通过** | 05 资源区只有 `goal_used / goal_reserved / goal_remaining` 的原始 JSON，`help_wait_seconds` 埋在 JSON 里，没有单独、带标签的资源行 |
| 18 | “Memory 来源以结构化展示（来源轨迹、验证结果、版本），而非原始 JSON”（timeline 要求） | **不通过** | “Memory 来源、版本与授权”区块直接倾倒事件列表 JSON（`planner_memory_query_started / retrieved / finished`，含 memory_id、version、grant、packet sha），没有按来源/验证/版本排版 |

## 发现的问题

1. **（需要决定）Eval 开启门与独立 Dev UI 的创建表单冲突。**表单里没有 Eval 控件（模式、任务、seed、策略、预算、Full planner 开关……），而 experiment 模式现在要求显式 `eval_agent.enabled: true`（轨道 B1 的门）。按代码判断，这个 UI 在“冻结策略对比”模式下提交会被门拒绝。**本次没有点击提交**，这条来自代码阅读和 Codex 的调用方影响分析，不是浏览器实测。主 UI（`ui/live.js`）的桥接路径已由 B1 同步修改，但那条路径本次**没有在浏览器里验证**。
2. 资源区与 Memory 来源区只有原始 JSON（项 17、18），与 timeline 的 UI 要求不符。
3. 刷新后不恢复选中的实验（项 16），属于小问题。
4. 副作用：启动 `dev_ui_server` 会往 `--artifacts` 目录写 `ui-instance-<pid>.json` 回执；8773 实例把它写进了 `outputs/carry-102-run8-20261018/artifacts/`（只新增文件，已在收尾时删除）。

## 未验证

主 UI（8769 对应的 `ui_server`/`serve_daily`）整体；定向请求的关键帧标记；实时帧；Eval 与 AdvisorProxy 的真实展示；服务重启后的恢复；真人（非测试替身）回复流程；窄屏/移动布局（只用了默认视口 1024×768）。
