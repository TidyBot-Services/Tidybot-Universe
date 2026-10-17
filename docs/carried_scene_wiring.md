# 携带物体代理碰撞场景接线

## 调研

- **球索引**：`SingleArmAttachment.spheres` 是 256 槽的本地容量，只有前 `sphere_count` 个球有效，其余半径为 -100；`configure_carried_body` 只把有效球写入 `kinematics.collision_spheres[attachment.link]`。官方 loader 按每个 collision link 的配置球数建立 `link_sphere_idx_map`，`KinematicsParams.get_sphere_index_from_link_name(link)` 从全局球列表取该 link 的槽位，`total_spheres` 还包含其他 link 的球。官方 `extra_collision_spheres` 也可能建立 -100 半径的预留槽。原接线把 link 槽数与本地容量 256 比较，真实 54 球附着因此被误拒。现检查该 link 的正半径球正好占声明的前 `sphere_count` 个映射槽位，预留槽保持非正半径，索引唯一且在全局边界内；再复核 planner/checker 的有效索引集合及总槽数。`MeasuredKinematicsInputs.prepare` 对重新测量的球维持自己的完整映射检查。
- **场景创建**：`MotionPlannerCfg.create` 产生 `SceneCollisionCfg`；官方 `MotionPlanner._initialize_components` 用固定的 `create_scene_collision` 工厂造一个场景，传给 IK、轨迹优化和可选图规划器。配置没有自定义场景类入口。此处仅在附着且 supports 非空时用服务端 `MotionPlanner` 子类，在首次 warmup 前把 `CarriedSceneCollision` 传给官方求解器。`RobotCollisionCheckerCfg.load_from_config` 有 `scene_collision_checker` 参数，因此验证 checker 可直接接收另一个预建的分组场景。无附着或空 supports 保留原构造路径。
- **接口核对**：官方 `SceneCollision.load_collision_model(scene_model, env_idx=0)` 与覆盖方法签名一致；`get_sphere_distance`、`get_sphere_collision`、`get_swept_sphere_distance`、`get_swept_sphere_collision` 四种查询各自转发给 `CollisionChecker`，均接受 `scene`、`query_sphere`、`collision_buffer` 等参数。`CollisionBuffer.from_shape/resize` 可供分组查询使用。旧的 `CarriedSceneCollision` 把 collision 查询别名为 distance，还错误要求重挂的物体名出现在静态场景中；现已修正。CUDA 图的实际捕获与梯度尚需真实 GPU 验证。

## 安全边界

`_scene()` 已把附着物体形状从静态场景移出，物体由官方 robot extra link 的球表示；完整世界仍必须声明这些物体形状。普通球查询所有静态形状，包括支撑面。携带球查询移除 `departure_support_names` 后的所有静态形状。不同球组的输出相加；关节界限、自碰撞以及精确几何闸门仍走原路径。对象或支撑名缺失、重复，球槽位变化，或者 planner/checker 未绑定分组场景时抛错。精确几何仍只接受原有向上离开且 separation ≥ -0.1 mm 的接触条件。

`last_validation.proxy_support_exemption` 仅在启用分组场景时记录 `enabled`、支撑名、有效携带球数、物体形状名。

## 验证范围

官方解释器的 CPU Warp 小场景覆盖携带球与支撑面、其他障碍，以及普通球与支撑面的碰撞；54 个有效球与 256 容量槽的配置通过，少/多一个有效球、索引改变仍拒绝。标准解释器没有 cuRobo，因此跳过场景测试。尝试在官方环境用 CPU 构造完整 `CarriedMotionPlanner`：配置解析的默认 `DeviceCfg()` 先访问 CUDA；把该默认值仅在临时验证脚本中改为 CPU 后，官方 URDF parser 的 `get_link_parameters` 仍硬编码 `device="cuda"`，无 GPU 时在构造 `MotionPlannerCfg` 阶段失败，未到 planner 初始化。完整服务的 IK、轨迹优化、CUDA 图和 seed 102 GPU 复现均未验证。
