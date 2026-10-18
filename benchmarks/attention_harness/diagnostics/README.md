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

## 可移动物体接触豁免与 seed 102 抓取链路

Dev v2 冻结配置增加 `contact_exempt_objects`，取值 `none`（默认）或 `movable_objects`。诊断环境变量 `ATTN_DIAG_CONTACT_EXEMPT=none|movable_objects` 覆盖配置值；未设置时使用配置，非法值在启动规划器前报错。运行回执的 `contact_exemption.configured/source/mode` 说明配置值、来源和最终值。最终值为 `movable_objects` 时，豁免作用于 cuRobo 手臂规划世界，也让物理监控放行机器人（含手指、手掌、连杆和车体）与已登记可移动物体的接触；底座路径预检查仍使用完整世界。对象必须同时在任务的 `obj_body_id` 中、具有独立 free joint；其子 body 也属于该物体。`obj`、`distr_counter`、`distr_sink` 符合时可放行；固定柜体、水槽、台面、咖啡机、墙和地板不进入豁免集合，机器人与它们的物理接触仍中止。原有机器人自碰撞及其余关节、速度、手爪、底座和手臂监控判据不变。被夹持对象以 `entity:<body_id>:shape:<geom_id>` 名称重新进入规划世界，作为已有 `attachment.shape_names` 的附着几何；`support_names` 仍指向完整保留的支撑面。

规划器的 `world-*.json` 写出 `exempted_geoms`、`exempted_objects`、豁免前后盒数及哈希。`trajectory-*.json` 的 `contact_exemption` 和 `world_receipt.contact_exemption`、`native-actions.jsonl` 的 `contact_exemption` 事件记录实际豁免名单及两个哈希。最终豁免开启时，journal 的该事件另写 `physical_monitor_allows_movable_contacts: true`。物理日志在机器人与任务可移动物体的接触开始/结束时写 `movable_contact_begin` / `movable_contact_end`，含 `physical_step`、`robot_geom_name`、`object_body_name`、`contact_force_n`、`distance_m`。每次动作最多 200 条；多出的事件由 `movable_contact_overflow` 记录 `dropped`。豁免开启且本物理步有放行接触时，`native-physics.jsonl` 该行另写 `expected_movable_contacts`，每项为 `[机器人 geom 名, 物体 geom 名, 距离 m]`；关闭时日志行字段保持原样。

`grasp_chain_probe.py` 默认 seed 102，但必须显式给出机器人 mount 系的 `--base-shift DX,DY,DYAW`。应先从可达性扫描中选择**完整车体路径检查通过、目标中心和上方 5 cm 可达**的候选；扫描提示 DY≈0.30 m 可作为首个候选，不保证轨迹可达。命令示例（在 worktree 根目录执行，使用新输出目录）：

### Seed 102 抓取姿态诊断

`reachability_map --seed 102 --from-reset --grasp-search --exclude-object` 在宿主侧真实 reset 一次，不步进物理，写出 `fixture.json`：当前完整 `snapshot` 世界、q0、锁定关节、同一批 `obj_g*` 目标盒、目标仿真真值位姿与几何 z 范围。整体与指垫窗口分层的 5° 水平投影宽度算法直接复用 `object_proxy_measure.py`；搜索以指垫窗口内最窄方向为 θ 中心（seed 102 约 55°），同时保存整体最窄方向（约 65°）。此信息**使用仿真真值,仅诊断**，公开 observation 不含物体朝向。搜索默认扫 `dx=0;dy=0.20,0.25,0.30,0.35;dyaw=0`，每个位移试 48 个姿态和预抓取、抓取两个目标，共 384 次带碰撞 IK；目标代理盒从 IK 世界中排除，车体路径仍检查完整世界。预抓取 z 为目标顶面以上 10 cm，抓取 z 为几何 z 范围中点，水平位置均为目标 body 真值位置。TCP 局部 y 为水平闭合轴，局部 z 为向下逼近轴，局部 x=y×z；搜索世界 XY 最窄角 ±15°、闭合轴正反号、俯仰 0/20/40/60° 和水平垂直方向正反号。`results.json` 保存全部组合的双目标结果，`results.md` 列出车体路径和双目标都通过、优先俯仰角小及底座位移小的前十个世界系 xyzw 四元数。姿态约定由真实 TCP FK 和手指几何测量确认；IK 使用服务默认 4 mm、0.03 rad 容差。`--dry-run` 只显示目标数和估时。

复现扫描可改用 `--grasp-search --fixture <此前输出>/fixture.json --exclude-object`，无需再次 reset；可选 `--seed` 必须与夹具记录一致。
分层最窄值是窗口内碰撞顶点的投影，seed 102 的 55° 最窄层靠近物体顶端；它只决定候选方向，不证明指垫接触或抓取成功。

```bash
PYTHONPATH="$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service" /home/truares/Documents/Codex/2026-10-11/goal-curobov2-nvidia-service-1-service/work/official-env/bin/python3.11 -m benchmarks.attention_harness.diagnostics.reachability_map --seed 102 --from-reset --grasp-search --exclude-object --side left --robot-config /home/truares/Documents/Codex/2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-03/runtime/project/config/openarm_fixed.yml --assets-dir /home/truares/Documents/Codex/2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-03/runtime/project/curobo_v2/service/curobo_service/assets --out outputs/grasp-reach-seed102
```

`grasp_chain_probe --grasp-quat x,y,z,w --oracle-from-reset` 使用搜索得到的世界系 TCP 四元数，在启动策略前宿主侧只读 reset 一次，将目标 body 真值位置和几何 z 范围中点作为策略常量注入，输出明确标注**使用仿真真值,仅诊断**。`--sequence rotate-first` 是默认值，保持原策略源码逐字节相同：底座侧移、张手、原地分步旋转、平移到预抓取、下探、闭爪、抬起。`--sequence translate-first` 在张手后保持初始姿态，先以小于 5 cm 的步骤平移到目标顶面上方 10 cm，再于该位置分步旋转；旋转命令的平移量只用于抵消观测到的 TCP 漂移，每步后重新观测。`--sequence combined` 将到预抓取位置和目标姿态的距离拆为 `N=max(1,ceil(distance/0.045),ceil(max_abs_rotvec_coordinate/0.45))` 步，每步同时平移和旋转，并用新观测重算剩余量。三种序列随后均下探、闭爪、竖直抬起。报告另列每个手臂动作的步号、平移/旋转增量、公开结果、失败原因、证书查询数/耗时与该步目标 TCP 世界位姿，以及闭合轴与世界水平面夹角、两指开度。无 `--oracle-from-reset` 时仍可用公开 `find_objects` 的中心位置做位置目标，但无真值 z 范围。

持握稳定性诊断：设置 `ATTN_DIAG_HELD_TRACE=1` 后，有双指夹持或 `self.held` 的 `native-physics.jsonl` 行增加 `held_trace`，逐物体记录 `object_body`、TCP 系相对位置 `rel_pos_tcp_mm`、相对首次双指接触参考的 `rel_rot_deg`、首次记录的 4×4 `reference`、两指接触力 `finger_contact_force_n` 和接触距离 `finger_object_distances_mm`。未设置时不增加任何字段；其他值报错。监控原有 4 mm / 0.03 rad 判据不变，开启诊断时越界行也会写出。`held_transform` 的默认 `fixed` 模式在闭爪固定 20 步、确认至少连续 12 步双侧接触后建立；`settled` 模式再等物体相对 TCP 的相邻步位移 ≤ 0.2 mm、转角 ≤ 0.05° 连续 10 步，最多额外 60 步。超时取当时参考并记录不稳定，不放宽监控或附着数值。`ATTN_DIAG_HELD_REFERENCE_MODE` 覆盖 Dev v2 冻结配置 `held_reference_mode`（默认 `fixed`）；gripper 回执记录配置值、来源、最终值及等待步数，`held_reference` journal 事件记录稳定性和参考位姿。轨迹首次记录参考与监控参考分别保存，以定位闭爪阶段的挤动和抬起阶段的滑动。

探针支持 `--theta-deg D`（与 `--grasp-quat` 互斥，xyzw = `(sin((90-D)/2),cos((90-D)/2),0,0)`，角度按度转弧度）、`--grasp-offset-mm X`（沿四元数局部 x 轴的水平投影）、`--grasp-z-offset-mm Z`、`--lift-step-m S`（默认 0.025）和 `--lift-height-m H`（默认 0.10）。零偏移及默认抬起值不改变生成策略源码。`results.json` 另有最终观测相对初始观测的 `object_lift_cm`、`aborted_by`、`monitor_aborted`、闭爪后开度与物体相对 EEF 的逐抬起动作位移及最大滑动量；即使监控中止，也从 `final_public_observation.json` 读取最终高度。未产生双侧夹持轨迹时，滑动轨迹可能为空。`--close-hold-steps 0` 是唯一可用值：本 dev SDK 的 `left_gripper.close()` 无参数，worker 拒绝手爪参数，固定执行 20 步，白名单也没有等待操作；精确 N 步保持须另行增加受限 SDK 操作及原生零命令控制步实现，当前没有把重复闭爪冒充保持。

GPU 机器上串行运行独立变体（下例全为可运行的 `hold=0`）：

```bash
PYTHONPATH="$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service" /home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python -m benchmarks.attention_harness.diagnostics.grasp_variants --seed 102 --base-shift 0,0.25,0 --sequence translate-first --rotation-step 0.15 --variants 'base55:theta=55,hold=0,offset=0,z=0,lift=0.025;angle70:theta=70,hold=0,offset=0,z=0,lift=0.025;shift5:theta=55,hold=0,offset=5,z=0,lift=0.025' --out grasp-variants-102
```

脚本逐变体传入 `ATTN_PLANNER_MODE=ik_path`、`ATTN_IK_CANDIDATES=8`、`ATTN_IK_MAX_JOINT_JUMP=1.0`、`ATTN_DIAG_HELD_TRACE=1`；`grasp_chain_probe` 和 `grasp_variants` 可选 `--held-reference-mode fixed|settled`，仅显式传入时设置 `ATTN_DIAG_HELD_REFERENCE_MODE`，默认生成策略源码不变。`--dry-run` 仅生成策略，`--only name1,name2` 只选择指定变体。矩阵结果写入输出目录的 `results.json` 和 `results.md`，每个变体另有独立执行目录。

`--rotation-step RAD` 控制三种序列的每坐标旋转步长，取值须为有限的 `(0, 0.5]` rad，默认 `0.45`，此时生成的策略源码逐字节保持原样。指定非默认值时，步数以 `0.9×RAD` 计算，指令上限仍为 `RAD`；每次旋转重新观测残差，主循环最多多走 3 步，残差小于 0.01 rad 时提前结束，超过 0.02 rad 时最多再做 3 步限幅修正，仍未收敛则报告残差并失败。seed 102 的 translate-first 实测中，预抓取的 4 步平移全部成功，水平误差约 0.2 mm；随后约 0.48 rad 的旋转步分别消耗 5.5 s（9425 次证书查询）和 7.7 s（12089 次查询），第二次所在的 `/plan/ik_path` 请求超过客户端每请求 8 s 上限而超时。较大旋转会扩大扫掠体积并增加证书耗时；可用 `--rotation-step 0.15` 增加步数，降低单步旋转幅度，但总 SDK 调用数也会增加。SDK 上限为 80 次；带 `--dry-run-state` 时，dry-run 按约 4 步下探估计各序列的调用数，估计值超过 70 即警告。估计值不能替代真实执行时的调用预算和证书耗时检查。

`--dry-run` 不 reset，也不启动仿真；它按所选 `--rotation-step` 打印三种序列的初始步数公式。确切步数依赖侧移并张手后的公开 TCP 位姿和目标位置；非默认步长下的额外收敛步数只能在执行中确定。已有这些数据时，可以额外传 `--dry-run-state path.json`，内容为 `{"eef_position":[x,y,z],"eef_quat":[x,y,z,w],"pregrasp_position":[x,y,z]}`；输出三种序列的初始数字步数和 SDK 调用数估计，且仍不启动仿真。详见 [抓取序列说明](../../../docs/grasp_chain_sequences.md)。

对称误差规则：最终豁免为 `movable_objects` 且该侧手指 geom 在本物理步接触可移动物体时，该侧 `GRIPPER_MIMIC_CONTACT_BOUND_M=0.010` m；单指行程 44 mm，一侧受物体顶开时允许最多 10 mm 暂态失配。其他时刻及另一侧仍为原 1 mm。仅放宽生效的 `native-physics` 行添加 `mimic_error_m` 和 `mimic_bound_m`；位置、速度、限位等原检查不变。

```bash
PYTHONPATH="$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service" \
  /home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python -m \
  benchmarks.attention_harness.diagnostics.grasp_chain_probe \
  --seed 102 --base-shift 0,0.30,0 --exempt movable_objects \
  --out outputs/grasp-chain-seed102
```

先用 `--dry-run` 打印策略，完全不启动模拟器。真实运行由用户显式执行，写 `results.json` 和 `results.md`；按公开动作回执分阶段列出底座、张爪、预抓取、下探、闭爪、抬升和物体上升判定，并汇总 `continuous-*.json` 的认证次数/耗时及首个失败。每段 WORLD 位移拆为严格小于 0.05 m 的 SDK 调用；底座位移拆为每次不超过 0.20 m、0.30 rad，并按 SE(2) 合成保持目标终点。`--tilt` 在底座动作前用两次 WORLD y 轴各 -π/8 rad 转动，合计绕初始机器人 +y 轴约 45°；注册 seed 的初始 mount yaw 为 π。默认保持初始末端姿态。`--tilt` 可用 `ATTN_PLANNER_MODE=plan` 或 `ATTN_PLANNER_MODE=ik_path`；真实探针中 `plan` 模式的旋转规划曾触及客户端 8 s 上限而超时，`ik_path` 仍须通过 IK、关节跳变及认证检查。

### `ik_path` 模式下的旋转语义

`ik_path` 模式下 `move_delta(dx,dy,dz, rotation_delta)` 保证的是末端目标**终点位姿**：服务端对位置误差使用 ≤4 mm、姿态误差使用 ≤0.03 rad 的校验，并对实际关节空间五次多项式路径做连续几何认证。它不保证末端走直线；只平移时也不保证直线。旋转不会绕过原有碰撞、关节跳变或身份检查。

诊断变量 `ATTN_IK_MAX_JOINT_JUMP` 只在 `ATTN_PLANNER_MODE=ik_path` 时有效；例如 `ATTN_IK_MAX_JOINT_JUMP=0.8` 将该请求的 IK 候选单步最大关节变化阈值设为 0.8 rad。未设置时请求体不发送此字段，由服务端沿用默认 0.35 rad；设置后必须是正的有限小数，且不超过 2.0 rad，非法值或在 `plan` 模式下使用会直接报错。`trajectory-*.json` 和执行日志 `native-actions.jsonl` 的 `intent` 记录实际阈值及 `default`/`override` 来源。此阈值仅用于过滤 IK 候选间的大跳变；整条关节路径仍须通过连续几何证书，速度、加速度和加加速度边界不变。

seed 102 reset 实测：`robot0_left_eef_pos ≈ [0.411192,-2.279444,1.089394]` m，`obj_pos ≈ [0.496910,-2.377295,0.934829]` m；两者均为公开 WORLD 坐标。`mount_pose` 平移约 `[0.812481,-1.767212,0.600000]` m、旋转约 `Rz(π)`；世界点到机器人系为 `p_robot=R_mountᵀ(p_world-t_mount)`，反向为 `p_world=R_mount p_robot+t_mount`。公开 observation 暂无 base pose 字段，探针只用 WORLD 末端/物体位置算 WORLD 手臂位移；底座命令直接用给定机器人系位移。

当前 `wt-exempt` 工作树的已冻结 `openarm365/model/identity.json` 还要求本地副本 `openarm_phase0_recheck/model/compat_v6/robot.urdf`，但该文件在这个工作树中缺失。因此本沙箱内的 `make_lock` 会按原有身份检查报 `native robot model identity drift`，真实探针会在创建实验时停止。运行前需要按原身份清单恢复工作树的完整模型副本；探针不会跳过或放宽该检查。本阶段未启动 worker、服务、GPU 或物理动作。

### 持物搬运诊断（可选）

`grasp_chain_probe --carry --carry-base-shift auto --oracle-from-reset` 在既有抓取和 10 cm 抬起之后追加 `carry_lift`、`carry_base`、`carry_xy`、`lower`、`release`、`retreat`、`observe`。`--carry` 默认关闭；关闭时生成策略与此前逐字节一致。也可指定 `--carry-base-shift DX,DY,DYAW`（机器人 mount 系）。`auto` 把 reset 的 `sink_pos−banana_pos` 转为抓取后 mount 系位移；`base.move_delta` 拆分为每次平移 ≤0.20 m、转角 ≤0.30 rad 并保持 SE(2) 终点。搬运升到水槽边沿顶面上方 0.20 m，水平移到水槽目标中心，降到边沿上方 0.08 m，松爪后上抬 0.10 m。所有新增臂动作使用原 SDK、规划证书和物理监控；手臂水平步长 <0.05 m，竖直升/降每步 <0.03 m 或 ≤0.03 m。

`--carry --oracle-from-reset --dry-run --dry-run-reset` 只做 reset，不启动物理步进，打印水槽边沿高度、来源、`sink_pos`、auto 位移、香蕉抓取时与水槽移位后的 mount 相对位置，以及待执行策略。边沿高度从 reset 原生 MuJoCo 水槽 main 固定碰撞盒的最大 z 计算；没有对应盒时退回 `sink_pos` 的 z 并明确标明。这些几何均标注“使用仿真真值,仅诊断”。正常 `--dry-run` 行为不变。真实执行目录的 `native_result.json` 被原样读取到 `results.json` 的 `native_success` 和 `native_status`；`object_final_pos` 来自最终公开观测，`object_in_sink` 只按水槽碰撞盒 XY 投影与低于边沿的 z 做启发式判定，不代替原生评估器。

```bash
PYTHONPATH="$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service" \
  XDG_CACHE_HOME=/tmp/attn-carry-cache NUMBA_CACHE_DIR=/tmp/attn-carry-numba \
  /home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python -m \
  benchmarks.attention_harness.diagnostics.grasp_chain_probe \
  --seed 102 --base-shift 0,0.25,0 \
  --grasp-quat 0.3007057995042732,0.9537169507482269,0,0 \
  --oracle-from-reset --sequence translate-first --rotation-step 0.15 \
  --exempt movable_objects --grasp-z-offset-mm 5 --lift-step-m 0.025 \
  --carry --carry-base-shift auto --dry-run --dry-run-reset
```

seed 102 只读 reset 实测：`sink_pos=[0.2999998883,-1.7962400050,0.9861874579]` m；main 固定碰撞盒 13 个，边沿顶面 `1.0186950289` m；`auto=(+0.1969099291,-0.5810548409,0)`。mount 旋转为 `Rz(π)`，所以世界系 `sink−banana=(-0.1969099291,+0.5810548409)` m 映到 mount 系后符号翻转。抓取时香蕉与移位后水槽的 mount XY 均为 `[0.3155711446,0.3600824950]` m。以上仅为只读可达性自检，未执行新增物理阶段或获得原生成功结果。

## 抓取链诊断任务预算

`grasp_chain_probe` 和 `grasp_variants` 可选 `--budget-scale S`，其中 S 为有限的 1–8 倍正数。例如在 seed 102 上使用 `--budget-scale 2` 时，单次上限为 160 次 SDK 调用、4800 控制步和 960 秒。此参数只供脚本化诊断探针使用，命令会设置 `ATTN_DIAG_BUDGET_SCALE` 和探针标记；直接在普通 Dev 或正式运行中设置该环境变量会被拒绝。非法值报错；浮点倍数的整数上限向上取整。未传参数时沿用原 80/2400/480、原预算文件和策略源码。

放大由 `openarm365_dev_v2/diag_budget.py` 统一计算。诊断实验的 `budget.json`、账本上限和执行预留同时放大；例如 2 倍时诊断账本单次预留 160 次调用、4800 步、1080 秒（含放大的 120 秒启动清理预留），wall limit 为 8520 秒。生成策略的搬运段硬上限随之变为 160，抓取段保留 5 次调用余量，变为 155。`results.json`、runner 结果、sandbox/native 回执、worker RPC 回执和 reset 回执记录 `diagnostic_budget_override`。放大执行的账本回执标为 `diagnostic`，有效执行次数统计排除它，正式边界拒绝它。

这只放大任务资源上限。证书、逐物理步监控、关节与速度限制、接触检查、身份和哈希校验、沙箱代码白名单均未更改。探针宿主脚本是普通 Python；它生成的 SDK 策略仍须通过 `policy_validation` 和沙箱白名单，因此不能使用任意 import、NumPy 或未允许的内置函数。`--dry-run` 不执行物理步进；`--carry --dry-run` 需要 `--dry-run-reset`，会做只读 reset。
