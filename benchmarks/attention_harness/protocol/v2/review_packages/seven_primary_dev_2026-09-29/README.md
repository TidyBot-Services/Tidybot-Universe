# 七条件主开发集冻结 review 包

本包仅冻结本开发集主实验：2任务、seed 101–125、7条件、每格1次，共350入口。本轮只生成并校验文件，执行数为0；held-out（未来10例）与Memory+evidence消融另行冻结。总体准入由独立审计逐项判定，冻结输入不等于授权运行。

每任务七条件共用一份已批准机器人策略，policy.py保持原始字节；配置按每任务每seed共享。M1锁绑定代码、配置、demo/k/随机输入以及Memory合同。软件版本文件绑定Universe执行代码commit与五个Service确切commit；冻结文件的交付commit与执行代码commit分开记录，避免自引用。sha256_manifest.json列出本包所有冻结文件字节SHA。

- demo_first使用经独立审核的公开SDK动作前缀，来源运行原生失败；它是固定先验输入，不声明成功示范或策略有效。
- retry_k_then_ask的k=2；随机条件以固定quota=1、3个失败槽、seed及SHA排序预注册，早停产生的实际求助不足如实记录。
- 六个非full条件Memory可见范围为空；full仅可取任务对应可信v1，原有作用域不扩大。每任务101/103/105匹配，102/104相机不匹配，其余种子在原作用域外。没有新增最低覆盖率或原生成功门槛。
- 每run从规定初始状态开始：独立SQLite副本、独立证据副本、空Advisor缓存；矩阵期间禁止自动晋升、版本回滚或生命周期修改。缓存命中仍计1 credit、2秒逻辑延迟，新provider token为0；非缓存按实际usage记账。未知费用保留，不能当作0。
- 4 attempts、每attempt120秒、全run300秒、1 credit、4096实际token、200个已派发SDK调用跨attempt聚合。provider最多一次HTTP/格式尝试。完整异常与超限成本保留，禁止按结果择优或补跑。

execution_contract.json及逐入口launch_arguments.json仅供审核，均execution_authorized=false；动态artifact-root必须是包外全新目录。运行前须另获总体准入PASS、复核完整SHA与确切干净版本。当前缺失的Safety负控实测不由冻结文件替代；具体逐项结论见本轮独立总体准入报告。

固定基础策略的输入消费范围见[semantic_limitations.md](semantic_limitations.md)：两任务控制代码均不读取attention_input，当前仅可解释求助决策、记账与输入曝光/授权记录，不能据此声称控制代码采纳或改善任务结果。
