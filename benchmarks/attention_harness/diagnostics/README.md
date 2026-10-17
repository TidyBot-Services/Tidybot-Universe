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

## Seed 101 左臂可达性图（离线单目标 IK）

`reachability_map.py` 在进程内复用 `ik_path_world.json` 的完整 818 个底座系障碍盒、`q0` 与实测锁定关节，调用 planner core 的 `solve_ik`，不启动 HTTP 服务、模拟器或运动规划。单次正式扫描是 196 个网格位置加第 6 步目标、13 个姿态、两种场景，共 5122 次单目标 IK。网格以夹具中唯一的 `obj_g*` 碰撞盒组中心范围中点作牛奶**位置代理**；它不是公开物体位姿。x/y 各为相对代理中心 ±12 cm、4 cm 间隔，z 为 0、5、10、15 cm。候选姿态在**机器人底座系**定义：工具 +z 指向 +x 水平、+x/-z 斜下 45°、-z 竖直向下，分别绕工具 z 轴滚转 0/90/180/270°，另含初始 FK 姿态。使用服务默认的 4 mm 位置、0.03 rad 姿态容差；未放宽求解器或身份检查。

夹具没有世界系到机器人底座系变换。正式运行先从 `q0` 做 FK，得到底座系初始 TCP 完整位姿，再与 `reference_ladder.py` 中 seed 101 的**世界系初始 TCP 位置和 xyzw 四元数**配准：`T_base_world = T_base_tcp(q0) × inverse(T_world_tcp)`。以此把记录的世界系牛奶位置及第 6 步世界系目标转成底座系；报告同时给出 `obj_g*` 代理中心与换算后的物体位置之间的距离。第 6 步世界系当前位置和位移是近似记录，因此它的目标也仅具相同精度；配准依赖参考策略记录与夹具 `q0` 属于同一初始状态和 TCP 定义。报告保留变换旋转、初始 FK、代理来源、逐点失败原因，便于核验。若这项配准前提不成立，世界系数字无法仅由夹具换算，应以夹具底座系网格结果为准。

先用两个解释器均可运行的纯数据检查：

```bash
python -m benchmarks.attention_harness.diagnostics.reachability_map --dry-run
```

在用户的官方环境中执行真实 IK 时，令 `ROBOT_CONFIG` 指向该环境的机器人配置 YAML，`ASSETS_DIR` 指向相同运行配置的 cuRobo 资产目录，然后运行：

```bash
PYTHONPATH="$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service" /home/truares/Documents/Codex/2026-10-11/goal-curobov2-nvidia-service-1-service/work/official-env/bin/python3.11 -m benchmarks.attention_harness.diagnostics.reachability_map --side left --robot-config "$ROBOT_CONFIG" --assets-dir "$ASSETS_DIR" --out reachability-map-results
```

输出 `results.json` 和 `results.md`，含各姿态两种场景的成功率、可达点轴对齐范围、第 6 步对照与逐点失败原因。`without_collision` 清空**外部世界**，但 core 的自碰撞、关节限位与几何终点验收仍生效；因此空世界 IK 失败不能单独证明臂长或关节极限。带障碍失败与空世界成功支持障碍相关的进一步检查，也不构成路径可达或抓取成功证明。测试只覆盖网格、姿态、汇总和 Markdown 格式；真实 IK 留待官方环境运行。
