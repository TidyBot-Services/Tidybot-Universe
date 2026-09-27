# M3 双模拟器执行：修订版工程验收口径（工程层面已批准）

本文件是对 [`m3_dual_sim_execution_acceptance.md`](m3_dual_sim_execution_acceptance.md) 的**明确修订**，不覆盖原始验收或其“D：历史 Robosuite depth 500 根因关口未通过、M3 未关闭”的结论。修订版只决定是否允许 **M3 工程层面**关闭；历史故障继续列为独立、未解决的可靠性风险。所有运行仍为 `formal_eligible=false`，不构成正式实验准入。

## 修订标准与边界

| 项目 | 原标准及原结论 | 修订版工程标准 |
| --- | --- | --- |
| A 正常执行 | 两套四任务 × 开发 seed 101–103，12/12 已通过 | 保持必过；逐例复核原始身份、配置／代码／Service SHA、SDK 动作、原生结果、Safety、Raw Trace、四类产物与进程组。 |
| B Harness 分派 | 四任务各 seed 101，4/4 已通过 | 保持必过；复核 run／attempt／entry lock、runner 分派及错套拒绝。 |
| C 故障路径 | 双 Service 超时／取消／Safety，加 RoboCasa 动作 job 取消，7/7 已通过 | 保持必过；复核原始终态、Safety、原生结果、四类产物和进程组回收。 |
| 旧 D：历史 depth 500 根因 | 旧帧缺失、根因未确认、未做针对性修复；**未通过** | **不改写这项历史结论。** 不再要求找回缺失旧帧或在工程模块内无限期等待偶发故障重现。将其单列为未解决可靠性风险；不能称故障已解决。 |
| 新 D'：受控异常隔离 | 原标准没有这一替代关口 | 在冻结的 `cube_lift` seed 101 真实正式 Runner／独立 Robosuite Service 链路，第一项真实动作后的 normalized depth 各注入一个 `>1` 有限值和一个 NaN（两次独立进程）。必须经当前 Service 的原 `_metric_depth` 严格拒绝，保存逐字节原始 `.npy`、诊断与 SHA；HTTP 500 传至正式 Runner，独立 Safety 记 `action_outcome_unknown` 并停止；结果为 failed、`native_success=false`，不能把未知动作计为成功；四类产物 SHA 正确，Service leader 与进程组完全回收。帧中的异常像素须原样保留，禁止静默裁剪。注入器只存在于外部测试启动器，不修改 R／U 生产源码；记录其 SHA 和进程替换命令。任一子项失败则 M3 继续开放。 |
| E 回归 | 旧 M1 双 lock 不变，M1／Formal Runner 65、M2 gate／Bridge 22、R Service 5 passed | 保持必过；复核原始 SHA／命令及当前仓库契约，只补跑受本修订影响的必要测试。 |

原始 A／B／C／E 和旧 D 证据保持在 `/home/truares/桌面/attentionbench-m3-20260928/`，不得改写。新证据独立放在 `/home/truares/桌面/attentionbench-m3-revised-20260928/`。只读复核 `prior_evidence_recheck.json` 已核对原始 536 文件索引、A 12／B 4／C 7 逐例哈希及回收、旧 D 未通过、M1 lock 和 Service HEAD，全部通过。新增注入矩阵在 `revised_freeze.json` 先行固定，SHA `f9cd0c0e6fe60824ef6d677b7613f6a6fc27b3e708d00c174fd810659060f04c`；版本沿用 U `e1b76bd`、R `081cd57`、C `320020a`、A `4cf4daa`、T `b18bbf1`。此轮不修改 M1／M2 契约或 Service 生产代码。

## 必须保留的可靠性风险

旧 `cube_lift` seed 101 首次夹爪动作后的 HTTP 500，已知由旧版 normalized depth 守卫看到至少一个 `<0` 或 `>1` 值触发；旧异常帧不存在，具体数值、像素及上游渲染／时序成因未知。旧序列 20 次新进程重放和两任务 × 三 seed 的 36 次动作未复现，**不能证明根因已修复**。即使 D' 通过，真实偶发异常仍可能使动作结果未知并触发 Safety 停止。后续一旦复现，先保存 `.npy` 原帧、诊断、HTTP／Service 日志和 SHA；定位确证的生成路径后修复，并重跑冻结 A／B／C、受控注入与重复动作测试。工程关闭不解除这项可靠性风险，也不放开正式实验资格。

## 决策状态

**修订版工程关口 A／B／C／D'／E 已通过。** `revised_engineering_audit.json`（SHA `c7cf6d7f2646c9501aa3b84a96f585fb0cb8c2f80986465864b63dd964c38e35`）逐项为 true，并再次确认旧根因未解决、无遗留 Service 进程及 `formal_eligible=false`。新原件的 44 文件索引为 `revised_raw_index.json`（SHA `52494b939c3d856bbe6041c03708b3bc5a3e903956e94a52fa33d30397381551`）。该审计是**批准前快照**，其中 `closure_pending_explicit_user_confirmation=true` 不得改写。

- **A／B／C**：只读复核原始 536 文件索引和 12／4／7 逐例四类文件 SHA、终态、版本及进程组；见 `prior_evidence_recheck.json`。旧 D 仍为未通过，没有用本次注入改写历史根因结论。
- **D'**：两个独立 Service 进程分别在首次真实夹爪动作后的 64×64×1 float32 normalized depth 第一个像素注入 `1.000100016593933` 和 NaN。当前 R `081cd57` 原 `_metric_depth` 分别诊断 `above_one_count=1`／`nonfinite_count=1`，保存原始 `.npy`；帧字节 SHA、文件 SHA、形状、dtype、异常像素及诊断逐项一致。HTTP 500 进入正式 Runner，SDK 夹爪事件 failed，独立 Safety 均记 `action_outcome_unknown`／`unsafe_attempts=1`，attempt failed、`native_success=false`，四类产物 SHA 和进程组回收均通过。见 `runs/over_one/`、`runs/nan/`、`injection_audit.json`（SHA `218cbeb0fbd3acdda79bc67f7a70fce36d5593c076d0d258e3cf258d37fe9553`）。注入器只在外部启动器替换启动命令、包装一次动作帧，并调用 R 原转换器；R 工作树保持干净。首轮运行脚本内置审计因嵌套 HTTP JSON 解析错误将结果标为 false，但未影响 Service 执行或原件；独立只读审计正确解码并全部通过，错误和修正都保留在索引中。
- **E**：当前旧 M1 双 lock SHA 重算仍一致；必要回归为 Formal Runner 26 passed、M2 gate／Bridge 22 passed、R Service 5 passed。原始 M1／Formal Runner 65 passed、M2 22 passed 的证据也已复核。未修改 M1／M2 代码、锁结构或生产 Service。

2026-09-28，用户在本对话明确回复：“批准按修订口径仅关闭 M3 工程模块，保留 depth 500 未解决风险”。据此，**M3 仅按修订口径完成工程验收并关闭**。原验收包中旧 D 根因关口仍为未通过；历史 depth 500 未修复，不能据本次受控注入宣称根因已解决。正式实验仍不准入，`formal_eligible=false`。M4 可另行冻结工程验收包；M5／M6、held-out、Memory 晋升、七策略效果矩阵、真人及正式实验均不由此次批准启动。
