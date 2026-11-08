# 报告：Counter-to-Sink seed 102 在 qifei 上 `scene_contract_mismatch` 的原因（2026-11-09）

## 结论（一句话）

**qifei 上搭出来的 seed 102 场景和 truares 是同一个场景**——物体、fixture、布局、资源、包版本全都相同。
不同的只是浮点数的最后一位：qifei 的数学库（glibc 2.39 libm，以及 numpy 在 AVX‑512 CPU 上用的另一套
SIMD 内核）和 truares（glibc 2.35，没有 AVX‑512）算出的结果差 1 ULP。复位时的物理沉降在
seed 102 里把这点差异放大了约 10¹⁰ 倍：`distr_counter` 的四元数一个分量差了 5.4e‑6。
注册合约要求“取 6 位小数后 sha256 完全相等”，所以这个差异足以判为不吻合。
101 和 103 在 qifei 上也有同样的末位差异，只是没被放大（≤4e‑14），取整后哈希仍然一样。

这不是代码或资源漂移，而是 **按位相等的指纹在不同主机之间本来就不能保证可移植**，
102 只是第一个踩到这个问题的种子。

## 比对的是什么

- 抛出位置：`openarm365_dev_v2/worker.py:146-149` 在 `env.reset()` 之后调用
  `scene_contract.capture_state(env)`，再调用 `validate(...)`；`runner.py:109`（以及 agent 模式下 broker 的
  `find_objects` 探针）看到 `matched=False` 就置为 `scene_contract_mismatch`。
- `capture_state` 记录：机器人两臂的 TCP 位姿、关节角、夹爪 qpos；每个任务物体（`obj`、`distr_counter`、
  `distr_sink`）的世界位姿；全部 fixture 根 body 的世界位姿。共 606 个数。
- v2 合约（`scene_contracts/dev-seeds-101-103-v2-20261019-kp1500.json`）对每个 (task, seed) 只保存一个
  `normalized_sha256`：每个数 `round(x, 6)` 后序列化再取 sha256。比对就是**两个哈希是否相等**。
  合约里写的 `tolerance: 1e-6` 在 v2 里实际上没有用到，是按位相等的取整哈希，不是容差比较。
- 合约是 10‑19 在 truares 上生成的（每个种子 20 个新进程 × 40 次复位，都相同）——它证明的是
  **同一台机器**上可复现，没有证明跨机器可复现。

## 证据

### 1. 失败运行本身（qifei `devexp-a11132142ebad76e51a4b582`）

两个 probe-owner 的 `scene-condition.json` 都是 `normalized_sha256 = 149932e2…`（合约要求 `d7d2f8f3…`），
两次彼此按位相同——qifei 自身是确定性的。

和 truares 上通过的运行（`devexp-9dd83103…/probe-owner-0001/scene-condition.json`，`d7d2f8f3…`）逐字段比较：
606 个数里 22 个不完全相等，**取 6 位小数后只有 1 个不同**：

| 字段 | truares | qifei | 差 |
|---|---|---|---|
| `objects.distr_counter_quat[1]` | -0.012262340539552528 | -0.01225697544494191 | 5.4e‑6 |
| `objects.distr_counter_pos[1]` | -2.4942138890640217 | -2.4942137171840124 | 1.7e‑7 |
| `objects.distr_counter_pos[0]` | 0.19200365308456085 | 0.19200380645875337 | 1.5e‑7 |
| `objects.distr_sink_quat[*]` | … | … | ~8e‑16 |
| `robot.*_joint_pos[*]` | … | … | ~1e‑20 |

字段集合完全相同，fixture 位姿全部按位相同。

### 2. 离线复现（只建环境 + `env.reset()` + `capture_state`，不连 planner、不跑 policy、不碰 sim.lock）

探针：`/home/qifei/scratch-seed102-20261109/scene_probe.py`（truares 上是同一份文件，在 scratchpad 里）。
参数和真实 worker 一样（`PickPlaceCounterToSink`、布局/风格 (11,34)、`pretrain`、20 Hz、`scene_reset_seed=seed`、
`PYTHONHASHSEED=0`、`MUJOCO_GL=egl`、和 worker 一样去掉 `LD_PRELOAD/LD_LIBRARY_PATH`）。
qifei 上用 `nice -n 19` 跑；当时 qifei 上有两个运行在执行，探针没有拿锁。

| seed | truares | qifei | 合约 |
|---|---|---|---|
| 101 | verified `90b0bf96` | verified `90b0bf96` | `90b0bf96` |
| 102 | verified `d7d2f8f3` | **mismatch `149932e2`** | `d7d2f8f3` |
| 103 | verified `d7e16c54` | verified `d7e16c54` | `d7e16c54` |

qifei 的 102 离线结果和失败运行的哈希完全一样（`149932e2`），所以离线探针确实复现了问题。
两台机器的原始差异：101 最大 3.9e‑14（`distr_sink_quat`），103 最大 4e‑19，102 最大 5.4e‑6。

qifei 上已有的 59 次 seed‑101 场景检查全部 verified；seed 103 qifei 上没有真实运行过，以上离线结果是第一份证据。

### 3. 什么相同（排除掉的原因）

- 代码：qifei 仓库 `bf30a5c`（“Sync of main 784f63d for the run host (same tree)”），场景相关文件与 truares 相同。
- 包版本：mujoco 3.3.1、numpy 2.2.5、scipy 1.15.3、robosuite 1.5.2、robocasa 1.0.1，两边相同；
  `libmujoco.so` 和 numpy `_multiarray_umath` 的 sha256 两边相同（同一个 wheel）。
- 场景 XML（运行保存的 `scene.xml`）：把两边的绝对路径替换掉以后，338 个资源文件引用相同，
  只有 5 行不同，且都是 fixture 尺寸**最后一位数字**不同（`coffee_machine_left_group_g*` 三个、
  `coffee_machine_left_group_start_button_1`、`dishwasher_left_group_door_handle_1`，例如
  `0.007150397340171456` vs `0.00715039734017146`）。也就是说差异在仿真开始前、robocasa 用 Python 计算
  fixture 尺寸时就已出现。
- 资源：worker 启动时的 `OfficialEnvironmentIdentity.verify_identity()` 在两边都通过；XML 引用的文件名相同。

### 4. 什么不同（原因）

| | truares | qifei |
|---|---|---|
| CPU | Intel i5‑14600KF（无 AVX‑512） | AMD Ryzen 9 9950X（Zen 5，有 AVX‑512） |
| glibc | 2.35（Ubuntu 22.04） | 2.39（Ubuntu 24.04） |
| Python | 3.11.15 | 3.11.16 |
| GPU | RTX 5070 Ti | RTX 5090（与此无关：沉降是 CPU 上的 MuJoCo） |

对 20 万个随机输入逐位比较常用数学函数（`libm.py`）：

- Python `math`（直接调 glibc libm）：`cosh`、`sinh`、`tanh`、`log2` 两边结果不同；其余相同。
- numpy：`exp`、`log`、`power`、`tan`、`arctan`、`arctan2`、`arccos` 两边不同；在 qifei 上设
  `NPY_DISABLE_CPU_FEATURES=AVX512…` 后，numpy 这些函数与 truares 完全一致（即这部分来自 numpy 的 AVX‑512 内核）。

对照实验（qifei，seed 102）：关闭 numpy AVX‑512、用 `GLIBC_TUNABLES` 关闭 glibc 的 AVX‑512 路径、两者都关，
哈希**都仍是 `149932e2`**。所以单靠关 SIMD 救不回来；剩下的 glibc 2.39 libm 实现差异（不是 CPU 分派，而是
算法版本不同）也会产生末位差。哪一个具体函数调用造成那 5 个尺寸末位不同，没有继续追到行号——对结论和修复方案没有影响。

### 5. 为什么偏偏是 102：复位结束时场景还没静止

truares 上 `env.reset()` 结束那一刻的物体速度：

| seed | 物体 | 线速度 | 角速度 | 接触数 |
|---|---|---|---|---|
| 101 | distr_counter | 2.2e‑5 | 2.3e‑4 | 4 |
| 102 | **distr_counter** | 3.9e‑5 | **1.9e‑3** | **3** |
| 102 | **distr_sink** | 3.7e‑3 | **1.6e‑1** | 2 |
| 103 | distr_counter | 1.8e‑4 | ~0 | 4 |

102 的 `distr_counter` 只有 3 个接触点、还在摇晃，`distr_sink` 还在转。这种“没站稳”的接触对初始误差非常敏感，
1e‑16 的差被放大到 1e‑6 量级，就跨过了 6 位小数的取整边界。101/103 的物体基本已经静止，末位差保持在 1e‑14 以下。

## 影响范围

- 任何不是 truares（或与 truares 相同 CPU + glibc 2.35）的主机都可能出现这个问题；qifei、以后的新主机都是。
- 101、103 目前在 qifei 上能过，但它们也是“恰好没跨取整边界”，不是被证明跨主机稳定。
- 与 `host_lib_policy_b` 一致：那条决定里 dev 层只记录 host‑lib 漂移，而这里是 host‑lib 漂移直接让场景合约失败。

## 修复选项（都需要中控台/用户决定，本次没有改任何生产代码）

1. **按主机注册（最小改动，推荐先做）**：在 qifei 上用和 10‑19 一样的流程（每个种子多个新进程 × 多次复位）
   确认 102 在 qifei 上稳定得到 `149932e2`，然后在合约里允许每个 (task, seed) 有多个**带主机环境标签**的指纹
   （例如按 `cpu_family + glibc` 分组），或为 qifei 注册单独的合约。这需要改合约 schema / `seed_entry`，并重新登记
   `REGISTERED_SHA256`——属于改变“什么算同一场景”，要用户点头。好处是仍然按位严格；坏处是每加一台新主机都要做一次登记。
2. **改成真正的容差比较**：v2 只比哈希，忽略了自己写的 `tolerance`。可以在合约里同时保存完整状态，用 v1 那样的
   `compare()` 逐字段比，容差取 1e‑4 m / 1e‑3（四元数）之类。102 的最大差是 5.4e‑6，完全能过。这是**放宽检查**，
   按用户规则必须用户本人同意。
3. **让场景先静止再取指纹**：在 reset 后（取指纹前）多沉降若干步直到所有物体速度低于阈值，并重新生成合约。
   这会改变初始场景本身（所有种子的指纹都要重算、已有运行不可比），并且不能保证彻底消除跨主机末位差，
   只是降低放大。改动较大，不建议作为第一步。
4. **只在合约生成主机上提供 102**：Remote 继续不提供 102（现状，main `b7466c6`），或者把需要 102 的运行调度到 truares。
   不改代码，但 Remote 的种子选择变窄。
5. （不推荐）在 qifei 上换 glibc / 强制关 AVX‑512：已经证明关 AVX‑512 不够；改 glibc 需要 sudo，而且是共享机器。

建议：短期保持 4（102 不在 Remote 上提供）；如果要把 102 放回来，走 1（按主机注册），并在新主机上线清单里加一步
“对所有已注册种子跑离线场景探针”（下面的探针可以直接用）。2 需要用户本人决定。

## 复现方法

```bash
# truares（仓库根目录下）
env -u LD_PRELOAD -u LD_LIBRARY_PATH PYTHONHASHSEED=0 MUJOCO_GL=egl PYTHONPATH=$PWD \
  /home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python -B scene_probe.py 102 out.json
```

```bash
# qifei（~/scratch-seed102-20261109 下）
nice -n 19 env -u LD_PRELOAD -u LD_LIBRARY_PATH ATTENTIONBENCH_WORKSPACE=/home/qifei/ab-remote-20261107 \
  PYTHONHASHSEED=0 MUJOCO_GL=egl PYTHONPATH=/home/qifei/ab-remote-20261107/repos/attentionbench \
  /home/qifei/ab-remote-20261107/sim/runtime/venv/bin/python -B scene_probe.py 102 probe_102.json
```

qifei 上留下的文件（只新增，未改动 qifei 工作区）：`/home/qifei/scratch-seed102-20261109/`
（`scene_probe.py`、`libm.py`、`cpu.py`、`probe_10{1,2,3}.json`、对照实验 `probe_102_*.json`、日志）。
