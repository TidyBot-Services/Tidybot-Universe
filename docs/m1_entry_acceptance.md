# M1 任务入口封闭验收包（2026-09-28 冻结）

范围：当前 U 工作树的 UI `/api/runs/start`、正式 `formal_attention_cli` 和共同的 `run_formal_attention` 入口；RoboCasa `counter_to_sink`／`counter_to_cab`，Robosuite `cube_lift`／`cube_stack`。只允许开发 seed 101–125；held-out 1001–1100 和 smoke 9001–9005 均不得经正式入口启动。固定 `sim_gt`，suite 与 task、模拟器配置、执行目标必须相符。七策略只验选择和输入锁定，不验决策效果。

## 输入、正例与拒绝例

| 条件 | 正例 | 启动执行前拒绝例 | 证据 |
| --- | --- | --- | --- |
| 任务／模拟器 | 两套 suite 各自的已支持 task，配置任务和 seed 一致，配置 schema 与 Service 身份完整 | 未知、跨 suite、目标不匹配，配置 `perception_mode` 非 `sim_gt` | 参数化入口测试、双 Service smoke |
| seed | 开发 101–125，且与批准配置相同 | 其他值、held-out、smoke、布尔值、配置错配 | 入口拒绝测试 |
| 策略 | 七个 `POLICY_IDS` 中选择一个 | 未知策略 | 参数化入口测试 |
| 预算／期限 | attempts 1–10、credits 0–10、tokens 0–100000、真人期限 1–600 秒、单次执行期限 30–600 秒；有限实数 | 类型错误、NaN／Infinity、越界 | 入口拒绝测试 |
| `demo_first` | 已批准、固定 SHA 的公开 demo manifest 与资产；suite/task 匹配，资产投影仅公开 SDK 数据 | 缺 manifest／SHA、manifest 或资产篡改、错配／私有字段 | 入口拒绝测试与固定 demo 收据 |
| 随机基线 | 已批准 SHA 的预注册 JSON，目标、failure slots、seed 与预算一致 | 缺文件／SHA、篡改、超预算或错配 | 入口拒绝测试与随机计划收据 |
| 配置身份 | 批准的代码与模拟器配置 SHA、所选策略和预算形成不可变入口摘要；UI 展示后提交重校验 | 展示后修改任何批准文件；无摘要或身份错配 | 锁定记录、下游 request／trace 摘要、拒绝测试 |

通过标准：正例在运行前写持久锁定记录；下游真实 Service 边界收到相同 suite、task、seed、策略、预算和配置 SHA；UI 与 CLI 对相同条件同判定。每个拒绝例在建 Attention run、启动 Service 之前失败，不产生貌似有效的 run。双模拟器各一次最小真实 Service 交接，只验证配置传递，不要求原生任务成功。保存命令、版本、输入／摘要和原始结果；运行相关自动测试并复测修复项。

M1 输出给 M2–M6 的契约是锁定身份和配置快照；M2 的代码生成／人工批准流程、M3 的 runner 稳定性、M4 的七策略决策、M5 Memory、M6 终态展示，以及 held-out／正式成绩均不在本包内。Service 可用性属于外部依赖；若无法完成真实交接，M1 标为部分完成且外部阻塞。

## 冻结的输入／输出契约

UI catalog 只接受绝对路径和批准 SHA；操作员选择 `profile_id`、seed、七策略之一、执行目标、求助模式、attempt／credit／token 预算与两个期限。正式 CLI 接受等价字段与代码、模拟器配置及所选预注册文件路径／SHA。UI 提交与 CLI 启动共用 `inspect_formal_entry`；CLI 还核对 UI 传入的 `--expected-entry-sha256`。

通过后产生 `attentionbench.formal-entry-lock.v1`：suite、task、开发 seed、`sim_gt`、执行目标、策略、批准代码／配置／demo／策略配置摘要、预算、模式、期限；对无 `sha256` 字段的排序紧凑 UTF-8 JSON 计算 SHA-256。CLI 在执行前将批准代码和配置复制进 `entry-locks/<id>/`，并写 `entry_lock.json`。UI 的持久 launch 行保留同一 lock；下游 `FormalRunRequest`、runner result、原始 trace 与 `attention_run.json` 保留完全相同的 lock。边界按 lock 和四类产物 SHA 复核。demo 的 manifest 和资产在建 run 前核验，随机基线目标在建 run 前核对 seed、failure slots 与 credit 预算。拒绝不产生 Attention run。

其他可执行脚本已审计：`sim_gt_attention_cli.py`、两套 `sim_gt_cli.py` 和 RoboCasa `generated_policy_cli.py` 是开发／单任务探针；两套 `formal_cli.py` 是单 attempt runner 探针。它们不生成上述 M1 lock，已有结果均标 `formal_eligible=false`，不得当作本包通过的正式任务入口。`run_formal_attention` 是正式 CLI 的内部下游函数，接收并核验 lock；不带 lock 的旧开发调用不授予 M1 准入身份。

## 本次结果（冻结包逐项）

| 项 | 结果 | 原始证据 |
| --- | --- | --- |
| 双 suite 任务／`sim_gt`／开发 seed 与错配拒绝 | 通过 | `tests/test_m1_entry.py` 参数化正反例；`pytest-m1.stdout` |
| 七策略选择、预算／期限、demo／随机预注册正反例 | 通过 | 同上；127 个相关测试全部通过 |
| UI 展示后文件修改、CLI held-out／缺预注册拒绝且无 run | 通过 | 同上；HTTP held-out 旧回归与新增 UI／CLI 测试 |
| UI／CLI 同条件锁定与持久身份 | 通过 | `ui-http-smoke-v3.json`；两套 suite 的 UI 与 CLI 各得同一 entry SHA，`audit-all.stdout` 复核最终版本的 4 个 run |
| 双 Service 最小真实交接 | 通过 | Robosuite `cube_lift`、RoboCasa `counter_to_sink` 各 seed 101、1 attempt；`*-v3.command.json`、原始 stdout／stderr、`audit-all.stdout`；原生结果均 false，`formal_eligible=false` |
| 未通过／外部阻塞 | 无 | 本次仅关闭 M1；M2–M6 及全链正式实验仍未验收 |

原始证据目录：`/home/truares/桌面/attentionbench-m1-entry-20260928/`。仓库与 Service 版本、命令和文件 SHA 见该目录的 `manifest.json`；静态回归入口为 `tests/test_m1_entry.py`，真实产物审计入口为 `protocol/v2/audit_m1_entry.py`。
