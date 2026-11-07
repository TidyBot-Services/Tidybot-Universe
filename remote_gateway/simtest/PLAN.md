# 方案：Remote 多用户模拟测试

2026-11-07，分支 `claude/bold-gates-c82b8e`，基于 a503112。这份方案等用户确认。真实测试要等中控台放行，并告知基线 commit。

## 现在到哪一步

| 层级 | 状态 |
|---|---|
| 脚本和监控页 | 已写好，在 `remote_gateway/simtest/` |
| 自动测试 | `remote_gateway/tests` 全部通过，其中新增 `test_simtest.py` 18 项 |
| 离线全流程 | 跑通：真实 Chrome + 真实网关 + 真实页面 + 假后端，9 个用户，37 项检查全过。页面判定“有错误”，因为测出了下面 4 个产品问题 |
| 真实 run | 没有跑。本任务没有开 run，没有占 `sim.lock`，没有重启服务 |

## 一、模拟用户

每人一个独立的 Chrome 上下文、一个独立邀请码和账号，通过点击真实页面操作。

| 用户 | 剧情 | 覆盖 |
|---|---|---|
| U1 阿青 | 开 run、看实时画面、回复求助、等结束 | 登录、大厅、开 run、排队、实时画面、回复 |
| U2 小白 | 排队时取消 | 离开队列、队列接力 |
| U3 老周 | 点“跳过” | 跳过求助 |
| U4 小林 | 不回复 | 120 秒后 Advisor 接手 |
| U5 大海 | 看到画面后 Stop run | Cancel、`sim.lock` 释放、下一位启动 |
| U6 阿杰 | 运行中断网，恢复后回复 | 断线重连、画面恢复 |
| U7 小雨 | 排队时关闭页面，稍后再打开 | cookie 和排队位置保留 |
| U8 黑客 | 错误邀请码、动别人的 run、无 CSRF、运维接口、路径穿越、占用 `lab` 名字、开第二个 run | 无权限操作，每项都核对状态码 |
| G1 路人 | 不登录，跟着看机器人，尝试写操作 | 公开观看、访客被拒 |

离线结束时，脚本还会向假后端核对求助结果（human、skip、advisor），不只看页面显示。

## 二、假后端和真实 run 的比例

| 阶段 | 假后端 | 真实 run |
|---|---|---|
| 用途 | 全部剧情、并发、故障注入，可反复跑 | 证明真实链路在多人下也成立 |
| 规模 | 9 人全剧情，时钟 8 倍，一轮约 6 分钟 | 同样 9 人跑 1 轮：6 个真实 run，每个最多看 10 分钟；U2 不上机器人；U5 看到画面就停 |
| 占用 | 不用 GPU 和模型，磁盘小于 2M | 预计 70–90 分钟，单仿真串行 |

假后端校验网关签名，并记录“同一时间两个 run”。真实测试只用 PARCC GLM，不用 Codex，所以不消耗 Codex 额度。

## 三、监控网页

默认地址 `http://127.0.0.1:8870/`，只监听本机。从 Mac 打开用 `ssh -L 8870:127.0.0.1:8870`。

- 每个用户一条泳道：状态、排队位置、当前动作和已用时、run 编号、实时画面有无、页面截图（点开放大）、状态时间轴。页面每秒刷新。
- 顶部：机器人上是谁、队列、错误数、检查项、`sim.lock`、磁盘。
- 错误常驻显示：步骤失败、HTTP 错误、页面脚本异常、请求失败、检查未通过，都列在红框和泳道里。
- 数据断了也报红：数据源读不到、30 秒没有新事件、页面收不到数据，都会显示红色告警。
- 预期拒绝（比如 U8 的 404）单独计数，不算错误，但仍然可见。
- `sim.lock` 只读 `/proc/locks`，不去试锁，所以监控不会占锁。

## 四、“PARCC 无上限”放开什么

代码核查结果：网关和 harness 都没有 PARCC 的 token 或调用次数上限。真正卡使用量的是网关的次数限制。

| 项 | 默认 | 测试实例 | 说明 |
|---|---|---|---|
| `runs_per_day` | 3 | 100000 | 放开 |
| `versions` | 3 | 10 | 放开到 harness 硬上限 |
| `help_requests` | 3 | 10 | 放开到 harness 硬上限 |
| `wall_seconds` | 3600 | 1200 | 不放开：它管 GPU 和磁盘，不管模型用量 |
| `active_runs` | 1 | 1 | 不放开：这是要测的规则 |
| 请求限流、登录限流 | 10/秒，实时 30/秒 | 不变 | 要测真实限流；此前真实 run 出现过 14 次 429 |

这些值只写进 `rr-sim/real/gateway.json`。`config.py` 的默认值和 `deploy/` 模板不改，`test_no_limit_lives_in_the_test_instance_and_nowhere_else` 会检查这一点。

## 五、真实测试放在哪

建议放在 qifei（RTX 5090），网关在本机：

- 本机根盘不用承担 run 数据。
- 新开实例 `rr-sim/real`，网关端口 8871，经现有隧道 8862 连 qifei，和 8863 共用 `qifei-sim.lock`。
- 只走本机和 SSH 隧道，不开放公网端口。
- seed 用 101。seed 102 在 qifei 过不了场景校验。

放行后要做的准备：

1. 把 qifei 的 harness 更新到基线 commit。这一步要重启 qifei 上的 harness，我会先向中控台确认。
2. PARCC key：qifei 的 `~/.litellm-key` 被拒（401），需要用户用 getpass 输入，key 不经过对话。

## 六、磁盘预算和清理

| 项 | 值 |
|---|---|
| 现状 | truares 剩约 200G（用了 78%）；qifei 剩 177G（用了 95%） |
| 已知 run 大小 | 20 分钟 2.0G，36 分钟 2.7G，75 分钟 7.6G |
| 单 run 预算 | 预计不超过 3G，硬上限 20G |
| 整次测试预算 | 预计约 16G，硬上限 25G |
| 底线 | 运行主机剩余不低于 130G，否则不开始或叫停（qifei 上是沿用 truares 的数字，请确认）；新实跑底线 60G 同时满足 |

- 跑前跑后都打印 `df`，监控页也实时显示。
- 安全线每 30 秒检查一次。超限时，暂停新 run，所有用户取消自己的 run，页面显示原因。这个流程已用假后端验证。
- 清理：`python -m remote_gateway.simtest.cleanup` 只列出本次测试数据库里的 run，加 `--delete` 才删除。删除前我会把清单给用户。

## 七、需要用户决定

1. **`no-new-runs` 标志**：这个标志还在，写于 10-31，当时用了 76%。truares 网关的 `stop_files` 包含它；qifei 网关不包含，所以技术上挡不住 qifei。qifei 上的真实测试是否受它约束？
2. **真人回复路径**：GLM 很少提交版本或求助，临时提示词在 qifei 上也没触发。
   - 方案 A（建议）：真实 run 全用 GLM + `reactive_help`，未触发就如实报“未触发”。
   - 方案 B：另加 1 次 Dev 用 Codex 的 run。此前这样触发过，额度从 0% 到 1%，但要临时放开网关对 Codex 的限制，并守 77% 停线。当前额度未核实。
3. **qifei 的磁盘底线**：沿用 130G 是否合适。

## 八、离线测出的产品问题

都是真实页面和网关的行为，用假后端复现，还没有在真实 run 上观察过。这次没有修。

| # | 问题 | 位置 |
|---|---|---|
| 1 | 排队时 Stop run 是灰的，用户无法从页面离开队列；网关的 cancel 本身正常 | `live.js` 的 `canStop`，`dev_agent_view.py` 的 `can_stop=False` |
| 2 | 在 Remote 点“跳过”会多发一个 `POST /api/lobby/runs/{run}/reply`，返回 404，并抛脚本异常。跳过本身送达了 | `lobby.js` 的 `bindHelp` 给真实 run 也绑了 `[data-hr-skip]` |
| 3 | 别人的 run 在等回复时，非主人和访客也看到回复框；提交会被网关拒绝 | `live.js` 的 `agentPolicyHelp` 没判断主人 |
| 4 | 排队的 run 轮到时启动失败，会被静默移出队列，页面只显示“PREPARED / NOT STARTED”，没有错误 | `server.py` 的 `dispatch` 和 `loop` 丢弃了结果 |

另有一点只来自读代码，未实测：`policy._card` 不会给真实 run 产生 `waiting_human`，所以大厅不显示“Needs you”。

## 九、基线合并后要补

- “等待回复时全屏警示 E→F”不在 a503112 里，基线合并后要加进 U1 的检查。
- 用基线 commit 重跑一遍离线全流程，再开真实测试。

## 怎么运行

离线（`rr-sim/venv` 里有 Playwright，用系统 Chrome）：

```bash
~/桌面/AttentionBench-Workspace/development/rr-sim/venv/bin/python -B -m remote_gateway.simtest.run_sim --dir /tmp/sim-offline --hold
```

故障演示（第 18 秒后端宕机，第 30 秒启动失败）：

```bash
~/桌面/AttentionBench-Workspace/development/rr-sim/venv/bin/python -B -m remote_gateway.simtest.run_sim --dir /tmp/sim-fault --only U1 U3 --scale 0.2 --fault 18 '{"down": true}' --fault 30 '{"start_fails": true}'
```

真实模式要带 `--real --release-commit <中控台给的 commit> --run-root <run 目录>`。commit 不符、停止标志还在或磁盘低于底线时，脚本都会拒绝开始。
