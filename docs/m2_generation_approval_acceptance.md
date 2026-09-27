# M2 生成、批准与 Bridge 封闭工程验收包（2026-09-28 冻结）

基线：U `1062d7906e434a4088c9d8321d37bda0728b7314` 的 M1 契约见 `m1_entry_acceptance.md`。本包只增加 Graph → 有界 GLM Dev → 人工批准 → 正式 AttentionBench Bridge。保持 M1 lock 结构和 `formal_eligible=false`，不修改已验收的 M1 输入、拒绝矩阵及四类产物定义。

## 范围和通过标准

RoboCasa `counter_to_sink` 与 Robosuite `cube_lift` 各取开发 seed 101、`sim_gt`、`autonomous`、1 attempt、0 credit，Graph 读取各自 M1 同条件 lock。真实 `parcc/GLM` 至多两次格式纠正调用，每次客户端至多一次 provider attempt；保存公开 SDK 源码、源码 SHA、调用次数、模型 token 用量、prompt SHA、生成收据和实际 hypothesis（未产出记 `unknown`）。生成后 Graph 持久停在待人工批准状态，不能建 run 或启动 Harness。批准必须由用户对本次源码、配置、M1 lock 的精确 SHA 明示；Graph 布尔字段或 Dev 文本不构成批准。Bridge 核对批准身份与文件字节后只调用 `formal_attention_cli`，下游 M1 lock、code/config SHA 与 graph 一致。每 suite 仅一次真实正式 Service 开发 seed 交接；原生成功不设门槛。

拒绝矩阵：未批准、伪造或不匹配审批身份、批准后源码／配置／M1 lock 改变、suite/task/seed 错配、收据与源码不符、源码／配置／收据路径越界、无效公开 SDK 策略、重复派发、启动中进程重启，均须在未批准代码执行前拒绝或保守恢复。重启保留待审批及批准身份，已完成同一派发不得重复启动；中途状态不明确时停在人工审查，不自动再跑。测试 fixture 可模拟批准来验证门禁，但不得充作现场批准或真实 GLM 证据。

## 证据和依赖

证据目录 `/home/truares/桌面/attentionbench-m2-20260928/`：冻结本包、U／R／C／A／T／M 版本与工作树状态、Graph 原始状态及 HTTP 日志、每次 GLM 原始响应／usage 与收据、代码／配置／lock SHA、用户批准记录、Bridge 命令与 stdout/stderr、下游 run／attempt／entry SHA 和 Service 原始日志、自动测试命令和输出、重启前后状态及审计结果。缺任一关键证据不得标完成。

跨仓库依赖：U 正式 CLI 和 Bridge；R `robosuite_sim-service`；C `maniskill_sim-attention-variation`；A Agent Server checkout；T RoboCasa task checkout；M `attention_memory_service` 为安装依赖，本包不验 Memory 功能。各仓库版本和脏改动逐一记录，不能以旧证据代替本次运行。真实 GLM 连通和用户对两份精确 SHA 的批准属于外部关口；不可用则报告部分完成及阻塞，不以测试回复或自行批准冒充。M3 稳定性、M4 七策略效果、Memory 晋升、真人、held-out、正式实验均排除。

## 逐项结果

**M2 封闭工程验收完成，仅限本包范围。** 用户提供的密钥仅经临时环境变量进入生成阶段 Graph 进程；未写入仓库、配置、命令记录或证据，正式 Harness 子进程不继承密钥。已在 `https://litellm.parcc.upenn.edu/v1/chat/completions` 完成一次有界 `parcc/GLM` 聊天探针（1 provider attempt，41 tokens），然后双真实 Graph 各生成一份公开 SDK 源码。两套均为 1 次格式调用、1 次 provider attempt；审批前 Graph 持久停在 `review`／`awaiting_approval`，无 Harness run，重启 auto-start 均为 `spawned=[]`，未批准正式 Bridge 命令被拒绝。用户随后针对上一条列明的双套精确源码、配置、M1 lock SHA 回复“批准”；外部 operator 记录绑定该会话审批来源、Dev 收据和精确摘要。之后双 Graph 各经正式 Bridge → Service 完成一次 seed 101 handoff；派发后重启 auto-start 均为 `spawned=[]`，没有第二个 run。最终只读审计：`/home/truares/桌面/attentionbench-m2-20260928/audit_final.json`；逐文件索引见同目录 `manifest_final.json`，审批前原始索引仍在 `manifest_preapproval.json`。

| 待用户审阅的候选 | 源码 SHA-256 | 配置 SHA-256 | 生成后 M1 lock SHA-256 | Dev 用量／hypothesis |
| --- | --- | --- | --- | --- |
| Robosuite `cube_lift` seed 101，`m2_robosuite_seed101_generated.py` | `83368aae752803dff84d7e6a447d8d5978fed157ba0518f94657b589b8ad1dc8` | `31ff175cfea8fb7e07097bb78f8293c457b62270ebc5143f3719b3a10adae98a` | `97d1819615d5c337c0a256f45822924143f31bb0de0fef0e7d89e1616a1909a1` | 380 tokens；`unknown` |
| RoboCasa `counter_to_sink` seed 101，`m2_robocasa_seed101_generated.py` | `124d975c40b8ab542310e368c0d06adfc110829e6381a8247409d41ca521a7cc` | `fd32f0dfde74c6a754edf8e0b4ba78f94f733762e226bec2af177341edb2cc27` | `a3ff0dcb629a076446b4763b540d79f98a467f7bca073753186bf93fe5f6c399` | 450 tokens；`unknown` |

| 冻结项 | 当前结论 |
| --- | --- |
| M1 输入锁及契约保持 | 通过：两套相同条件旧代码／配置重算 lock 分别仍为 Robosuite `512796d4…`、RoboCasa `cf83204d…`；M1 受保护文件与基线提交无差异，M1＋M2 专项 45 passed。 |
| Graph 接收双 suite 开发任务、未批准时不执行 | 通过现场核对：双 Graph 各派发一次 Dev，生成后均停在 `awaiting_approval`；重启 auto-start 均不重派，未建 run。 |
| 真实 GLM 公开 SDK 生成、usage／脱敏回复／hypothesis | 通过：先做有界 HTTPS 聊天探针，再由双真实 Graph 生成；两套调用数均 1／1，上表摘要、脱敏响应及生成收据已持久化。无实际 hypothesis，明确记 `unknown`。 |
| 人工精确 SHA 批准前暂停、伪造及篡改拒绝 | 通过：审批前现场 Bridge 拒绝未批准候选；用户对上一条双套精确 SHA 明示“批准”后才创建 `approval-robosuite.json` 与 `approval-robocasa.json`。外部 operator 记录绑定源码、配置、Dev 收据和 M1 lock。Graph 布尔字段、错误审批、源码／配置／收据／任务变化、路径越界、无效策略等自动拒绝测试通过。 |
| 正式 Bridge、双 Service 各一次、批准后重启恢复 | 通过：Robosuite `run:attention-robosuite-cube_lift-seed101-46f6aaa17d8c`／`attempt:attention-robosuite-cube_lift-seed101-46f6aaa17d8c:0`，RoboCasa `run:attention-robocasa-counter_to_sink-seed101-34dd62a7d395`／`attempt:attention-robocasa-counter_to_sink-seed101-34dd62a7d395:0`。两份 `bridge_command.json` 均只调用 `formal_attention_cli`，传入原批准源码／配置和 `--expected-entry-sha256`；下游正式结果 lock、源码／配置 SHA 与 run／attempt 身份均与 Graph 一致，Service 原始日志及四类 SHA 产物齐全。两次原生任务均失败，CLI exit 1 与原生结果一致；这是允许的开发 handoff 结论。Graph 只登记本次交接，不调度 Memory／Eval。 |
| 重复派发／中断恢复 | 通过：首次派发前持久认领。双 Graph 派发后真实进程重启，auto-start 均为 `spawned=[]`、保持同一审批记录 SHA／run 身份、Bridge 命令未改且每套仅一个 run 目录；中途不明状态的代码测试停在 review，不自动再次执行。 |
| 自动回归 | 获批交接后 M1／M2／Bridge 相关集 **68 passed**；旧 Graph **84 passed、0 failed**。全 Harness 独立复跑仍记录 **345 passed、10 skipped、7 failed**：`test_memory_v2.py` 的 6 个 trusted Memory 上下文测试和 `test_robocasa_generated_policy.py` 的 1 个 trusted Memory 策略测试；这些不导入 M2 Graph／Dev／Bridge，本轮未修改受测 Harness 文件。原始失败输出保留于证据目录，不把全 Harness 说成通过。 |

另有隔离的**测试回复**真实 Graph 进程探针：本地 fixture HTTP 服务返回固定公开 SDK 代码，Graph 写入 Dev 原始回复、usage、生成收据和候选 lock 后停在 `awaiting_approval`，没有 run；重启再次 auto-start 为 `spawned=[]`。源码已从仓库移入证据目录，与真实 GLM 验收严格区分；该探针不满足本包的真实生成、人工批准或双 Service 条件。

RoboCasa 正式 Runner 的 `approved_config.json` 快照将中文路径写成 JSON Unicode 转义，因而快照字节 SHA 为 `04c5e91bbabf0fb0e97888f48a11eee31d78744af35f29afda37edf30c24e682`，不同于批准的原配置 SHA `fd32f0df…`；两份 JSON 解析后完全相等。Bridge 的 CLI 参数指向原批准配置，正式 result 的 `config_sha256` 和 M1 lock 均仍为批准 SHA。此处只记录已验收 M1 的序列化行为，不改 M1 契约。

审批记录属于外部操作员信任边界：本地 JSON 只能校验身份一致性，不能对有权写任意本地文件的攻击者提供密码学签名。若要抵御这种本机攻击，需另接独立签名批准服务；本包将“伪造审批”验为 Graph 字段、错误记录及不匹配摘要不能触发执行。用户实际审批与测试 fixture 分开保存。`formal_eligible=false`；本包不宣称 M3 稳定性、M4 七策略效果、Memory 晋升、真人、held-out 或正式实验通过。
