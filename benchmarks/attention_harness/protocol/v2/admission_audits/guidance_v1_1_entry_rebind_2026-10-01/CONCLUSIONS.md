# 新 guidance v1.1 身份绑定与准入重签

验收完成：新身份冻结包已 review 并按本轮用户明确指令记录批准；总体重新签发 `formal_eligible=true`，用户关口7/7、canonical关口9/9 PASS，未过项为空。判定仅限本开发身份和原准入证据范围；`execution_authorized=false`。

## 交付

- 新包：`/home/truares/桌面/Tidybot-Universe-attention-native/benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_guidance_v1_1_dev_2026-10-01/`。
- 两套基础策略、50份配置、350份M1 entry lock、350份launch身份以及1290文件SHA清单。25个dev seed（101–125）×2任务×7条件；无效果执行。
- `REVIEW.md`、`identity_rebind_index.json`、`operator_approval.json`、逐entry机械review、11项专项测试及只读`verify_package.py`。
- 总体审计：本目录`independent/overall_admission_audit.json`。

## 逐项结论

| 要求 | 结论 | 实际证据 |
|---|---|---|
| 新策略身份严格绑定 | PASS | 350份锁经原`inspect_formal_entry`重新计算；每份canonical只改变`approved_policy_sha256`和计算摘要。50份配置与所有条件输入SHA保持原值。 |
| launch与M1对应 | PASS | 350份launch按原CLI参数声明解析，与锁完整比对；原`request.validate`接受新锁、拒绝旧策略锁。receipt源位置、策略SHA、条件、seed、预算、Service根、Python实际字节均一致。 |
| 身份检查保留 | PASS | 398份原runtime源码及原boundary/CLI/Memory验证器逐字节不变；当前Safety请求helper另按上轮精确SHA锁定。本轮生产代码修改0。 |
| 冻结与review | PASS | 1290文件完整SHA索引，350/350机械review；11 passed，覆盖旧SHA、旧entry、代码/配置/条件篡改、held-out拒绝、单调用guard、Memory及generation路径。 |
| operator批准 | PASS | 本轮goal明确要求生成→review→批准→重签；sidecar引用该原话及其SHA，绑定新REVIEW/manifest/两策略的精确SHA和UTC/上海时间。没有伪造另一条人类事后确认SHA的发言。审查为作者机械review，角色如实记录。 |
| 原六用户关口复用 | PASS | chain/profile/depth/任务可解性/Memory/当前Safety负控原对象逐字段保留；另外7个canonical对象不改；无任何重跑。 |
| 版本适用性 | PASS | 策略字节等于原已批准guidance；上轮完整无指导AST桥继续适用；新350身份填补原M1/launch错配。原错配审计及false判定保留。 |
| 总体准入 | PASS | 仅重判`policy_identity`、`seven_condition_approval`；全部关口、身份批准和完整性联合推导true，未直接改旧布尔。 |

批准来源是本轮人类直接goal所授予的生成、review及批准工作；不是宣称人类随后另看过新SHA，也不是冒充外部独立审查员。原guidance开发批准仅证明代码来源；新身份批准明确另记本轮精确包。

## 精确SHA

| 对象 | SHA-256 |
|---|---|
| 新REVIEW | `a83605415c146694d3c1b2b4116805db51477253f07ca396edd53450b3558c47` |
| 新逐文件manifest | `32febf630ff2d6a979e9eabec50152ff9ac887721ab3eef372b30434c7160d5e` |
| 新operator_approval | `10d29fece3c46b5327fd09b3900d779fe7b1e225965441886d31b69e329346e3` |
| 新总体重签审计 | `620867e88541c647869be69fa558e7de1af5ab3ae3d4491fc0a74f3d2c2bfc9d` |
| R控制策略 | `405f752968f73e0e0541b5a51e0cb201069f07c2dcfb3f26a5d0cb45deb767f2` |
| C控制策略 | `c016bed6d2a85eb2cd1a972299a2f38394a63780fc537f4b19e83991a6c015d7` |
| 原已批准guidance REVIEW | `090a190f325a23a138003456c41ee436c3f8e85605584b7ca0c7ba21615981c7` |
| 上轮当前Safety负控 | `59ab3797ecd3557cc933c9c357575c113ee80a665467c2c8b8d66179ee651ce6` |
| 上轮总体false审计 | `a68f8fec50b9de3463b5e5612133fe1810428bf6f07b869324607c9fa7da3b9c` |

## 保留的边界

当前Safety仍为已完成的v1.1真实负控：检测0.214ms、worker停止1.824ms、第二动作0、双Service回收0.524s。本轮仅复用其完整SHA和无指导AST桥，没有重新注入。

RoboCasa“动作改变但成功未改善”、旧Memory未采纳、历史depth根因unknown、原生成与review lineage及所有失败保留。新generation receipt如实说明来自已批准控制修订，未重新调用模型，未将旧工程结果写成新策略结果。Memory合同指向原冻结authority，snapshot、范围、版本与费用不变。

原3033个证据/冻结/源码保护项无漂移；旧七条件与guidance包未改。工作区先前未提交改动完整保留。新runtime身份以原398源码字节加上已通过Safety的请求helper精确SHA冻结，不把整个工作区描述为干净HEAD。

生成末尾一次复制review工具缺目录的失败、首次测试5项错误预期文案/类型的失败输出均保留。所有拒绝原本有效，修正的是测试断言；11/11最终通过。原350身份没有重生成，没有任何Service/robot启动。

## 下一步

本轮执行0：Service、机器人、Safety/六关口重跑、350格效果、held-out、Memory新探测/晋升、模型生成均0。安全收尾完成，不自动下一轮。

新批准与总体准入不授权效果执行。若后续运行，需要另行明确授权执行计划及范围；先审阅本次精确身份和准入交付。`formal_eligible=true`不表示效果已验证，也不把已知RoboCasa设计限制写成成功改善。
