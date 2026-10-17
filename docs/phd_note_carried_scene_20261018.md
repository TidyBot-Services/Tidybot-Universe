# 给 PhD：携带物体离开支撑面的代理碰撞豁免（2026-10-18）

## 问题

MuJoCo 接触 margin 使静置物体常悬于台面上方 1.5–6 mm。夹持后 lift 的起点，保守携带球仍与支撑面代理盒相交，规划在起点被拒。seed 102 的真实 `/plan/ik_path` 请求见 `outputs/grasp-chain-102-support-20261018/.../planner/planner_http.jsonl` 第 145 行：附着请求含 12 个 `support_names`，HTTP 200 但响应 `status=failed`、`trajectory=null`，`validation.samples=1`、`joint_limits=true`、`proxy_collision_free=false`、`exact_geometry.collision_free=true`；该记录使用 deployment-03。

## 证据

`outputs/start-collision-102-20261018/results.json` 的 GPU 起点诊断：原代理检查为 false、精确几何为 true；只移除 `mujoco:1214:counter_1_left_group_top_left_1` 即通过。普通球世界碰撞通过，携带球失败。随后 `outputs/start-collision-102-carried2-20261018/results.json` 在启用豁免时记录 `proxy=true`、`exact=true`；其无豁免复现为 `proxy=false`。在 057ae08 的父提交上，排除模块自身后 `git grep` 未找到 `contact_scene.py` 或 `CarriedSceneCollision` 引用。

## 改动

057ae08 在 `planner_core.py` 的 `_select` 中，仅当有附着且 `supports` 非空时，于 warmup 前把 `CarriedSceneCollision` 接入求解器和代理 checker。`contact_scene.py` 将普通球送完整静态世界、携带球送移除 departure supports 的静态世界；四种碰撞查询分别转发。球槽映射、物体与支撑名称或完整世界不一致，或 planner/checker 场景未接好时抛错。启用时 `last_validation.proxy_support_exemption` 记录支撑名、携带球数及物体名。`geometry.py` 的精确接触闸门未改：只接受向上离开、每个接触点 separation ≥ −0.1 mm。

## 安全性论证

豁免仅作用于携带球与列出的 departure supports；普通球（包括手爪）仍检查全部静态形状，携带球仍检查其余障碍，自碰撞、关节界限及精确几何继续检查。`openarm365_dev/attachment.py` 从物体的非正距离接触，以及与物体几何距离 ≤10 mm 的合格固定 body 选 supports，并把入选 body 的全部形状列入；机器人、物体自身及可移动 body 不靠邻近规则进入。向下或侧向穿入会触发未变的精确闸门，不能仅凭代理豁免放行。

## 未验证与风险

deployment-04 本轮只做离线锁校验与测试；CUDA 图捕获路径、真实规划执行和其他物体形状未验证。若固定 body 被误列为 support，最坏情况是携带球对该 body 的代理约束被移除，只剩精确闸门；这是支持面识别正确性的依赖。升级官方 cuRobo 库后需重新核对分组查询和图捕获。20 mm cell edge 的携带球包络仍偏保守。

## 需要你确认的点

1. `contact_scene.py` 是否原本就计划接入代理检查？
2. 固定 body 的 10 mm 邻近阈值是否合适？
3. 20 mm cell edge 携带球覆盖的保守程度是否合适？

## 近期其他安全相关改动（已核对提交）

- 32ba117：关节位置增益 kp 300→1500。
- dab1619：认证查询预算 100000 次，移除墙钟限制；加入 IK path。
- deployment-03：冻结适配器及 ik_path，锁哈希 `7e2611af…8304`。
- bff21bc / 43f7a4a：`base.move_delta`、车体预检与整机精确余量。
- 08edd81 / 24a00ff：可移动物体接触豁免与监控中的预期接触放行。
- 0d00220：可移动物体接触时手爪对称误差界放宽至 10 mm。
- 8ec1861 / f83d7b9：ik_path 旋转及 `max_joint_jump` 诊断覆盖。
- e5c6e59：底座静止后再记录保持姿态。
