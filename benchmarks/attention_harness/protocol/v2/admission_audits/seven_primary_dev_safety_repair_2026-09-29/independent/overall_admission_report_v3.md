验收完成，重新签发 `formal_eligible=true`；用户关口7/7 PASS，canonical gate 9/9 PASS，未过项为空。判定限原签发的本开发集主实验范围。

| 用户关口 | 结论 | 处理 |
|---|---|---|
| chain | PASS | 原PASS及证据SHA原样复用，0复跑 |
| depth | PASS | 原PASS及证据SHA原样复用，0复跑 |
| memory | PASS | 原PASS及证据SHA原样复用，0复跑 |
| profile | PASS | 原PASS及证据SHA原样复用，0复跑 |
| safety_negative_controls | PASS | 新C负控补齐；复用28份Safety和R14负控 |
| seven_conditions | PASS | 原PASS及证据SHA原样复用，0复跑 |
| task_feasibility | PASS | 原PASS及证据SHA原样复用，0复跑 |

新C负控：1真实attempt、1动作、1完成job、1次独立 `action_outcome_unknown` 检测。检测 0.178 ms，worker停止 1.633 ms，双Service回收 0.532 s，预设≤1s/≤15s通过。第二动作0。四类原件、注入/job/worker/Service收据与日志均保留。原停止reason仍为normal_cleanup；覆盖结论还要求注入→原Safety错误→worker失败→无后续动作→有界回收的完整因果链，普通回收单独不能通过。

修复仅新增独立负控请求构造器：由station所在目录派生run/attempt身份。生产校验与runner/Safety/Service未变；12项定向回归+5项审计反例测试通过。先前负控失败、此次运行前旧源码pin拒绝均保留。
保护1411份旧证据及源码，重签SHA核验无漂移。其余6用户关口（8 canonical子gate）完全复用；新chain/profile/depth/Memory/七条件测试、矩阵、held-out、消融执行均0。

[总体审计](/home/truares/桌面/attentionbench-safety-repair-20260929/independent/overall_admission_audit_v3.json)，SHA-256 `ad66e2cd7da547e0450a77403cf2d993a9913c7512e6e48c1a84e5ffa654d0b6`。
[新C负控审计](/home/truares/桌面/attentionbench-safety-repair-20260929/independent/negative_control_post_run_audit_v3.json)，SHA-256 `7c25bd5b7c69b966a339de822ff7c19fa2b0b33e1760cd4dc3a67c965dbc0636`。
[完整结论与修复说明](/home/truares/桌面/attentionbench-safety-repair-20260929/CONCLUSIONS.txt)。

本轮结束；Service及控制器已退出。下一步仅交付和独立审阅本次新证据，不自动开启新一轮。准入签名不自动授权效果矩阵、held-out或消融执行。
