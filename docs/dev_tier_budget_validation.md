# Dev v2 预算档与离线验收

## 档位和兼容性

- 新实验默认 `budget_tier='dev'`：单次上限 320 次 SDK 调用、9600 控制步、1920 秒。档位同时写入选择、任务配置和清单；配置与清单的现有字节哈希因此绑定档位。任务次数、模型用量、人工额度和物理安全阈值没有放大。
- 显式 `budget_tier='legacy'` 使用 80/2400/480，whole-case 仍是 1800 秒。旧格式中**缺少** `budget_tier` 字段就是 legacy 的版本约定。HEAD 旧配置及清单本来没有此字段，补写字段与“逐字节相同”不能同时成立；新 dev 清单则明确写入 `dev`。验证器按清单、选择和每份配置的一致档位校验，不从环境推断旧档。
- 已授权的旧目标账本缺少档位标记，Main Bridge 将它解释为 legacy；`dev_integration.prepare` 明确选择 legacy，因此旧授权、派生预算、预留和挂钟仍沿原额度运行。新自动建账目标标记为 dev 并使用 4 倍任务资源及目标挂钟。预算文件的挂钟声明必须与该档位匹配。
- 正式配置仍用 80/2400/480 和正式 schema。正式入口拒绝 Dev 配置，正式配置生成器拒绝 dev 档。`ATTN_DIAG_BUDGET_SCALE` 只在有探针标记时生效，倍率直接覆盖 Dev 默认倍率，1–8 的原校验不变。

## 43 个回归名称分类

| 类别 | 用例 | 原因和处理 |
| --- | --- | --- |
| A，旧断言 | `test_contract.py::test_task_config` 的 6 个参数、`test_iteration.py::test_formula`、`test_recovery_matrix.py::test_R10b_minimal_wall` | 新 Dev 单次上限变为 320/9600/1920；三次执行秒数是 `3×(1920+60)=5940`，单槽最小挂钟是 `2×120+1920+60=2220`。按这些推导更新断言和边界值。 |
| A，旧夹具 | `test_integration_prepare.py::test_prepare_original_resource_remaining_insufficient` 的 13 个资源参数、`test_prepare_retry_does_not_double_allocate_exact_capacity` | 这些测试的目标账本固定为旧授权；离线 `plan()` 必须显式选 legacy 才能计算“差 1”与“恰好够”的原额度。 |
| B，授权兼容 | `test_integration_prepare.py` 其余 18 个名称、`test_main_integration.py::test_main_bridge_maps_actual_frozen_configuration_and_no_clock_on_refresh` | 旧目标账本不能承载 Dev 4 倍预算，错误在目标级前置检查就发生，掩盖被测分支。实现改为从已有账本选 legacy，prepare 显式锁旧档，仍保留资源和挂钟的拒绝检查。 |
| B，清单复现 | `test_iteration_edges.py::test_old_manifest_exact_bytes` | 默认 Dev 改写不可变旧清单；增加显式 legacy 路径，恢复旧配置、预算和清单的原字节。 |
| C，既有断言 | `test_attachment_dump.py::test_opt_in_validation_and_default_context` | 附件支持集合现包含 10 mm 内固定件。测试的模拟模型补齐距离查询，期望 `counter` 为邻近支撑；未改附件实现。 |

## 字节对照与测试

使用 `git show HEAD:openarm365_dev_v2/dev_config.py` 的原函数及原预算依赖，在相同锁输入下生成 seed 101 `open_cabinet` 配置：HEAD 与显式 legacy 均为 1951 字节，SHA-256 同为 `9d78a0f5bbd27e67b28d0b356d03e095814e09e8f1971daad1120c444865fcd4`。用 `git show HEAD:benchmarks/attention_harness/dev_experiment.py` 的原创建函数，在相同目录、请求和旧账本下先写交互实验，再由当前显式 legacy 创建：v2 不可变清单没有冲突，6187 字节和 SHA-256 `e2096b23d68915974bf95926732526dd1c76d079e679e333e7698217bd80804b` 一致；v3 不可变清单也没有冲突，10197 字节和 SHA-256 `78501c16ec18587e44c5a876d5dbd3ac2348393711a126c0cb9bf1d3ea0fe6cf` 一致。两者的 cell 预算、目标限额一致。`test_old_manifest_exact_bytes` 用稳定旧实现夹具逐字节比较清单、配置、cell 预算、目标限额及首轮执行预留，在 legacy 下通过。

本 worktree 缺 `openarm_phase0_recheck/model/compat_v6/robot.urdf`。失败名称对照在 HEAD 快照和当前树都只对离线测试的 `model_sources` 使用相同替身，不改生产身份校验：给定 43 个名称，HEAD 42 通过、1 失败（附件旧断言），当前 43 通过。扩大到 `openarm365_dev_v2/tests` 后，识别到旧预算文件夹具和运行次序造成的另外 12 个差异；更新档位授权夹具后，这 12 个与原 43 个合并重跑为 55 通过。8 个相关 benchmark 测试文件在 HEAD 与当前各为 21 failed、82 passed、4 errors，失败名称差集为空。`test_integration_prepare.py` 等 42 个相关用例在相同替身下全部通过；新档位及诊断测试 15 通过。未启动物理设备，未调用模型。
