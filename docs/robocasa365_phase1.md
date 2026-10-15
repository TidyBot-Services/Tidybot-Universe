<!-- robocasa365-phase1 -->
## RoboCasa365 独立工程接入：两任务范围通过（formal_eligible=false）

2026-10-09：独立 `robocasa365_sim` 仓库 `feature/robocasa365-phase1` / `076ac669d2bf041712ad20653e88c952ff09cfcc`；正式 Adapter/Runner 接通共用 AttentionHarness、SDK、Trace、独立 Safety、Memory Service v2 与 UI。冻结 PickPlaceCounterToSink / OpenCabinet、PandaOmron、HYBRID_MOBILE_BASE、layout11/style34、开发 seed101、sim_gt。两个任务真实正式 smoke 均完成6动作/8控制步，native_success=false但evaluator可用，回执/观测/安全/产物/回收可核对；真实取消、超时通过。真实UI reactive-help只调用一次parcc/GLM（4508token），回复进入第二attempt并将位移1mm改为2mm。Memory仅验证范围/版本/授权拒绝和候选，未授权旧Memory、未晋升、未宣称效果。

278项合并回归（含45项RoboCasa365针对性测试）和3项独立Service测试通过，108项证据检查通过；6268个旧配置/审批/历史证据文件哈希不变，主仓库原分支与已有脏改动保留。验收报告、安装/契约/配置、最终源码补丁与真实证据：`/home/truares/Documents/Codex/2026-10-09/new-chat/outputs/robocasa365`。仅结论：**两个任务范围内的 RoboCasa365 工程接入通过，不代表完整 benchmark 或论文实验完成。** 没有held-out、target/unseen、七策略效果矩阵、训练或实机；本轮结束，不自动开启下一轮。
<!-- /robocasa365-phase1 -->

独立 Service 的 README、CONTRACT，输出目录的 ACCEPTANCE_FROZEN、OFFICIAL_REVIEW、RUNNING、ACCEPTANCE_REPORT 与证据是本阶段入口。
