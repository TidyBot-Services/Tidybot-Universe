# AttentionBench 亲自验证指南

目的：亲自确认系统能执行、指导影响实际动作、异常确实被停止。三个演示独立运行，不是七策略效果实验，不修改冻结包执行授权。

## 先读

- 当前包为 `seven_primary_guidance_v1_1_dev_2026-10-01`，两任务均读取 `attention_input`；“不读取”的结论只适用于旧版。
- 指导编译器是有限文本规则和公开SDK轨迹，不是通用自然语言修复器。输入、编译参数、完成动作与原生成功必须分别核对。
- [脚本说明与判据](../benchmarks/attention_harness/scripts/README.md)是详细依据。脚本已做准备检查，不等于三个演示实际通过。
- 脚本使用原工作站冻结的Python、Service与路径。新电脑需配置环境并另建路径身份，不可改历史锁或绕过检查。

## 在已配置的工作站运行

通过SSH进入工作站，在仓库根目录依次执行；上一项退出并清理后再做下一项。

```bash
cd /home/truares/桌面/Tidybot-Universe-attention-native
./benchmarks/attention_harness/scripts/verify_1_system_runs.sh
./benchmarks/attention_harness/scripts/verify_2_guidance_adopted.sh
./benchmarks/attention_harness/scripts/verify_3_unsafe_stopped.sh
```

| 演示 | 必须看到 | 不证明什么 |
| --- | --- | --- |
| 1 系统执行 | reset、一次真实step、末端位置变化、原生布尔与Service回收 | 不证明完整任务成功或所有seed稳定 |
| 2 指导采纳 | 无指导与固定hint对照；抓取偏移、接近容差及实际SDK动作有可关联变化 | 测试回复不是真实GLM；动作改变不等于成功改善 |
| 3 安全停止 | Safety拒绝超限命令、unsafe=1、无第二动作、backend派发=0、回收 | 不证明碰撞检测、真机急停或depth根因修复 |

演示2的两个策略run最多各4 attempts；提前成功、未求助或未采纳会如实返回未通过，不自动重跑。Robosuite本hint parser不支持`open_settle_steps`，保持默认10不是失败；源码和冻结证据不变，GLM调用被禁止。

每项创建独立`/tmp/attention-personal-verify-N.*`并打印停止命令。核对原Runner回收收据与外层`shell_cleanup.json`，后者须有`owned_process_groups_gone=true`、`robosuite_processes_absent=true`。不要用宽泛的`pkill -f`杀其他人的任务。

## 远程看UI

不需要主机桌面。主机须已启动与本run匹配的UI、SQLite和相机；演示脚本不会自动启动完整UI。

主机UI模板，路径和端点须换成这次实际使用的值：

```bash
python -m benchmarks.attention_harness.ui_server \
  --store /path/to/attention.sqlite3 \
  --artifact-root /path/to/attention-artifacts \
  --robosuite-url http://127.0.0.1:8082
```

在自己的电脑上替换`USER@HOST`，保持隧道开启：

```bash
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8769:127.0.0.1:8769 USER@HOST
```

浏览器打开`http://127.0.0.1:8769/ui/`。相机由UI后端代理，通常只转UI端口；RoboCasa配置见[UI文档](../benchmarks/attention_harness/ui/README.md)。

- `--demo`只是样例，不是真实验证。
- 确认同run/attempt、画面更新和请求关联；相机失败可能回退缓存帧，旧图不能当实时。
- UI中断只控制接入store的受控run，不自动覆盖手动step探针或独立负控。
- 关闭隧道不等于停实验。按`Ctrl-C`或脚本打印的`--stop <本次目录>`停止，检查回收收据。

## 记录结果

保留三项目录、退出码与原件，分别记通过、未通过或未执行；界面截图不能替代动作、Safety与回收证据。一次演示建立第一手理解，不替代多seed测试或正式效果比较。
