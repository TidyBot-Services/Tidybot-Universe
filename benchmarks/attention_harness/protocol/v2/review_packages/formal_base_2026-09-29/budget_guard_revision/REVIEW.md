# v2.1 执行预算修订候选（待独立审计与批准）

原 v2.1 SHA-256：`d2b5a68d7943b6cdf382dcc0217d99d72ff3d00ccb56f9a729d66ee42f9c0124`；已批准 101–105 锁索引 SHA-256：`e23a3cc42b85fcca31252d6ece584231606083afe1a9318c28807c97a54ffb20`。两者均未改动。当前分支 `feature/attention-native-robosuite`，HEAD `fbf0887`；本包源码为未提交候选，逐文件哈希见 `sha256_index.json`，最终执行前还须冻结精确软件版本。

## 差异与理由

旧正式链把 M1 的 `overall_deadline_seconds=300` 直接传给每个 Runner 和调度器，形成每次最多 300 秒、四次最多 1200 秒的解释，与 v2.1 的单次 120 秒、整例 300 秒相冲突。候选保留原 M1 身份及四次上限，增设显式单次 120／整例 300 参数；两套 Runner 收到单次截止，调度器以单调时钟停止新增尝试，收据必须匹配已下发的截止。每次启动前预留 10 秒给 Service 清理和产物收尾；超过整例上限要记录为无效并停止。策略源码、配置、生成收据、十份 M1 锁、已批预算和种子均不改。

## 验证与风险

`validation.json` 记录 `git diff --check` 和 91 项定向 pytest 通过，包含四次尝试、上限拒绝、错误收据拒绝、到期前零派发。所有验证为静态／合成小测试；本次 0 个真实模拟器 seed case。整例壁钟检查依赖 Runner 对取消与进程组回收的及时响应；若外部 Service 停顿超出余量，依协议保留证据并判无效，不能追认。RoboCasa／Robosuite 真实链路在此候选下尚未验证。

`formal_admission_v2_2_candidate.json` 只是一份新版本提案。v2.1 明定后续软件改动须另起不可变版本并经独立准入审计，因此本候选在独立审计和你对精确 SHA 的批准前不执行 101–105；25 seed、held-out、七策略比较继续禁止，所有产物 `formal_eligible=false`。
