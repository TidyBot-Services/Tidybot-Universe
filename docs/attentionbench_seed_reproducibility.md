# 场景可复现性：调查、根因与修复（2026-10-16）

问题：同一个（任务，seed）的初始物理场景不一致，策略之间不再是公平比较（此前 D3 关口失败，水槽 5 次重置只有 2 次匹配）。
本文记录阶段 1 到 3 的全部结论。证据：`outputs/seed-probe-20261016/`（探针脚本、原始 JSONL、汇总），`outputs/openarm365-formal-smoke-20261016/e2e-repro/`（端到端）。全程只用模拟器，没有任何模型调用，没有付费。

## 1. 阶段 1：测量（72 个新进程，360 次重置）

每个进程用与真实 worker 相同的参数创建环境并重置，缓存目录全新。探针的指纹与前一轮报告里失配状态的指纹逐位相同，说明它与真实 worker 看到的是同一个场景。

| | 钩子关（Dev 实验的默认） | 钩子开（`scene_reset_seed`） |
|---|---|---|
| 柜门 | 5 种场景 / 30 次，不可复现 | **1 种 / 30 次**（三个 seed 均是） |
| 水槽 | 16–20 种场景 / 30 次 | **2 种 / 30 次**，左右各约一半，与重置序号、进程无关 |

结论：钩子已经消除了物体位置与朝向的随机性；柜门完全正常；水槽只剩一个二选一。**默认不开钩子，所以默认实验完全不可复现。** 不同 seed 的场景互不相同（seed 仍然起作用）。

## 2. 阶段 2：根因

给 `sample_reset_region` 和 `get_fixture` 加日志后：随机数发生器的状态、选中的固定物体（`counter_1_left_group`）、选中的区域名（`geom_0`）在每次重置里**完全相同**；变化的是 `geom_0` 对应的区域的偏移量，在 +0.831 与 -0.784 之间切换（水槽 y=-1.796 两侧约 ±0.59 m）。

原因在上游 `robocasa/models/fixtures/counter.py:813`：

```python
valid_geoms = list(set(valid_geoms))
```

`valid_geoms` 是几何元素对象，对象按内存地址哈希，`set` 的迭代顺序因此每次不同；随后按这个顺序命名 `geom_0`、`geom_1`。哪一侧叫 `geom_0` 是抛硬币，之后同样的随机数选中 `geom_0`，就落在左或右。**这与随机数种子无关，所以设 seed 修不好。**上游只有这一处相关的 `list(set(...))`；柜门任务没有左右二选一，所以一致。

## 3. 阶段 3：修复与验收

修复（`openarm365_dev_v2/environment.py`，不改上游文件）：在可复现重置钩子生效期间，给 `Counter.get_reset_regions` 套一层包装，把返回的区域按其几何内容（偏移、尺寸）排序后再命名 `geom_0..`。这样顺序由几何决定，不由内存地址决定。左右被选中的概率仍为各一半（现在由 seed 决定）。只在钩子开启时生效。

| 验收 | 结果 |
|---|---|
| 水槽 seed 101/102/103，每个 40 次重置（20 个新进程 × 2） | 每个 seed 只有 **1 个指纹**，逐位相同 |
| 柜门 seed 101/102/103，每个 18 次重置 | 每个 seed 只有 **1 个指纹**，逐位相同 |
| 不同 seed 是否仍给出不同场景 | 是 |
| 真实 formal 流水线跑两次（水槽，seed 101） | 两次的 worker 端初始场景指纹相同，且与探针的指纹逐位相同 |
| 单元测试 | `test_scene_reproducibility.py` 4 项 |

其余变化：formal 配置现在**要求** `scene_reset_seed == seed`（缺失即拒绝）。Dev 实验要用 `"reproducible_reset": true`，否则仍然不可复现。

## 4. 第 1 周收尾（2026-10-16 下午）：补测、强制开启、held-out

**柜门补测。** seed 101–103 各再起 14 个新进程（每个重置 3 次），与原有 6 个合计每个 seed 20 个新进程、60 次重置：每个 seed 仍只有 **1 个原始指纹、1 个归一化指纹**；水槽早已是每个 seed 20 个新进程、40 次重置。六个指纹与 `phase3_summary.json` 逐位一致。原始数据 `outputs/seed-probe-20261016/cabinet_extra_*.jsonl`，脚本 `run_cabinet_extra.sh`，无失败记录。

**Dev v2 主引擎强制开启。** `dev_configuration.require_scene_gate`（在 `dev_experiment.create` 里、任何分发之前调用）：`mode=experiment` 时
- `reproducible_reset` 必须是字面量 `True`（缺失、`False`、`1`、`"true"` 都拒绝）；
- 必须给 `scene_contract`，且契约要覆盖所选的每一个（任务，seed），缺任何一个都拒绝（不是跳过）；
- 旧的独立 `configuration` 写法（daily 桥）无法表达这两项，**不受该门约束**，是已知缺口；主桥 `dev_main_bridge` 在实验模式下自动带上已登记的契约，所以 UI 里的实验目前只能选 seed 101–103；
- 诊断和交互模式不受影响。

通过后 manifest 的每个配置带 `scene_reset_seed = seed` 和 `scene_contract`；worker 在初始化后取场景状态与契约比对，不匹配就标 `condition_invalid`（既有行为，没有改）。

**契约 v2（按 seed 登记指纹）。** v1 契约一个任务只有一个完整状态，无法覆盖多个 seed。v2 是 `tasks[任务].seeds[seed].normalized_sha256`，按 `scene_contract.fingerprint(state, rounded=True)` 比对，其它字段（摘要校验、容差声明）不变；v2 的比对必须带 seed，查不到就报错。v1 仍可用，但实验模式下只允许单个 seed。已登记：`openarm365_dev_v2/scene_contracts/dev-seeds-101-103-v2-20261016.json`（两个任务 × seed 101–103，每项 20 个新进程），生成脚本 `outputs/seed-probe-20261016/build_scene_contract.py`，不满足"每项恰好一个指纹且至少 20 个进程"就拒绝写文件。

**Held-out。** `dev_tasks.HELD_OUT_SEEDS = 1001–1010`，取旧规格书 1001–1100 的前十个，事先固定，未看任何结果。`validate_selection` 对这些编号抛 "held-out seed is not available to development experiments"；`create_env` 经过同一个检查。没有对它们跑过任何探针、指纹或模拟。

**测试。** 解释器 `work/service_env/bin/python`，需 `PYTHONPATH=…/development/attention_memory_service`（`aspire` 环境里的 editable 安装仍指向搬迁前的路径）。`benchmarks/attention_harness/tests` + `openarm365_dev_v2/tests`：1835 通过、60 失败、57 错误。与 2026-10-15 基线相比，基线中的 56 项只有 1 项现在通过（`test_service_http_idempotence…`），其余同名失败仍在；多出的 5 个失败（`test_openarm_dev_loop` 锁路径、`test_ui_server` 相机桥、`test_contract::test_old_freeze_unchanged`、`test_failure_analysis` 历史样本、`test_wait_entry_recovery::test_cli_dispatch…`）和 57 个 `test_integration_epoch` 错误，在干净的 HEAD（只叠加他人已有的 formal 改动）上逐名相同地失败，原因是工作区搬迁后的旧路径（Service 字节漂移、旧路径下的 sqlite、冻结配置的哈希钉）。失败名单：`outputs/seed-probe-20261016/failures-after-20261016.txt`。根目录新增 `conftest.py`：其余测试用模拟 runner、不涉及物理场景，所以对它们关闭实验模式的场景门；只有标了 `@pytest.mark.scene_gate` 的测试跑真实检查。为让主桥测试贴近真实路径，`test_main_integration`、`test_wait_entry_recovery` 的 seed 从 125 改成 101。

## 5. 没有解决的

- 实验模式已强制（见第 4 节）；诊断和交互模式的默认仍是钩子关。
- 没有运行时守卫：如果以后上游或环境变化让场景再次漂移，要靠重新跑本探针发现。运行时校验现在靠 v2 契约，但只登记了 seed 101–103；其余开发 seed 在补测前不能进实验模式。
- 只验证了 seed 101–103 和布局 11/样式 34。其余 22 个开发 seed 没有测，也没有契约条目。
- 没做固定动作 rollout 指纹；契约只覆盖初始状态。
- held-out 编号没有指纹；需要单独放行后再测，其后才能写进契约。
- 契约指纹来自探针进程，worker 路径只对水槽 seed 101 做过逐位比对（端到端），柜门在真实 worker 里的首次比对还没发生；若不一致，所有柜门 cell 会被标 condition_invalid（宁可失败，不会悄悄放过）。
- 这是对官方环境行为的一处运行时修正（让区域顺序由几何决定）。它保持了左右各半的分布，但环境不再是“官方原样”，论文里要说明。
