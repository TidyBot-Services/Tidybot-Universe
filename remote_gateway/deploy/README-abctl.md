# Remote 部署指南（abctl）／ Deploying Remote with abctl

AttentionBench 的公开网页（Remote）整套跑在 **qifei** 上：两个 harness（8860 测试、8870 校园）、网关（8871）和 Cloudflare 隧道，都由 qifei 的 systemd 守护，断了会自动重启。改代码、上线、回滚都用一个命令：`abctl`。

The public AttentionBench site (Remote) runs entirely on **qifei**: two harnesses (8860 test, 8870 campus), the gateway (8871) and a Cloudflare tunnel, all kept alive by qifei's systemd. Changing code, going live and rolling back all use one command: `abctl`. English summary at the end.

---

## 0. 登录

```bash
ssh -p 7288 qifei@158.130.109.188     # 需要 Penn VPN；所有操作都用 qifei 账号
abctl status                           # 先看一眼现在的状态
```

`abctl` 在 `~/ab-remote-20261107/tools/bin/abctl`，默认不在 PATH 上。登录后先运行一次 `export PATH=~/ab-remote-20261107/tools/bin:$PATH`（只对当前终端生效），或者每次都写完整路径。下面的例子都假设已经运行过这句 export。

## 1. 看状态、拿网址和邀请码

| 命令 | 作用 |
|---|---|
| `abctl status` | 线上是哪个 commit、4 个服务是否在跑、公开网址、正在执行的 run、磁盘剩余、最近 5 次部署 |
| `abctl url` | 当前公开网址（临时隧道，cloudflared 重启后会变） |
| `abctl invite --days 7` | 生成一个邀请码（输出是 `编号 邀请码`），发给要试用的人 |
| `abctl logs gateway` | 看日志：`test` / `campus` / `gateway` / `tunnel` |
| `abctl pause` / `abctl resume` | 暂停或恢复接新 run（已经在跑的 run 不受影响） |
| `abctl doctor` | 体检：环境、密钥权限、服务是否开机自启、磁盘 |

## 2. 改代码并上线（最常用）

```bash
abctl workspace alice            # 第一次：建自己的工作目录 ~/ab-dev/alice（分支 alice，从 main 开）
cd ~/ab-dev/alice
# …改代码…
git add -A
git -c user.name="Alice" -c user.email=alice@upenn.edu commit -m "说清楚改了什么"
abctl test alice                 # 跑和部署一样的检查，但不上线
abctl --by alice deploy alice    # 上线
```

`abctl deploy` 会依次做这些事，**任何一步失败都不会动线上**：

1. **加锁**：同一时间只能有一个人部署；别人在部署时你会看到是谁、从什么时候开始。
2. **防覆盖检查**：如果你的分支不包含线上的 commit，部署会冲掉别人已经上线的改动，所以直接拒绝。先 `git merge main`（或合并线上的分支）再部署。
3. **测试**：网关测试（pytest）必须全部通过；UI 测试（node）不能有"线上版本能过、你的版本过不了"的新失败。
4. **等空闲**：有 run 正在执行就等，每 2 分钟查一次，最多等 6 小时。`--now` 可以不等，但必须在终端输入 `yes`，因为会打断别人的 run。
5. **切换**：暂停接新 run → 备份网关数据库（`~/ab-campus-data/gateway-backups/`，保留最近 10 份）→ 把线上指向新版本 → 重启两个 harness 和网关。**隧道不重启，所以网址不变。**
6. **健康检查**：3 个端口都在监听、校园 harness 带着 PARCC key、`/ui/` 和 `/api/lobby` 都返回 200。120 秒内不通过就**自动退回上一版**并报错。
7. **放开**：恢复接新 run（如果部署前就是暂停状态，就保持暂停），写入部署日志。

## 3. 回滚

```bash
abctl releases          # 列出保留的版本，* 是线上的
abctl rollback          # 回到上一次部署前的版本
abctl rollback 1a2b3c4d5e6f   # 回到指定版本
```

回滚也会等空闲、做健康检查。线上版本和它之前的那个版本（回滚目标）永远不会被清理；其余版本最多一共保留 3 个，`abctl test` 产生的版本最先被清理；`original-*` 是第一次接入 abctl 之前的原始代码，作为最后的退路，不会被自动删除。

## 4. 代码怎么流动

```
truares（主开发机）  ── git push qifei main ──►  qifei 中心仓库 ~/ab-remote-20261107/git/attentionbench.git
                                                    ├── main（主线）
你的工作目录 ~/ab-dev/<名字> ── commit ─────────────►  └── <名字>（你的分支，commit 后直接在中心仓库里）
                                                    │
                       abctl deploy <分支> ─────────►  ~/ab-remote-20261107/releases/<commit>/  ◄── repos/attentionbench（线上，符号链接）
```

- 中心仓库只在 qifei 上，不公开，也不同步到 GitHub。
- 主开发机用 `git fetch qifei` 拿到你的分支，合进 main 后再推回来。上线前记得先合并最新的 main，不然防覆盖检查会拦住你。

## 5. 规矩

- **不要直接改** `~/ab-remote-20261107/repos/attentionbench`：它是线上版本的链接，改了会在下次部署时丢掉，也不会经过测试。
- **不要手动** `kill` 或 `systemctl restart` 服务，用 `abctl`：它会等空闲、做备份和检查。
- **密钥**在 `~/ab-campus-data/secrets/` 和 `~/ab-campus-data/gateway-state/`，权限是 600/700，不要复制、打印，也不要放进仓库。agent 看不到这两个目录。
- **run 数据**在 `~/ab-campus-data/harness/runs/`，按 7 天保留；删除前要先列清单并确认。磁盘剩余低于 100G 时，网关会自动拒绝新 run。
- 每次部署都会记录谁、什么时候、哪个 commit、结果（`~/ab-remote-20261107/abctl/deploy.log`）。请用 `--by 你的名字` 或设置 `export ABCTL_USER=你的名字`。

## 6. 新机器或修复（很少用到）

```bash
abctl install --dry-run   # 看看会写哪些 systemd 单元和脚本，和现在的有什么不同
abctl install             # 写入并启用；如果没有 key 文件，会提示输入 PARCC key（不回显）
abctl restart-all         # 重启全部服务，包括隧道（网址会变）
```

模板在仓库的 `remote_gateway/deploy/host/*.in`，路径来自 `~/.config/abctl.json`（没有这个文件就用 qifei 的默认路径）。cloudflared 要放在 `~/ab-remote-20261107/tools/bin/cloudflared`，用固定版本，下载后核对 sha256。

## 7. 出问题时

| 现象 | 先做什么 |
|---|---|
| 网页打不开 | `abctl status`；`abctl url` 看网址是不是变了；`abctl logs tunnel` |
| 部署卡在 "waiting" | 有 run 在执行，正常现象；等它结束，或者确认后用 `--now` |
| 部署失败并自动回滚 | 看输出里的原因，也可以 `abctl logs gateway` / `abctl logs campus` |
| "another deploy is running" | 有人正在部署，等他完成；输出里能看到是谁 |
| GLM 选项不可用 | `abctl status` 里 "PARCC key in campus harness" 应该是 yes；不是的话 `abctl doctor` |

---

## English summary

- Log in as `qifei` (Penn VPN), `export PATH=~/ab-remote-20261107/tools/bin:$PATH`, run `abctl status`.
- Change code: `abctl workspace <you>` → edit in `~/ab-dev/<you>` → commit → `abctl test <you>` → `abctl --by <you> deploy <you>`.
- A deploy locks (one at a time), refuses a branch that would drop live changes, runs gateway pytest (all must pass) and the UI node tests (no new failures versus the live release), waits until no run is executing (`--now` asks for `yes`), pauses new runs, backs up the gateway DB, switches the live symlink, restarts the harnesses and the gateway (not the tunnel, so the URL stays), health-checks for 120 s and rolls back automatically on failure.
- `abctl rollback [release]` goes back; `abctl releases` lists them; 3 are kept plus the `original-*` checkout.
- Never edit `repos/attentionbench` directly, never kill or restart services by hand, never copy the secrets. Every deploy is logged with `--by` / `ABCTL_USER`.
