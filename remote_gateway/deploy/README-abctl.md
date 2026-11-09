# Remote 部署（简版）

详细说明见 `README-abctl-full.md`。

## 登录

```bash
ssh -p 7288 qifei@158.130.109.188          # 需要 Penn VPN
export PATH=~/ab-remote-20261107/tools/bin:$PATH
```

## 改代码并上线

```bash
abctl workspace 你的名字                    # 只第一次
cd ~/ab-dev/你的名字
# 改代码
git add -A
git -c user.name="你的名字" -c user.email=你的邮箱 commit -m "改了什么"
abctl test 你的名字                         # 测试，不上线
abctl --by 你的名字 deploy 你的名字          # 上线
```

## 常用

```bash
abctl status              # 看状态
abctl url                 # 网址
abctl invite --days 7     # 邀请码
abctl emails              # 用户登录时填的邮箱
abctl rollback            # 退回上一版
```

## 注意

- 上线前先 `git merge main`。
- 有 run 在跑时，deploy 会等它结束。
- 不要直接改 `~/ab-remote-20261107/repos/attentionbench`，也不要手动重启服务。
