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

### 底座位移与小网格扫掠

`--base-shift DX,DY,DYAW` 评估一个位移；默认 `0,0,0` 保留原有完整网格和结果结构。DX、DY 是**当前 mount 底座系**的平移（m），DYAW 是绕 mount 原点的偏航（rad）。`base_motion.py` 中车体碰撞盒的中心相对 mount 为 `[-0.198,0,-0.291]` m；旋转枢轴不是车体盒中心。固定世界点与障碍盒先按 `p_new = Rz(-DYAW) × (p_old - [DX,DY,0])` 变换，盒方向左乘逆偏航四元数。牛奶位置代理和第 6 步目标采用同样的逆变换；`q0`、锁定关节、初始 FK 和机器人系候选姿态保持原值。空世界与完整世界 IK 都使用位移后的同一组目标。

`--sweep` 默认按 DX=`0,-0.1`，DY=`0,0.05,0.10,0.15,0.20,0.30,0.40`，DYAW=`0,0.15` 的笛卡尔积评估 28 组。`--sweep-spec 'dx=0,-0.1;dy=0,0.05;dyaw=0,0.15'` 可覆盖各轴。每组仅评估牛奶代理中心周围 x/y 各 ±4 cm、步长 4 cm，z 为 0/5/10 cm 的 27 点，13 姿态，两种碰撞模式；不含第 6 步。共 `28×27×13×2=19,656` 次 IK。按单目标 0.15–0.20 秒估算，纯 IK 约 **49 分 8 秒至 65 分 31 秒**，还需加模型初始化、世界上传及输出时间。`--dry-run --sweep` 无需模型或 GPU，会输出目标数和估计秒数。

每组用夹具的完整 cuboid 世界及与 `test_base_motion.py` 相同的 inventory 结构调用 `check_chassis_path`。平移模长 ≤0.20 m 且偏航绝对值 ≤0.30 rad 的位移标为单步；更远位移沿直线和线性偏航插值，拆为满足 SDK 单步限额的小段，逐段检查，并对最终底座姿态再做零位移碰撞检查。多步标记表示理论上需要多次 `base.move_delta`，不是已在模拟器执行。汇总表给出每组带碰撞最佳姿态的成功格点数、中心和上方 5 cm 是否在任一姿态可达、路径/终点检查和单步状态。建议只从**中心与上方 5 cm 均带碰撞 IK 成功、路径及终点均通过**的样本中，按平移模长、再按偏航绝对值选最小位移；这是扫描数据排序，不代表抓取或运动执行成功。

正式扫掠命令（请为每次运行选新的 `--out`）：

```bash
PYTHONPATH="$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service" /home/truares/Documents/Codex/2026-10-11/goal-curobov2-nvidia-service-1-service/work/official-env/bin/python3.11 -m benchmarks.attention_harness.diagnostics.reachability_map --sweep --side left --robot-config /home/truares/Documents/Codex/2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-03/runtime/project/config/openarm_fixed.yml --assets-dir /home/truares/Documents/Codex/2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-03/runtime/project/curobo_v2/service/curobo_service/assets --out reachability-sweep-results
```

结果写入 `results.json`、`results.md`。当前仅做过纯数据测试及夹具 CPU 路径检查；没有执行真实 IK 或底座动作，因此没有实测最小可达位移。

### 排除目标物体代理盒

`--exclude-object` 只从带碰撞 IK 的外部世界去掉用于定位牛奶位置代理的 `obj_g*` 碰撞盒（seed 101 夹具为 16 个）；桌面等其余障碍、空世界对照及车体路径/终点检查保持原样。`results.json` 记录 `excluded_object.count` 和 `excluded_object.names`，`results.md` 标题标为“已排除目标物体”。单次扫描与 `--sweep` 均支持；不加此开关时保持原结果结构。`--dry-run --exclude-object` 会显示排除数量。该对照用于检验目标物体自碰撞是否造成中心高度 IK 失败，不代表抓取动作或完整路径可行。

针对 DY 0.2/0.3/0.4 m 的正式扫掠命令（在 worktree 根目录执行）：

```bash
PYTHONPATH="$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service" /home/truares/Documents/Codex/2026-10-11/goal-curobov2-nvidia-service-1-service/work/official-env/bin/python3.11 -m benchmarks.attention_harness.diagnostics.reachability_map --sweep --sweep-spec 'dx=0;dy=0.2,0.3,0.4;dyaw=0' --exclude-object --side left --robot-config /home/truares/Documents/Codex/2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-03/runtime/project/config/openarm_fixed.yml --assets-dir /home/truares/Documents/Codex/2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-03/runtime/project/curobo_v2/service/curobo_service/assets --out outputs/reach-sweep-exclude-object-20261017
```

### 牛奶代理缩放与真实几何测量（seed 101）

`reachability_map --object-scale S` 仅把带碰撞 IK 世界中与 `--exclude-object` 相同的 16 个 `obj_g*` 盒的 `half_size` 按比例乘以 S，保持盒中心和方向不变。空世界对照、车体路径检查和其他障碍不缩放。S 必须为正有限数；显式给出 `--object-scale` 与 `--exclude-object` 会报错。默认 S=1.0 保持既有世界对象、结果 JSON 结构和 Markdown 内容不变；显式传参会在报告标题标注 S。`--sweep` 和 `--base-shift` 都支持缩放。

在 worktree 根目录，用官方 cuRobo 解释器逐个运行 S=`1.0/0.9/0.8/0.7/0.6/0.5`；每次使用不同的 `--out`，例如 S=0.9：

```bash
PYTHONPATH="$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service" /home/truares/Documents/Codex/2026-10-11/goal-curobov2-nvidia-service-1-service/work/official-env/bin/python3.11 -m benchmarks.attention_harness.diagnostics.reachability_map --sweep --sweep-spec 'dx=0;dy=0.3;dyaw=0' --object-scale 0.9 --side left --robot-config /home/truares/Documents/Codex/2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-03/runtime/project/config/openarm_fixed.yml --assets-dir /home/truares/Documents/Codex/2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-03/runtime/project/curobo_v2/service/curobo_service/assets --out outputs/reach-scale-0.9-20261017
```

`object_proxy_measure.py` 使用 Dev v2 worker 的 `service_env` Python，先执行与 worker 相同的官方环境身份核验，再以 `PickPlaceCounterToSink`、seed 101、pretrain、layout 11/style 34、scene reset seed 101 创建并 reset 环境。它不启动策略或规划器；除 reset 自身外不调用 `mj_step`。脚本直接读出 reset 时的两指实际关节位置和间距；为了只读比较张开/闭合姿态，它另复制 reset 状态的 `MjData`，把两个独立指关节分别设为 0.043 m 与 0.001 m，并只调用 `mj_forward`。这两种指定开口是**运动学姿态的几何测量**，不是施加开合命令后的动力学实测。

```bash
PYTHONPATH="$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service" /home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python -m benchmarks.attention_harness.diagnostics.object_proxy_measure --out outputs/object-proxy-measure-20261017
```

`results.json` 列出目标物体每个真实碰撞 geom 的类型、尺寸、世界姿态；mesh 的凸包体积及轴对齐/定向盒体积；全部 geom 合并后的凸包与数值最小定向盒；16 个代理盒的世界姿态、尺寸、体积以及代理/真实比值。盒体积**求和会重复计算重叠**，不等于严格并集体积。曲面 geom 用有限表面采样近似整体凸包；定向盒由凸包面/边候选加局部数值优化得到。报告还列出双指间距、指垫厚度、夹持轴、水平每 15° 的牛奶投影宽度与余量、forward_down_45 的估计方向，以及所用机器人配置里的左指碰撞球和指尖球包络开口估计。球的开口估计使用指垫远端三分之一的球和配置的 buffer，仅用于比较规划几何与实体几何；无法代替接触仿真或抓取成功判定。

测量脚本的 `--dry-run` 只打印参数与零步进约束，不加载 MuJoCo。若官方模型/资产 identity 核验在当前沙箱失败，脚本原样报错，不跳过身份检查；需要在与 Dev v2 worker 相同的已认证环境运行正式测量。
