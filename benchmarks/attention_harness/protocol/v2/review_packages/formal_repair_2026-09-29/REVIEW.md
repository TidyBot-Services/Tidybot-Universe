# 双任务五 seed 修订基础策略审阅包

状态：工程整包待审；旧 seed 101 两例和 121 件冻结证据不替换。本包仅供新的开发链审阅，`formal_eligible=false`。

v2.1 协议原件与 v2.2 工程预算修订均保留。`protocol_v2_3_candidate.json` 明确本轮属于新修订：旧链失败结论不改，新链开始后也不允许按结果换策略。正式实验准入仍须独立审计。

- Robosuite：改用公开 SDK 局部控制器，修复旧策略把 `move_delta` 单步当成到位的错误。seed 9001 工程 smoke 完成，Safety 0，原生任务失败。
- RoboCasa：先用公开目标位置对齐底盘，再用公开 SDK 手臂目标运动。旧修订在 smoke 9001 曾发生 `action_outcome_unknown`，已硬停并保留；当前修订一次 smoke 完成、Safety 0、原生任务失败。未知动作仍由独立 Safety 停止。
- 两套策略的观测／动作白名单、选择依据和风险分别在各自 `review.json`；生成历史在 `generation_receipt.json`。这些修订明确使用了旧失败及工程 smoke 作诊断，不能冒充最初未见结果的候选。
- `budget.json` 固定四次 attempt、单次 120 秒、整例 300 秒、一次 Advisor credit、4096 tokens、每次最多 200 SDK 调用；`software_versions.json` 固定 Service 提交及关键执行文件 SHA。
- 两任务各五份固定配置、M1 锁和对应摘要在 `chain_lock_manifest.json`；完整逐文件 SHA 在 `sha256_index.json`。
- 六次 Service 工程检查的原始证据及完整审计位于外部 `smoke_evidence_audit.json`，SHA 由 `validation_report.json` 固定；其中一次 unknown 不隐藏、不计入新链。

## 精确锁定摘要

| 任务 | seed | 策略 SHA-256 | 配置 SHA-256 | M1 身份 SHA-256 |
| --- | ---: | --- | --- | --- |
| robosuite / cube_lift | 101 | `fa1eff2f0bf938fc8dec6ad1bcf58c1db481a00332f59e2108bcfb94d93ee6c8` | `31ff175cfea8fb7e07097bb78f8293c457b62270ebc5143f3719b3a10adae98a` | `cbd813b5fb2524d59404ced3940c9697a423df739692e0b24c842844c5b20e8d` |
| robosuite / cube_lift | 102 | `fa1eff2f0bf938fc8dec6ad1bcf58c1db481a00332f59e2108bcfb94d93ee6c8` | `484878c18e5a8956260982d05ef8eeab6ab46da075f7c68b3acbecade85c242d` | `a535c3307af8181cbb283526671008bb4e5d283fce920d2950ef5b157dae9802` |
| robosuite / cube_lift | 103 | `fa1eff2f0bf938fc8dec6ad1bcf58c1db481a00332f59e2108bcfb94d93ee6c8` | `1a0953f5c5311e3e69f2ddee99781e0f912b6add87847e0b8c5321c320e7ff1d` | `73a08a78c18abb4b2d46a88dc328d563b521106aa9f4b7e4a142f4adc2491f03` |
| robosuite / cube_lift | 104 | `fa1eff2f0bf938fc8dec6ad1bcf58c1db481a00332f59e2108bcfb94d93ee6c8` | `908524bba2e6cf4b3e0e7bea1a6a1afcc2b95c9585bd9451d3a7609787000fd0` | `72df25699b9642db4821d76aa6d9b381e02ce6203255bf489a2f79cce4d84238` |
| robosuite / cube_lift | 105 | `fa1eff2f0bf938fc8dec6ad1bcf58c1db481a00332f59e2108bcfb94d93ee6c8` | `17328bd0975ae1b932db494d118a249bd9b965afebaebd26042e39258713353b` | `5ef8490f01a12a58779b9a70f11ea8fa36e9234a5bddceadcaf017d16ad0d9d7` |
| robocasa / counter_to_sink | 101 | `43cefacfba35a4f7c1bdece690bf2b8a6f90e80821e066a4111e1216613a8cae` | `fd32f0dfde74c6a754edf8e0b4ba78f94f733762e226bec2af177341edb2cc27` | `4a1c1495a4bd02ed2475ae1506cdb356fa87e3293ec5431a75211df07d58c6ac` |
| robocasa / counter_to_sink | 102 | `43cefacfba35a4f7c1bdece690bf2b8a6f90e80821e066a4111e1216613a8cae` | `e56b73faea946fe2f9acfea378b3253c06cd5a9a13749480e64679354fa4cd4a` | `58b0ed0be5eb059d2c7645f7f6742e5587f5e3ee1514c7e4266c07c936d3dc7b` |
| robocasa / counter_to_sink | 103 | `43cefacfba35a4f7c1bdece690bf2b8a6f90e80821e066a4111e1216613a8cae` | `a0358fc37b4632ebc83dc6df2cffdd4255b0fe034427b6299eeaa7f68e2166d3` | `cedf62f92fe265007421e8b70709b82c9d0f5bc54ea11ecd1321072a907f4d3a` |
| robocasa / counter_to_sink | 104 | `43cefacfba35a4f7c1bdece690bf2b8a6f90e80821e066a4111e1216613a8cae` | `abe89f86bab4e179a2faf7ecbf56bf9f4f0ecfc0535679babf4332d87e7df384` | `d05c688f795de3df265b516dbbeeafb3b9736dedb392fd54980284425d7fd873` |
| robocasa / counter_to_sink | 105 | `43cefacfba35a4f7c1bdece690bf2b8a6f90e80821e066a4111e1216613a8cae` | `77f5cdc5b07d156ac66583a8c5c787326aae3fe11a019e7bf2ef3c8ca64b071b` | `76caf4fb795ffe548cb94218d3649272334fb13beea28ca86eb63b00a6637fe5` |

即使整包获批，也只允许按新版本边界考虑后续开发链；本审阅包没有执行 101–105、25 seed、held-out 或七策略比较，也没有设置 `formal_eligible=true`。
