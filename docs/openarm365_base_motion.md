# OpenArm365 Dev v2 底座运动调研与实现报告

## A. 调研结论

1. **原生通道。** `openarm365/robot.py` 的 BASIC 复合控制器给 `base` 配 `JOINT_VELOCITY`；`openarm365/model/base.xml` 是 x/y 平移关节和 z 偏航关节，原生速度执行器范围分别为 ±0.15 m/s、±0.15 m/s、±0.3 rad/s。上游 `robosuite/controllers/parts/mobile_base/joint_vel.py` 的 `set_goal` 把车体系输入旋回初始底座轴，`run_controller` 将归一化输入乘执行器范围；其 `scaled_delta` 并未用于速度输出。因此新闭环把初始轴目标速度先按执行器范围归一化，再按当前偏航旋到控制器输入系。位置由原生 qpos 反馈，不靠试探物理碰撞。
2. **重置保持。** `openarm365_dev_v2/environment.py` 沿用旧配置中的底座速度控制器；重置和执行动作向量的底座三通道原本一直写零。双臂重置时用 JOINT_POSITION delta 零保持，重置结束转绝对目标。底座初始 qpos、场景合约和重置回执的原有 `base_mode='stationary'` 字段未改；ready 能力描述新命令模式。
3. **执行耦合。** `openarm365_dev/executor.py::action` 拼双臂目标、双夹爪目标和底座通道；`control_step` 统一调用 `env.step` 并计入同一 2400 步预算；`sim.step2` 钩子逐物理步检查手臂、底座、夹爪和接触。手臂 `apply` 通过同一 `self.goals` 驱动。底座窗口把 `self.goals` 固定在启动前实测关节，且逐物理步检查变化≤4 mrad。
4. **移动后规划。** `mount_pose(env)` 每次读取 MuJoCo 当前 `robot0_openarm_body_link0`；`world.snapshot` 每次用其逆矩阵变换世界 cuboid，并验证 chassis 几何相对 mount 的固定中心；`plan_pose` 每次把世界目标乘同一当前 mount 逆矩阵。`planner.prepare` 的 FK 对齐是当前 mount 相对 TCP 的检查，不要求底座在初始点，但其 `prepared` 集合需在底座移动后清空，以便下一次规划重新检查。轨迹 ledger 也需失效。相关变换有离线测试。

原生通道支持安全闭环的静态前置检查与实测收敛检查，因此没有触发“无法实现时停止”的条件。真实仿真器的闭环精度仍待探针验证。

## B. 实现

- `openarm365_dev/base_motion.py`：校验单步平移模长≤0.20 m、偏航绝对值≤0.3 rad；以当前 mount 为规划坐标，按≤2 cm 平移、≤0.05 rad 转角采样平移和旋转同时插值的车体路径。使用 `chassis_binding` 的盒尺寸和中心、`world.snapshot` 的世界盒；对车体盒各轴扩张 2 cm 后用 15 个分离轴检测盒相交。该扩张对欧氏 2 cm 余量保守。水平支撑地面平面免除垂直余量要求，否则原本仅离地约 6 mm 的车体无法做任何水平运动。其他障碍和物体均检查；机器人本身由快照原有排除逻辑处理。
- `openarm365_dev/executor.py`：新增 `base_move` 的校验、静态预检、80 控制步内的位置反馈闭环、两步零命令稳定、5 mm/5 mrad 到位判据、逐物理步和端点的 4 mrad 手臂保护，以及包含请求 ID、起止 sequence、碰撞结果、前后/目标位姿、误差、手臂变化和耗时的 journal/回执。移动失败锁存执行器。非底座命令窗口仍以最近成功停靠位姿执行 1 mm/1 mrad 漂移守卫。
- SDK 链：`policy_worker.py` 代理、`policy_validation.py` AST 导入白名单、`sandbox.py` RPC 与统一调用计数、`owner.py` 公开 action trace、`openarm365_dev_v2/worker.py` 原生 RPC 分发，以及 `dev_openarm_profile.py` 的策略可见语义均增加 `base.move_delta`。每次调用与手臂调用同样占一个 SDK 名额；控制步用原有 horizon 检查。
- 移动成功后清空 `planner.prepared` 并失效旧轨迹 ledger。下一次手臂请求重新上传移动后的世界快照、重新做 FK 对齐。未改 `curobo_v2/` 和任何模型、场景合约或部署文件。
- `openarm365_dev/configs/dev_counter_sink_seed101.json` 的 `dev_adapter_sources` 只更新六个改动文件和新增 `base_motion.py` 的哈希，键仍用历史 `/home/truares/桌面/Tidybot-Universe-attention-native/...` 前缀。`config.make_config()` 当前按 `resolve()` 生成 worktree 键，和该历史快照并不一致；未顺手改其他条目。

## C. 探针

`benchmarks/attention_harness/diagnostics/base_motion_probe.py` 每个试验创建一个 seed 101 的独立 Dev v2 执行、提供一次脚本策略，覆盖 +y 0.10 m、−x 0.10 m、+yaw 0.2 rad、超限 0.25 m、按第一次世界快照寻找的碰撞方向、底座移动后左臂 z 向 0.01 m。碰撞候选先用同一静态检查确认会拒绝；找不到时用最近障碍的 0.20 m 方向。逐项汇总公开结果、底座变化、到位误差、手臂变化和耗时到 `results.json` / `results.md`。

干跑：`PYTHONPATH="$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service" /home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python -m benchmarks.attention_harness.diagnostics.base_motion_probe --dry-run`

真实模拟器中运行（本次未运行）：`PYTHONPATH="$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service" /home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python -m benchmarks.attention_harness.diagnostics.base_motion_probe --out base-motion-results`

## D. 离线核验与限制

- 新增测试覆盖无碰撞、擦边、穿透、旋转扫掠、上限、守卫窗口、SDK/事件链、预算计数、journal/回执、移动后的世界与目标坐标变换、探针干跑和表格。标准环境与官方环境的新测试均为 **7 passed**。标准环境加控制器、IK 模式、原有运动诊断测试为 **63 passed**。
- 扩大运行 Dev v2 executor/SDK/policy/planner 相关旧测试得到 **20 passed、66 failed、62 errors**；首个前置失败是当前 worktree 的 `openarm_phase0_recheck/model/compat_v6/robot.urdf` 与原生模型 identity 不符/缺失。原有 `openarm365/model/robot.xml` 也在旧冻结校验中触发 identity 错误。按限制未改模型或放宽校验；这些测试结果不能解释为本次底座逻辑的回归结论。
- 当前容器的 bubblewrap 因 `NETLINK_ROUTE socket: Operation not permitted` 不能启动隔离策略进程；SDK 预算在纯函数与假 owner 链测试中验证。真实环境仍需核验原生底座控制器方向、5 mm/5 mrad 收敛、2 cm 路径预检与实际碰撞一致性、双臂 4 mrad 保持、移动后 cuRobo 规划，以及六项探针结果。上述身份问题若在运行环境仍存在，探针会在配置建立阶段报错并保留失败结果，不会绕过 identity。
