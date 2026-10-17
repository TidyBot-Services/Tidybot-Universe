# OpenArm365 参考策略阶梯

本工具只针对开发任务 `pick_place_counter_to_sink`、seed `101`，用脚本化 provider 提交一个无模型策略。OpenArm365 Dev v2 的关节位置控制器默认 `kp=1500`：`kp=300` 时手腕关节摩擦死区导致无法满足 4 mrad 判据；真实模拟器梯度测试验证 `1500` 可通过。真实执行命令：

```bash
PYTHONPATH=/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service \
  /home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python \
  -m benchmarks.attention_harness.diagnostics.reference_ladder \
  --out reference-ladder-results
```

本 worktree 的 `ATTN_DIAG_JOINT_KP` 可覆盖默认值，例如设置为 `300` 可回退。运行器把当前环境原样传给每次执行的子进程；设置为非 `1500` 时会警告，但不会阻止执行。默认五级依次为 `approach`、`grasp`、`lift`、`carry`、`release`，各执行一次。每次尝试限 480 秒、SDK 限 80 次；生成策略自身采用更保守的 70 次上限。每次运行写入独立目录，并在输出目录更新 `results.json` 和 `results.md`。为避免误以为旧实验再次执行，请为再次运行选择新的 `--out`。

先离线检查：

```bash
python -m benchmarks.attention_harness.diagnostics.reference_ladder --dry-run
python -m benchmarks.attention_harness.diagnostics.reference_ladder --print-policy release:0.02
```

设计取舍：从初始末端四元数（xyzw）算工具 z 轴；物体沿该方向后退 10 cm 作预抓取点，再直线推进至物体。每段按欧氏模长等分，使用 `int(距离/步长)+1`，所以每步严格小于上限。合爪后竖直抬高 12 cm；重新读末端位置，保持当前高度，水平移到 `sink` 中心 x/y 上方，再张开。策略执行中逐段核对调用预算，若观测与开发 seed 101 的预估相差过大则停止在断言处。阶段判定依公开 SDK 操作的完成顺序；原生成功与否仍以 `native_result.json` 为准。尚无真实阶梯运行结果。
