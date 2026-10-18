# Dev v2 Eval 与注意力汇总

实验模式创建时要求显式配置 `eval_agent.enabled: true`。缺失或关闭会在创建账本和产物前报错；`interactive` 与 `diagnostic` 模式保持原行为。已有的视觉不可用后使格子无效的运行时规则未变。历史实验仍可只读分析，不会被追溯修改。仍强制关闭 Eval 的旧 DevMainBridge 实验入口会被新门禁拒绝，须先完成角色接线才可创建实验。

汇总命令（只读打开输入 SQLite；仅向指定输出位置写 JSON 和 Markdown）：

```bash
python -m benchmarks.attention_harness.summarize_experiment \
  --store /path/to/store.sqlite \
  --experiment-id devexp-... \
  --output-prefix /tmp/attention-summary
```

一个库只有一个实验时可省略 `--experiment-id`。读取器从 `v2_experiments` 的 manifest 引用、`v2_cells`、`v2_versions`、`v2_jobs` 关联数据。执行作业 `receipt.bundle` 指向 `vN/bundle.json`，其中 `artifacts.native_result` 与作业的 `receipt.native_result` 必须一致；原生文件只有 `evaluated: true` 且 `native_success` 为布尔值才进入分母。Eval 作业的 `receipt.eval_review` 指向 `vN/eval-review.json`，只有 `status: completed`、`mode: vision` 和布尔 `passed` 才进入混淆矩阵。所有读取的 JSON 引用均校验 SHA256；缺失或不一致的产物在 `read_errors` 中列出，不会被判成失败。

`native_success_rate` 是格子内任一执行版本原生成功的格子比例；`final_version_success_rate` 只在最后生成版本已执行且原生评估完成时取其结果。未知结果不算失败；场景合约失配、Eval 视觉丢失和其他无效原因的格子分别按策略计数。首次成功版本及从格子创建到成功执行收据结算的秒数逐格列出。注意力请求单位含已结算 AdvisorProxy 调用以及无对应已结算调用的超时请求；逻辑人工秒数只从已结算的 `hint_human_seconds` 取值，未知秒数的请求另列为 `attention_seconds_unknown_requests`。Eval 的调用、token 和秒数在 `eval_compute` 单列。`frontier_plot_data` 在每个预算水平只用各策略都有已知结果的相同任务、场景和重复编号给出配对绘图点，`nondominated_policies_by_budget` 只在逻辑秒数完整时列出双注意力分量均不被支配的策略；`marginal_utility_by_request` 按实际请求数给出相邻桶的描述性成功率差，每请求边际值不声称因果效果。请求超时取 planner 请求的 `timed_out` 状态；执行超时另列为 `execution_timeouts`。回退数包括全规划器选帧失败后的 generic evidence 回退和 planner/model answer 回退；其他策略按设计发送通用包不计为回退。

Eval 质量报告给出四格表（Eval 通过为阳性、原生成功为真值）、误判通过和误判失败、文字回退与不可用、未评估执行版本、各策略误判通过数及三种比例（原生失败版本中的误判通过率、全部可比较版本、Eval 判通过版本）。只有因末版视觉 Eval 误判通过且 `reason: eval_passed` 而停止的格子进入 `false_stops`。

历史诊断运行可能没有 Eval 作业，因而只有原生结果、Eval 不可用；不能从这类数据推断 Eval 的准确率。当前产物仍标记 `formal_eligible: false`，汇总不改变准入状态。
