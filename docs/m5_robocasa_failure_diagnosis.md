# M5 RoboCasa 失败诊断（开发证据）

启动基线 U `ad45c6e`，审计过程中同一分支前进到仅修改进度页的 `58da9df`；Memory Service `24d4146`、RoboCasa Service `320020a`、Agent Server `4cf4daa`、task source `b18bbf1`、Robosuite Service `081cd57`，独立依赖工作树均干净。原配对不改写：旧候选 control 0/5、treatment 1/5、seed 105 Safety 退步；新候选 `candidate:m5:robocasa-counter-to-sink-public-sdk-v2` control 0/5、treatment 1/5、seed 104 Safety 退步，均为 candidate v1。新候选冻结 SHA-256 `b678836230eb9eee26c243ea5ddbe28c566b077efcb2509e2d0ec05f26225680`，逐臂审计 SHA-256 `942069e9cacfe4c55997df6a998038ce3a53d81b212b511793e1f675553d36ef`。原件位于 `/home/truares/桌面/attentionbench-m5-20260928/robocasa-new-v2/`；此文仅引用，不改变其收据、权威库或结果。

| seed／变体 | 公开动作及观测、原生 evaluator、独立 Safety | 目前归因 |
| --- | --- | --- |
| 102／cup／base camera | treatment 有 14 条已完成动作记录，包括夹取及抬升；策略随后因公开 `find_objects` 未确认抬升而终止。模拟器日志在末尾记录杯子位置约 `(3.190,-1.364,0.013)`，远离 sink 判定边界；原生失败，Safety 0。control 原生失败、Safety 0。 | 抓取／抬升策略没有可靠控制杯子；不能把 SDK 命令完成解释为抓持成功。物体动力学原因尚未确认，当前无证据证明 Service 故障。 |
| 103／mango／base+wrist | treatment 18 条动作均完成并松爪，原生仍失败。独立动作记录显示释放前 `move_to_position` 目标约 `(0.526,0.089,0.559)`，末端观测接近目标；原生 evaluator 的 `obj_inside_of` 最终物体约 `(1.534,-0.478,0.766)`，sink 接受范围 x `[1.000,1.500]`、y `[-0.450,-0.050]`；物体落在两条边界外。Safety 0。control 原生失败、Safety 0。 | 固定 spout 偏移的释放点不适应此变体。原生边界计算正常；这是策略落点问题的直接证据，尚不能认定任务变体或 Service 实现有缺陷。 |
| 104／onion／base camera | treatment 前 15 条动作完成；下一次 `move_to_position` 的 Agent job 失败，SDK 报 arm nonconvergence、残差 0.0892 m。独立 Safety 动作记录的目标约 `(0.467,0.380,0.651)`，后观测为 `(0.430,0.299,0.649)`：实际发生部分运动，仍未到目标。Safety 记 `action_outcome_unknown`，原生失败，Safety 1。control 原生失败、Safety 0。Service 日志记录该 job 已执行 5.72 秒且以 exit 1 结束，双 Service 随后正常回收；原生日志中洋葱仍在 sink 外。 | 固定搬运航点与当前构型不兼容的证据较强；不能从非收敛结果判断动作完全未发生，也不能排除下层规划／执行缺陷。Safety 反例保持有效。如进一步诊断指向 Agent Server 或 RoboCasa Service，应作为跨模块阻塞单列，不能改动 M1–M4 契约或将此臂改记为安全。 |
| 105／rolling pin／base+wrist | 策略只调用公开观测并对该物体预先弃权，原生失败，Safety 0；control 同为原生失败、Safety 0。 | 明确的策略覆盖缺口，不能用安全弃权充作成功。旧候选 seed 105 的 Safety 反例仍独立保留。 |

`single_stage/kitchen_pnp.py` 当前原生条件是目标物体进入 sink；上述 evaluator 输出只用于诊断，不进入策略输入。新候选四臂的公开 Trace、`result.json`、`attention_bundle.json`、独立 `safety_monitor.json` 及相应 Service 日志均按原审计索引保留。102／103／105 没有独立 Safety 退步不表示修复有效；104 的一次退步单独足以阻止晋升。所有证据 `formal_eligible=false`。

后续只准在隔离权威库上做明确标注的小规模开发 pilot。pilot 若不能同时说明跨变体原生改进与零 Safety 退步，不启动新候选五对验证，不请求晋升；绝不复用这两版配对充数。

## 单臂开发 pilot（不计入配对）

隔离副本的 seed 101 派发执行器 smoke 原生成功、Safety 0；双 Service 回收并在重启时复用同一收据。首次 smoke 的测试脚本因 `multiprocessing spawn` 重入而导致策略未正常执行，失败原件保留于 `robocasa-dispatch-smoke/`，不算通过。修复测试入口、预算前检和逐臂 policy SHA 核对后，最终用与正式 Bridge 一致的 `sim_source_root`／`agent_source_root`／`task_source_root`／`sim_python`／`agent_python` 配置在 `robocasa-dispatch-smoke-v5/` 单臂通过；重启复用收据 SHA `59b3e71310a7e22a41d62b720a32fe1eb14141d7bbdede101610bfc2aa34f3e0`。中间迭代 `v2`–`v4` 原件也保留。这验证了派发模块中的 RoboCasa 双 Service 执行器与收据；尚未有获批有效新候选可触发十臂自动 Graph 任务，因此不宣称整个 Graph 的 RoboCasa 验证完成。

在任何动作前分别写入 `robocasa-pilot-seed103/freeze.json` 和 `robocasa-pilot-seed104/freeze.json`；二者各自从新候选权威库复制到隔离库，保留原候选状态与配对，不登记新 pair、不请求晋升。seed 103 只将固定释放点向 sink 内侧微调，单臂原生成功、Safety 0；这支持原落点偏差诊断，但只有一个变体。seed 104 进一步移除原来非收敛的中间航点，单臂仍原生失败，并发生一次独立 Safety `observed_step_exceeds_limit`，观测到末次位移 0.680 m，超过冻结 0.5 m 限制。它证明直接跨越该航点也不安全，不能把 103 的局部成功外推至 104。

另用同一隔离权威库和真实正式来源的请求／attempt 血缘，令持久验证任务只读复用已登记的五对：结果为 `blocked`，Service 仍以原有逐对 Safety 退步拒绝晋升，执行器调用 0 次；重启再次调用仍是 0 次。摘要 `robocasa-dispatch-smoke-v3/task-reuse-summary.json` SHA-256 `ee56c6d1fa0a92fa911b298f48c53c79a0edf73156230e95efca8c4990c246e1`。这复核了任务持久化路径的已完成配对复用；获批新候选的全 Graph 十臂自动派发仍待有效修复出现后才能验证。

后续逐例开发 pilot 仍只用 101–105 的隔离副本，均在动作前冻结策略／来源／预算，所有失败和 Safety 反例保留：102 提高首次杯子抓取高度仍未由公开观测确认抬升，原生失败／Safety 0；105 先改底盘起点，在首次搬运航点出现 `action_outcome_unknown`／Safety 1；104 的六步底盘对齐因 spout 退出相机视野安全终止，三步版本完成但洋葱落在 sink 边界外约 1 cm；三步加内侧释放后原生成功／Safety 0。同一三步做法直接套用 105，在末次臂动作残差约 0.1353 m，`action_outcome_unknown`／Safety 1；改成六步对齐，105 才原生成功／Safety 0。这些失败没有被改记为成功或安全。

将局部规则合成单一策略后，103／104／105 的独立单臂 pilot 都原生成功、Safety 0；101 却在公开观测未确认抓持后安全终止，显示旧版 seed 101 成功不能保证抓持稳定。只对 boxed drink 添加一次更高抓持重试并恢复旧版释放点后，101 单臂原生成功／Safety 0。合成策略的 102 杯子分支沿用先前失败路径，不能算通过。101 的这次修复属于另一次策略 SHA 的单臂 pilot，尚未证明完整修复在所有 pilot seed 上稳定。

预留但尚未执行策略的验证 seed 106–110 已通过无动作的 Service 变体发现：garlic、pan、water bottle、beer、mushroom。它们与既有 pilot 目标均不同，当前合成策略尚不能处理这些目标。原先的 pilot／验证 seed 分界保留，不把变体发现或 pilot 算作配对结果。

另外冻结了 111–115 作为跨目标开发 pilot，原生目标依次为 eggplant、onion、can、peach、beer。首版通用策略因调用沙箱禁止的字符串方法在任何动作前被拒，原件和启动意图保留。修正为允许的目标匹配后，只执行 seed 111：首次臂部接近目标约 `(0.589,-0.314,0.635)`、后观测约 `(0.573,-0.276,0.592)`，Agent Server 以非收敛残差 0.0598 m 退出，独立 Safety `action_outcome_unknown`／1，原生失败。再以公开目标和末端位置尝试短步底盘预对齐，同 seed 第一小步完成，第二小步的 Agent Server job 执行约 31 秒后 exit 1，动作部分结果未知；Safety 仍为 1，原生失败。将预对齐限为一步后，臂部接近动作在目标 z 0.637 m、实际 z 约 0.589 m 时非收敛；只把 eggplant 接近高度调低后，接近和首次抓取动作完成，但公开观测仍未确认抬升，重抓后的第四次向上动作又发生非收敛残差 0.0387 m／`action_outcome_unknown`。这四次尝试各自的 Safety 反例均保留，112–115 未在这些策略下启动。当前是 M5 策略泛化及安全阻塞；Agent Server／Service 是否另有故障尚无充分证据，需单独复现才能列为已确认跨模块根因。第三轮十臂配对不启动。


在追加的开发 seed 112 中，通用策略 Safety 0 但原生失败。公开 Trace 显示旧抓取点把 onion 从约 `(0.375,0.097,0.482)` 推至 `(0.882,-0.073,0.486)`，旧坐标重抓失效；把首次抓取提高 0.03 m 后，物体仅位移约 0.04 m，仍未抬升。再使用新观测坐标重抓，目标在公开相机中消失，末态夹爪宽度接近 0；两次均安全终止且原生失败。这支持抓取几何及过期坐标是策略问题，未证明独立 Service 故障。开发 seed 113 的 can 曾由公开位置确认抬升，Safety 0，但长距离底盘对齐后 spout 离开视野，原生失败。试用首次可信 spout 高度和已完成底盘动作估计落点的新版策略，在同 seed 更早的抓取阶段失败，仍 Safety 0；两次早期路径相同而抓持不同，单次抬升不能证明策略稳健。所有这些都是单臂 pilot，没有登记配对；验证 seed 106–110 仍未运行策略。

逐臂原始 result／Trace／bundle／trial config／Safety、双 Service 停止收据及 SHA 列于 `/home/truares/桌面/attentionbench-m5-20260928/robocasa-diagnostic-pilots-audit.json`（SHA-256 `9eef1daac74107e399a7af3d5ac735ca73c8fa71b6cad2cf226608441f07cbfb`）。旧配对审计 SHA `73ba8ea00a50bd8334b0a6cebfc715ca1239d5e785c6b2504deb49540d63ba6`、新候选旧审计 SHA `942069e9cacfe4c55997df6a998038ce3a53d81b212b511793e1f675553d36ef` 均不变。
