# RoboCasa Safety 负控修复与准入重签

验收完成：`formal_eligible=true`，用户7/7关口、canonical 9/9子gate全部PASS；无未过项。签发范围仅本开发集主实验。原六个PASS对象和证据SHA不变，矩阵、held-out、消融执行均0。

- [总体重签与逐项结论](independent/overall_admission_audit_v3.json)，SHA-256 `ad66e2cd7da547e0450a77403cf2d993a9913c7512e6e48c1a84e5ffa654d0b6`。
- [当前C受控负控审计](independent/negative_control_post_run_audit_v3.json)，SHA-256 `7c25bd5b7c69b966a339de822ff7c19fa2b0b33e1760cd4dc3a67c965dbc0636`。
- [完整结论、身份根因与时延](CONCLUSIONS.txt)；[逐项报告](independent/overall_admission_report_v3.md)。
- [新运行前计划](validation_plan_v3_1.json)及[通过的pin](independent/negative_control_pre_run_pin_v3_1.json)。旧计划SHA拒绝在独立文件和preflight_rejection源码副本保留，0 Service启动。
- [注入收据](attention-robocasa-safety-negative-v3/injection_receipt.json)，同目录attempts保留Trace/Safety/sandbox/native四原件和Service日志。
- [12项身份/Safety回归](tests_identity_and_safety.txt)与[5项审计反例测试](tests_audit_predicates.txt)。
- [逐文件SHA清单](sha256_manifest.json)；运行`python3 verify_delivery.py`只读复核交付副本、1411份旧原件与当前Service进程组状态，不启动模拟器。

身份修复代码为仓库的`benchmarks/attention_harness/robocasa_native/controlled_negative.py`，定向测试为`tests/test_robocasa_controlled_negative.py`。由station所在目录派生run/attempt:0，保留生产校验；所有原runner/Safety/Service字节未改。

唯一真实负控为seed101夹爪close job真实完成后丢失后端结果确认，独立Safety记`action_outcome_unknown`，worker失败且第二动作0。检测0.178ms、worker停止1.633ms、双Service回收0.532s，符合预设≤1s/≤15s。停止reason保持真实`normal_cleanup`；审计要求完整注入/原Safety错误/worker停止/无后续动作/有界回收链，普通cleanup不能替代该链。本证据不声明任意危险运动或碰撞检测能力。

此目录是外部原件 `/home/truares/桌面/attentionbench-safety-repair-20260929` 的逐字节交付副本。原件中的绝对路径不改写，manifest提供源路径映射；复制后可用verify_delivery验证。保留旧失败 `a1aefe83…`、旧总体 `334019e6…`、历史28份Safety与当前R14负控，未覆盖或重新运行。不要重启negative_control_v3.py：本轮唯一测试已结束，目录拒绝重复运行。

下一步是交付/审阅本次证据；本轮安全收尾，不自动开启新轮或执行效果矩阵、held-out、消融。既有基础策略Attention输入消费限制、Memory范围与depth根因unknown保留。
