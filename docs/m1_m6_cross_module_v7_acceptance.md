# M1–M6 跨模块全链工程验收：v7 收口

本页只记录跨模块工程链路；M1–M6 已分别通过的模块门槛未修改。结论为 **v7 同版工程验收通过**，不代表正式实验准入或策略效果达标。全部运行 `formal_eligible=false`，没有运行 held-out、正式七策略效果矩阵、真人或 LIBERO。M3 历史 depth 500 根因风险**仍未解决**。

## 冻结与版本

外部原件根 `/home/truares/桌面/attentionbench-m1-m6-cross-v7-20260929/`。`freeze.json` SHA-256 `70a9fc5ec5f35b9137b1e3f429d977f604352a8c260576002f69f0b225d29769`；`execution_lock.json` SHA-256 `63d3ea30968a81774535056b85c5693ef474a13757ad9d94019c91b5da3eb161`。Universe 冻结基线为 `feature/attention-native-robosuite` @ `f0d9093f35a462febdc349b0bfd21799b8e37074`；独立 Memory／Robosuite／RoboCasa／Agent／task source 提交分别为 `24d414638ad2cdd6557f09f042e8b6ef2b597b0f`／`081cd57ec9383895dc150050ca5d05c24628e7fa`／`320020a0c94434af31ec02df3413229576490fef`／`4cf4daaba61d4cbbb0ca6daaa4ff28165c9daf1b`／`b18bbf1585c42e370ae45ababdddad700cc2c71d`。

锁定任务为 Robosuite `cube_lift` seed 103 与 RoboCasa `counter_to_sink` seed 101，`sim_gt`，选定策略 `full_trace_aware_attention_planner`。每套最多两份 Graph 候选、两次正式 run；每 run 最多四次 simulator attempt、一次 Advisor credit、4096 tokens、总截止 300 秒。4096 的依据是首版真实 Advisor 2677 输入＋549 输出＝3226 tokens 超过原冻结 3000，留出 870 tokens 余量；v1 失败结论与原件不改。无 Service 预检先核实 trusted v1 的适用范围和 RoboCasa candidate 的未晋升状态。

v5 的 Robosuite 真实 Advisor／Memory 已触发，但 Eval 投影修复发生在 v5 运行后，冻结运行文件 SHA 不一致，最终仍是 `failed_acceptance`。v6 Dev 提示错误地让 RoboCasa 寻找 `yogurt`，与锁定任务和公开 SDK `boxed_drink` 不符，批准前拒绝，正式 run 为零。两个失败版本与 v7 分开保存，不拼接为通过。

## v7 真实 Graph 与审批

两套真实 Orchestrator Graph 完成 Dev 生成并等待明确批准。用户对 v7 候选 A 审阅包 SHA `3d838cba3800899fba012ecfffd5360b42e0299f371fc172bc6c0c0eb521d633` 明示批准；外部审批记录绑定源码、配置、生成收据和入口锁。RoboCasa 候选 A 的一次正式 run 完成 Runner、独立 Safety、Eval 和持久 UI，原生结果为失败且 Safety 停止；其两条未晋升 Memory candidate 均未获得本次授权或使用。

Robosuite 候选 A 的首次正式 attempt 原生成功，于是未触发 Advisor／Memory，且该成功 run 的 UI 没有 Trace 行。其 `candidate_a_audit.json` 保留 `failed_acceptance`，该 run 不算本次要求的完整链路，也未重派。冻结预算内第二份候选 B 来源于先前真实 GLM Dev 在**完全相同提示 SHA** 下生成的原样源码；`dev_origin.json` 保留原始回复和 SHA 链，并明确本轮没有新的模型调用。用户又对候选 B 审阅包 SHA `5d9c48d88ebf38722cf70a469f79897eafa459310a5a8ad0f15f6b1ba7b5b0b7` 明确批准；其源码 SHA `d84ba66fb643c395f7013dd3f00a29856488126a7f2ac93dbd721af2b88b2747`、配置 SHA `1a0953f5c5311e3e69f2ddee99781e0f912b6add87847e0b8c5321c320e7ff1d`、生成收据 SHA `f5a6eba12eb7202a5df440233ed5db43296df466a4bf96d3b83d9631feec1108`、入口 SHA `dd9588e9e8ed48760a5e4d4ebcfd3da82a81ebe9c2bf7d6720c23522f149506c` 在派发前复核。B 与 A 使用独立 Graph、Memory 副本、run 目录和 approval 文件；B 只派发一次正式 run。

B 的同一 run `run:attention-robosuite-cube_lift-seed103-2dc648187758` 完成四个 attempt。真实、未缓存 `parcc/GLM` Advisor 回复 2785 输入＋670 输出＝3455 tokens，在 4096 内，且在 attempt 2 的正式 Trace 中实际采用。后续公开 Trace 决策检索适用范围内 trusted v1 `candidate:attempt:attention-robosuite-cube_lift-seed101-68b4cbf67d66:0`；Memory Service 向 attempt 3 颁发版本 1 授权，并在同一 attempt 记录使用。原生任务最终失败；冻结工程门槛不要求策略成功。RoboCasa 原有 run 在 B 执行期间只读复核，run ID、产物 SHA 与首轮审计一致，未重跑。

## 完整性、故障与结论

选定的 Robosuite B 与 RoboCasa A 两个 run 逐 attempt 核对 Graph／Bridge／Harness／Runner／Trace 身份、批准代码与配置 SHA、原生 evaluator、独立 Safety、Trace／Safety／sandbox receipt／native result 四类原件 SHA，以及 Service leader 回收和进程组退出。Eval 持久诊断完成；Graph 重启后 UI 对 B 同一 run 展示四个 attempt、四条 Trace、一次请求、一次响应和 Eval。两套完成后重启 `/attention/auto-start` 都返回 `spawned=[]`。隔离的内存副本篡改检查拒绝 attempt 身份和产物哈希错配；另在隔离 Graph 副本中删除已认领 dispatch 的 run 链接，重启明确记录 `M2 dispatch interrupted; inspect downstream before retry`，没有新增 Runner attempt。

最终逐项脚本 `audit_candidate_b.py` 对选定双 run 和历史 A 分离审计，**58/58 passed**。`candidate_b_completed_audit.json` SHA-256 `892cf1c0ddc574734285fa2357caa3ad496099bd0a21fff1cfd2672c75d6aedd`；340 件原件和冻结运行文件的 `sha256-index-final.json` SHA-256 `288dce9f270c86555dd316c137d352cffea2cf55a8926c34b56b4a6d3d98957a`，逐文件复核零错。冻结命令见 `commands.completed.json`，源批准与两套真实 run 原件保留在上述外部根。

**结论：仅 M1–M6 跨模块工程验收通过。** 这不提升 M1–M6 原模块门槛，不宣称 RoboCasa 正向 Memory 效果、七策略效果优势、held-out、真人、LIBERO 或正式实验准入。候选 A 的早成功／UI Trace 缺失与 v1–v6 失败原件保留；M3 depth 500 根因另列未解决。
