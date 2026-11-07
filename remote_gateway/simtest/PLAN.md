# 方案：Remote 多用户模拟测试

2026-11-07，分支 `claude/bold-gates-c82b8e`，基于 main 652ea13。已按用户经中控台转达的决定修订：测试实例所有上限放开；Dev 用 Codex，跑在 truares；回复由 Codex 生成。真实测试等中控台放行。

## 现在到哪一步

| 层级 | 状态 |
|---|---|
| 脚本和监控页 | 已写好，在 `remote_gateway/simtest/` |
| 自动测试 | `remote_gateway/tests` 全部通过，其中新增 `test_simtest.py` |
| 离线全流程 | 跑通：真实 Chrome + 真实网关 + 真实页面 + 假后端，9 个用户，48 项检查全过，包含全屏警示。页面判定“有错误”，因为测出了下面的产品问题 |
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
| 规模 | 9 人全剧情，时钟 8 倍，一轮约 6 分钟 | 同样 9 人跑 1 轮：6 个真实 run，每个从开始起最多 30 分钟；U2 不上机器人；U5 看到画面就停 |
| 占用 | 不用 GPU 和模型，磁盘小于 3M | 最长约 3 小时，单仿真串行 |

假后端校验网关签名，并记录“同一时间两个 run”。真实测试的 Dev 用 Codex（gpt-6-astra），Eval 和 Advisor 用 PARCC。

**回复是模拟的：** U1、U6、U7 的回复由 `reply_writer.py` 调 Codex 写出，再由模拟用户在页面回复框发送，走完整的页面和网关路径。这不算真人验证，报告里会写明。写回复失败时不发送，监控页报错，由 Advisor 接手。

## 三、监控网页

默认地址 `http://127.0.0.1:8870/`，只监听本机。从 Mac 打开用 `ssh -L 8870:127.0.0.1:8870`。

- 每个用户一条泳道：状态、排队位置、当前动作和已用时、run 编号、实时画面有无、页面截图（点开放大）、状态时间轴。页面每秒刷新。
- 顶部：机器人上是谁、队列、错误数、检查项、`sim.lock`、磁盘。
- 错误常驻显示：步骤失败、HTTP 错误、页面脚本异常、请求失败、检查未通过，都列在红框和泳道里。
- 数据断了也报红：数据源读不到、30 秒没有新事件、页面收不到数据，都会显示红色告警。
- 预期拒绝（比如 U8 的 404）单独计数，不算错误，但仍然可见。
- `sim.lock` 只读 `/proc/locks`，不去试锁，所以监控不会占锁。

## 四、测试实例放开什么

用户决定：测试里所有上限都放开，包括请求频率。只写进测试实例的 `gateway.json`；`config.py` 默认值和 `deploy/` 模板不改，`test_no_limit_lives_in_the_test_instance_and_nowhere_else` 会检查这一点。

| 项 | 默认 | 测试实例 |
|---|---|---|
| `runs_per_day` | 3 | 100000 |
| `versions` | 3 | 10（harness 硬上限） |
| `help_requests` | 3 | 10（harness 硬上限） |
| `wall_seconds` | 3600 | 21600（harness 硬上限） |
| `active_runs` | 1 | 1000 |
| 请求频率（普通、实时） | 10/秒、30/秒 | 100000/秒 |
| 登录失败 | 10/小时 | 100000/小时 |

不属于“上限”、继续生效的：同一时间一个仿真（`sim.lock`）、磁盘底线和预算、Codex 77% 停线、单 run 30 分钟的测试时长。放开限流后，这次测试不再能观察 429。

## 五、真实测试放在哪

按用户决定跑在 truares，只走本机：

- 新开实例 `rr-sim/real`：自己的 harness（端口 8874，自己的 store 和 run 目录，不碰 rr-1107）、测试网关 8871、监控 8870。
- 网关用 `simtest/codex_gateway.py` 启动：只在这个测试入口把 Dev 换成 Codex，并把 `quota_stop_percent` 设为 77。公开网关照旧拒绝 Codex，测试里有这一条检查。页面不用改，模拟用户照常选 Lab models。
- `stop_files` 为空（原因见下），`sim_lock` 用共用的 `/tmp/claude-1000/shared/sim.lock`。
- 用 `reactive_help` 策略，让求助和 120 秒等待有机会触发。Codex 也可能在 30 分钟内不提交，这种情况会如实报“未触发”。

中控台转达的决定：

1. **`no-new-runs`：** 用户原话“测试里完美放开”“我将放行所有的run”。标志文件不删不改，只是测试实例的 `stop_files` 不含它。`df` 可用低于 130G 时，脚本不开新 run。报告要写明这一点。
2. **PARCC key：** Eval 和 Advisor 沿用 8860 的方式，由 harness 读 `~/.litellm-key`，key 不进对话、不打印、不写日志。用户反对就改 Codex。

额度：跑前跑后打印 `/api/codex-quota`，到 77% 叫停。这个接口的读数可能不是最新；harness 里的 `QuotaMonitor` 用实时读数，同样按 77% 停。

## 六、磁盘预算和清理

| 项 | 值 |
|---|---|
| 现状 | truares 剩约 200G（用了 78%） |
| 已知 run 大小 | 20 分钟 2.0G，36 分钟 2.7G，75 分钟 7.6G |
| 单 run 预算 | 预计不超过 3G，硬上限 20G |
| 整次测试预算 | 预计约 16G，硬上限 25G |
| 底线 | truares 剩余不低于 130G，否则不开始或叫停；新实跑底线 60G 同时满足 |

- 跑前跑后都打印 `df`，监控页也实时显示。
- 安全线每 30 秒检查一次。超限时，暂停新 run，所有用户取消自己的 run，页面显示原因。这个流程已用假后端验证。
- 清理：`python -m remote_gateway.simtest.cleanup` 只列出本次测试数据库里的 run，加 `--delete` 才删除。删除前我会把清单给用户。

## 七、离线测出的产品问题（已修）

都用假后端复现，修复也只在假后端和自动测试上验证过，还没有在真实 run 上观察。

| # | 问题 | 修法 |
|---|---|---|
| 1 | 排队时 Stop run 是灰的，用户无法从页面离开队列 | `live.js`：自己排队中的 run 可以 Stop |
| 2 | 在 Remote 点“跳过”多发一个 404 请求并抛脚本异常 | `lobby.js`：`bindHelp` 不再绑定真实 run |
| 3 | 非主人和访客看到回复框和全屏警示 | `live.js`：Remote 上只有主人才有回复框，全屏警示跟着回复框走 |
| 4 | 排队的 run 启动失败被静默移出队列 | 网关记下原因，大厅卡片带 `start_error`，页面 02 显示 “DID NOT START” |
| 5 | `policy._card` 从不产生 `waiting_human`，大厅不显示 “Needs you”（读代码后确认属实） | `daily_dev_v2.sessions` 的列表带上 `human_wait`，`_card` 据此给状态和等待秒数 |

## 怎么运行

离线（`rr-sim/venv` 里有 Playwright，用系统 Chrome）：

```bash
~/桌面/AttentionBench-Workspace/development/rr-sim/venv/bin/python -B -m remote_gateway.simtest.run_sim --dir /tmp/sim-offline --hold
```

故障演示（第 18 秒后端宕机，第 30 秒启动失败）：

```bash
~/桌面/AttentionBench-Workspace/development/rr-sim/venv/bin/python -B -m remote_gateway.simtest.run_sim --dir /tmp/sim-fault --only U1 U3 --scale 0.2 --fault 18 '{"down": true}' --fault 30 '{"start_fails": true}'
```

真实模式：

```bash
python -m remote_gateway.simtest.run_sim --dir rr-sim/real --real --release-commit <中控台给的 commit> --run-root rr-sim/real/public/runs --run-cap-seconds 1800 --scale 12
```

commit 不符、停止标志还在、磁盘低于底线或额度到线时，脚本都会拒绝开始。
