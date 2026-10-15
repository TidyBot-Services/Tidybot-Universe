# 批准任务 Skill DAG

当前 guidance v1.1 开发批准策略各是一份整体 Public SDK 程序。`robosuite_cube_lift.json` 和 `robocasa_counter_to_sink.json` 各定义一个无依赖根节点，锁定当前策略 SHA；没有可独立调度的子技能，不将内部 `approach` 函数或 SDK 动作虚构为节点。`status=done` 表示当前开发策略定义已交付，执行与原生任务结果独立由每次 attempt 记录。

`live_run_catalog.json` 是 UI8769 的两种当前 Robosuite 相机配置，增加 `require_skill_graph=true` 和三个显式绑定字段，其余批准参数保持。路径依赖本机现有批准包和1080p配置。RoboCasa 使用同一绑定字段和冻结/快照机制，测试隔离验证，不启动 RoboCasa 正式任务。

版本规则：策略变化必须创建/审核匹配的任务定义并更新图 SHA。启动前校验源字节和任务/策略身份，冻结 content-addressed manifest；实际派发在同一 SQLite 事务记录 attempt 快照。历史 run 无同期快照时显示缺图，不能回填。细分技能实时事件接入为独立后续工作；formal_eligible=false。

任务图绑定的是 task/policy 身份；根定义中的 formal_config_file 是已有64x64基准入口。UI选择的64x64或1920x1080批准相机配置由各自entry_lock与config SHA独立冻结，不改变该任务的技能拓扑或策略。
