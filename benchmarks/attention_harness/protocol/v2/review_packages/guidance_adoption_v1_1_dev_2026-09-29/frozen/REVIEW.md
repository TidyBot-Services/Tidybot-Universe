# 指导采纳 v1.1：新版本冻结审批包

**有界工程验证通过；当前最终 SHA 审批待用户决定。新包始终 formal_eligible=false，不继承旧准入签名。** 本轮结束后不自动开新测试，350 格与 held-out 均执行 0。

本包解决“指导只被曝光、未进入控制”的缺口。两份策略直接消费 attention_input，限定文本/SDK轨迹/授权 Memory 文本进入动作参数；不改 evaluator、Safety、worker 或七条件决策与准入门。原七条件冻结包及其 semantic_limitations.md 原件不变。

## 逐项结论

| 项目 | 实际结果 | 结论边界 |
|---|---|---|
| 设计先冻结再实现 | 原设计 SHA 与 v1.1 修订设计 SHA 均有实现前收据 | 三条内容到控制的路径、曝光≠采纳规则明确 |
| 无指导基线不变 | 两套旧/新机器人指令相同；Robosuite 原生 OSC 数组也完全相同 | RoboCasa 只读规划查询目标有后端运行间差异，不算机器人动作变化；离线相同观测下全部调用一致 |
| Robosuite hint | 同 seed101/config/初始观测；容差 .018→.004、抓取中心+.020→+.005m、闭合10→30步；实际49→86控制步；原生 false→true | 指导来自标注的确定性开发 Advisor fixture，未声称线上 GLM 自主修复 |
| Robosuite demo | 已验 SHA 的 SDK 先验轨迹决定目标偏移/容差/夹爪参数；实际控制与无指导不同；原生 false→true | 为明确标注的 authored SDK development prior，非成功示教录像 |
| Robosuite Memory | 已选中 trusted v1 原文本触发控制 recipe，实际动作改变；原生 false→true | 额外实际调度器证明限定 retrieve/grant/文本/next attempt/use 一致；使用原 autonomous 开发路径，不冒充 full 条件 Memory 效果 |
| full_trace_aware_attention_planner | 两次失败→inspect/一次求助→第三次相同策略消费回复并原生成功；请求/回复/execution关联已核对 | 原 full 决策代码不变；当前一般失败下不会先选择Memory，不在本轮扩展其选择门 |
| RoboCasa hint/demo v1 | 首个已完成底盘job前进 .125→.11m，9/9 job完成；仍原生 false | 已采纳；成功改善未通过，属于后续可达性设计问题 |
| RoboCasa Memory v1 | settle_steps字段变化被实际后端忽略；修改的抓取高度未执行（只读hover plan拒绝） | **未采纳**，原件保留，不能凭SDK字段差异算通过 |
| RoboCasa Memory v1.1 | 实现前另冻修订：同文本还选择较短初始进路；新同策略双臂首个底盘job .125→.11m；9/9 job完成；仍原生 false | 采纳通过；成功改善未通过。v1 hint/demo现场证据仍按其原SHA解释；v1.1只改Memory短语recipe |
| Safety/四类产物/回收 | 20/20完整attempt，unsafe总数0；80份主产物SHA，所有专属Service进程组已回收 | 独立Safety代码保持原SHA；未碰其独立负控缺口 |
| 有界验收与冻结 | 独立执行驱动之外的只读机械审计204/204；相关最终测试82通过；1724旧包/源码文件无漂移 | 机械审计由实现者编写，不能声称独立评审人员已审批；至少一例原生改善门槛由Robosuite满足 |

本轮共20次attempt，只有seed101的两种原场景。6次原生成功、14次原生失败全部保留；这是存在性因果证据，不是独立样本成功率或success–attention frontier效果矩阵。

## 新版身份与审批范围

- 整包：`guidance-adoption-v1.1-dev`。
- Robosuite最终控制字节（v1）：`405f752968f73e0e0541b5a51e0cb201069f07c2dcfb3f26a5d0cb45deb767f2`。
- RoboCasa最终控制字节（v1.1）：`c016bed6d2a85eb2cd1a972299a2f38394a63780fc537f4b19e83991a6c015d7`。
- 只读证据审计：`855084d55ed6881fae94f54446304f4fd1c441154b4621402ba4bf29294a3a8a`。
- 审批绑定外层 sha256_manifest.json 的确切 SHA，以及以上两份控制字节。旧基础策略审批、旧准入formal_eligible身份均不得沿用。
- 本次待审批只确认该新版本的**有界开发验证与SHA冻结**；不授权继续运行、不授权350格/held-out、不签发正式准入。

## 复核与原件

`frozen/final_policies/` 是最终执行控制源码；`frozen/versioned_source/` 保留v1及v1.1和驱动/审计工具。`frozen/inputs/` 含锁、原配置、原基线、新demo及Memory文本。`frozen/evidence/` 含逐attempt审计、ledger、全旧文件保护索引和测试。

`frozen/raw_development_evidence.tar.gz` + `frozen/raw_manifest.json` 收录本轮完整原件，含失败、数据库、Service日志、OSC动作回执及RoboCasa完成job日志。部分RoboCasa临时执行wrapper由已有Agent recorder清理；可得到的wrapper/状态样本已额外收录，无法得到的明确记missing。四类必要主产物无缺失，实际job完成序列及独立Safety状态采样保留。

使用 `python verify_frozen_package.py <本包绝对路径> --verify-raw` 只读复核包和归档内每文件SHA，不启动机器人。

首次离线测试fixture误用了无base能力的共享SDK，14失败/22通过，修正为现有RoboCasaMobileSDK后通过，记录保留。首次机械审计因demo快照路径重定位而1/204未过；按已批准资产SHA和实际轨迹内容核验后204/204通过，原失败审计也保留，未重跑机器人或改原始证据。

## 下一步

审批完成后保留该新冻结身份。本轮不继续测试。RoboCasa在较短进路后仍只读规划拒绝，后续需另行设计可达进路/抓取动作验证；一般Advisor语言覆盖、真实示教录像、full条件Memory选择及多seed效果均未验证，不能在本包中预宣称收益。当前其动作变化但成功未改善，按设计问题记录。
