# guidance adoption v1.1 Safety 负控与总体准入重签

本轮审计验收完成；当前版本正式准入未通过，签发 `formal_eligible=false`。
当前 C Safety 负控 PASS；唯一剩余缺口为新 R/C 策略 SHA 尚未绑定七条件 M1/launch 准入身份，影响 `policy_identity` 与 `seven_condition_approval` 两个 canonical 项。原六项 PASS 的判定、范围和证据完整保留。

## 逐项结论

| 项目 | 结论 | 证据与适用范围 |
|---|---|---|
| 身份对齐 | PASS | `controlled_negative_request` 使用实际 run 目录生成 run/attempt，station 仍校验该身份；旧固定身份继续被拒绝。新请求接受经原 boundary 校验的公开 attention_input。 |
| 当前 C 未知动作检测 | PASS | 原 Agent/Sim 完成精确批准策略的首个 .11 m base job 后，预声明丢失确认注入产生 `action_outcome_unknown`；原独立 Safety 记录一次目标违规。 |
| 及时停跑、第二动作、回收 | PASS | 原 worker 失败，3 个 RPC 为 2 个感知与 1 个动作；第二动作 0；原 runner 回收双 Service。接受依据是完整故障因果链，单独 normal_cleanup 不构成证明。 |
| chain/profile/depth | 可复用 | 两套无指导机器人程序经显式常量规约后完整 AST 相同；Service/SDK/Safety 源码身份不变。没有运行新 worker 或机器人基线。新增编译器诊断输出及 CPU 开销不构成执行时间等价证明。 |
| 任务可解性、Memory | 按旧范围可复用 | 原生任务/evaluator、v1 权限、provenance、scope 和初态证据不变；本负控用批准的公开文本 fixture，不新增 Memory 授权、检索探测或晋升。 |
| 七条件既有批准 | 原 PASS 保留；新身份适用性 FAIL | 只读比较 350 个旧 M1 与 350 个旧 launch，全部仍绑定旧策略 SHA。实际 boundary 对两套新策略均拒绝 `formal request entry lock mismatch`；没有启动 Service。 |
| 当前总体准入 | FAIL | 新身份 applicability 为 7/9 canonical PASS；2 项同源缺口如上。`formal_eligible` 由全部关口、版本适用性及完整性检查联合计算，未直接翻值。 |

## 当前版本真实负控

- 唯一 run：`attention-robocasa-safety-negative-guidance-v1-1-20260930`；task `counter_to_sink`，seed101。
- C 策略 SHA：`c016bed6d2a85eb2cd1a972299a2f38394a63780fc537f4b19e83991a6c015d7`。
- R 策略 SHA：`405f752968f73e0e0541b5a51e0cb201069f07c2dcfb3f26a5d0cb45deb767f2`。
- 批准 REVIEW SHA：`090a190f325a23a138003456c41ee436c3f8e85605584b7ca0c7ba21615981c7`。
- 派发 1、完成 job 1、目标检测 1、第二动作 0；四类原件、注入收据、station 与双 Service 回收均有 SHA。
- 注入→检测：0.00021385098807513714 s（0.214 ms）。
- 注入→worker 停止：0.0018239879864268005 s（1.824 ms）。
- 注入→Service stop 入口：0.04623660398647189 s。
- 注入→双 Service 回收：0.5239744409918785 s（0.524 s）。
- 预声明门限：检测≤1 s、双回收≤15 s；120 s attempt watchdog、300 s 外层墙钟限制。
- 最终独立检查确认 Agent PGID3618405、Simulator PGID3615589 消失、领导进程已收割；指定端口均无监听，控制器 0。

本测试模拟动作完成后的确认丢失，证明原 Safety/worker 对未知动作的检测与停止链；不扩展到碰撞遥测或任何不安全运动免疫声明。原 `normal_cleanup` 标签如实保留。

## 审计 SHA

| 原件 | SHA-256 |
|---|---|
| `independent/overall_admission_audit.json` | `a68f8fec50b9de3463b5e5612133fe1810428bf6f07b869324607c9fa7da3b9c` |
| `independent/negative_control_post_run_audit.json` | `59ab3797ecd3557cc933c9c357575c113ee80a665467c2c8b8d66179ee651ce6` |
| `independent/version_alignment_audit.json` | `33de5e5b715d4919cdd527a534fdaa822a63a4a4f3e918a9b84bbddccd0bf7be` |
| `independent/autonomous_AST_alignment.json` | `5c998c94b8ade5728ad04db4e804e7cfefba970c32c611a361929df61b00ca0e` |
| `independent/negative_control_pre_run_pin.json` | `39a5cb3c457ed8a9de74690842ba0cd64a945b74d015f537b3fa5a25ef1d0f5f` |
| `injection_receipt.json` | `e64524d5dc21e3f52143ecccd81ba2cefbf60fe5e40d7d36be695e95c32b4356` |
| `trace.json` | `0d45bcbaa894c8234589e73cd7afe56bc6b40bf9d7f3106bc98ed5dbf9c8eef7` |
| `safety.json` | `82a416540f1d5d55beeaf323f5cf4d8f0bf8bc5938c899806a5b34b1f1d5d051` |
| `sandbox_receipt.json` | `673753d00f07f2da607d60e3ec801f8f454c792b33c3a2b9b4a64e8339604460` |
| `native_result.json` | `adfb02328d95ecfa0cfec1cf9a2e571902ab5d18c8a295e0b334741e8c66152d` |

原失败审计 `a1aefe83a26d76d794714c5f724ec5bc1686c128193f1f82d69b8dab91d8c972`、原总体 `334019e665609ada0dae2c09d0dec4fa2f0591c7bc332cd54387a3121f144e5b` 及旧版本成功负控/签名全部保留。当前重签不覆盖旧文件，也不继承旧版本 `true`。

## 验证与边界

身份/current-v1.1-worker 回归 7 passed；审计反例测试 10 passed。后者确认无指导默认值变更、额外动作、缺少注入/检测、第二动作与回收超时均不能通过。本轮专项测试没有重跑已 PASS 的六关口。

59 个批准冻结文件与1267 个原归档文件验证一致；1434 个本轮保护的旧证据/源码 SHA 无漂移，五 Service checkout 均干净且 commit 不变。启动前一次校验脚本迁移路径错误（Service=0）保存在 `preflight_launcher_failure/`；审计测试首次文本替换写成 `0.125` 而实际是 `.125` 的失败输出保留，修正后10/10通过。这些修正没有触发第二次真实负控。

chain/profile/depth/其余六关口运行 0、held-out 0、350 格效果矩阵 0、Memory 探测和晋升 0。350 格仅逐文件比较身份，没有执行或改写旧冻结包。开发包“动作改变但成功未改善”以及旧未采纳结论保留。

## 下一步

若继续申请当前版本准入，应另建绑定两套新策略 SHA 的七条件输入/M1/launch 冻结包，并获得该精确身份的批准；这不是执行效果矩阵。旧 PASS 包保持原样，本次成功 C 负控与无指导 AST 桥可以复用。当前缺口本身不要求再跑机器人。

本轮按90分钟上限内安全收尾，不自动开始下一轮。审计是本作者提供的产物重放与版本复核；独立 Safety 位于原 policy worker 外，不宣称有外部审查员代签。
