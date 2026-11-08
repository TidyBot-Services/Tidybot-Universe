# 方案：场景合约的主机变体指纹（seed 102 / qifei，2026-11-09）

背景与根因见 `docs/REPORT-seed102-qifei-20261109.md`（main f4f6502）。简述：同一份代码、同一个场景，
qifei（Zen 5 + glibc 2.39）和 truares（i5‑14600KF + glibc 2.35）在数学库上有 1 ULP 的差，seed 102 复位时
`distr_counter` 没站稳，把差放大成取整后的指纹不同。用户 11-09 选择报告里的方案 1：按主机登记指纹。

## 目标

- qifei 上的 seed 102 场景被判为 `verified`，并在结果里写明匹配的是哪个变体。
- 判定仍然是**按位相等**（6 位取整后的 sha256），不引入数值容差，不放宽任何检查。
- `registered_contract_ref()` 的返回值**不变**（还是 `dev-seeds-101-103-v2-20261019-kp1500.json`，`bb28cd3e…`）。
  原因：Memory 导入（`dev_v2_memory_import.py:190-194`）、`dev_planner_memory.py:53`、fork 等用 `==` 比合约引用；
  换文件会让所有旧运行和笔记失去可比性。
- 新主机上线时，`verify.sh` 对**所有**已登记种子做检查，而不是只查 101。

## 设计

### 1. 新文件：主机变体附加表

`openarm365_dev_v2/scene_contracts/host-variants-20261109.json`：

```json
{
  "schema": "attentionbench.public-scene-contract-host-variants.v1",
  "base_contract": {"file": "dev-seeds-101-103-v2-20261019-kp1500.json", "sha256": "bb28cd3eb1334bf5baf7dd428741ed8640adfd62ecb31e6cf0379d2d1670c716"},
  "variants": [
    {
      "id": "zen5-glibc239-20261109",
      "host": {"cpu_model": "AMD Ryzen 9 9950X 16-Core Processor", "avx512": true, "glibc": "2.39",
               "python": "3.11.16", "mujoco": "3.3.1", "numpy": "2.2.5", "observed_on": "qifei"},
      "evidence": {"fresh_processes": 5, "resets_per_process": 4, "date": "2026-11-09",
                   "report": "docs/REPORT-seed102-qifei-20261109.md"},
      "tasks": {"pick_place_counter_to_sink": {"seeds": {"102": {"normalized_sha256": "149932e26b0a6c8d4651418eac60b8fdf1c4a5c348d9b22a37056c0c40af9676"}}}}
    }
  ]
}
```

- 只列与基础合约**不同**的 (task, seed)。qifei 上 101/103 与基础合约一致，不列。
- `base_contract.sha256` 把变体表绑定在这一份基础合约上。

### 2. `openarm365_dev_v2/scene_contract.py`

- 新常量 `REGISTERED_HOST_VARIANTS`（路径）和 `REGISTERED_HOST_VARIANTS_SHA256`（代码里锁定 digest）。
  文件 digest 不符 → `ValueError`（和基础合约一样 fail closed）。
- 新函数 `accepted_fingerprints(ref, task, seed) -> list[tuple[str, str]]`：
  - 先放 `('reference', seed_entry(...)['normalized_sha256'])`；
  - 只有当 `ref == registered_contract_ref()` 且变体表的 `base_contract.sha256 == ref['sha256']` 时，
    才追加该 (task, seed) 的变体 `(variant_id, sha)`；其他合约（旧的 20261016、catalog 合约、R5 fixture 合约）不带变体。
  - 变体表的 `base_contract.sha256` 和基础合约对不上 → `ValueError`，不能静默忽略。
- `validate()` 的 v2 分支：`matched = actual in {sha for _, sha in accepted}`；返回值保持现有字段，并新增
  - `matched_variant`：`'reference'`、变体 id 或 `None`；
  - `accepted`：`[{"variant": id, "normalized_sha256": sha}, ...]`；
  - 不匹配时 `differences[0]['reference']` 仍是基础指纹（兼容现有读取方），另加 `accepted` 便于诊断。
- `registered_contract_ref()`、`load_contract()`、`seed_entry()` 的签名和行为不变。

### 3. 主机信息写进回执

`worker.py` 写 `scene-condition.json` 时，在 `validation` 里多一个 `host`：`cpu_model`（`/proc/cpuinfo`）、
`glibc`（`platform.libc_ver()`）、`python`、`mujoco`、`numpy`。只记录，不参与判定。
跨主机的 Memory A/B 仍按 `host_lib_policy_b` 记为 needs_review，本方案不改 Memory 的判定。

### 4. 新主机检查：`scripts/attentionbench/scene_fingerprint_check.py`

- 检查 `pick_place_counter_to_sink` 基础合约里登记的**全部种子**（101、102、103），每个种子一个新进程
  （保持“一次调用一个新进程”的语义：脚本自身用 subprocess 逐个起子进程，或加 `--seed` 参数由外层循环）。
- 每个种子打印 `seed`、`normalized_sha256`、`matched_variant`；全部匹配才打印 `matches_registered: True`
  （`verify.py:33-34` 用这个关键字，保持不变）。
- 不匹配时打印实际指纹和主机信息，并提示：用 `register_host_variant.py` 采集证据，经用户同意后登记。
- 保留 `frame_rendered` 检查（只需在一个种子上渲染）。

### 5. 采集工具：`scripts/attentionbench/register_host_variant.py`（新）

- 参数：`--task`、`--seeds`、`--processes N`（默认 5）、`--resets K`（默认 4）、`--id`。
- 对每个种子起 N 个新进程，每个进程 reset K 次，要求 N×K 个指纹全部相同，否则报错退出（不确定的场景不能登记）。
- 只**打印**一个可粘贴进变体表的 JSON 片段（与基础合约相同的种子不输出），**不写**登记文件；登记必须人工改文件并更新
  `REGISTERED_HOST_VARIANTS_SHA256`。
- 不启动 planner、不跑 policy、不拿 sim.lock。

### 6. 文档

`docs/attentionbench_collaborator_setup.md` 的 “Scene fingerprint” 一段：说明现在检查全部登记种子，以及不匹配时的登记流程。

## 测试（`openarm365_dev_v2/tests/test_scene_contract.py`）

1. 基础指纹 → `verified`，`matched_variant == 'reference'`。
2. qifei 变体指纹（102）→ `verified`，`matched_variant == 'zen5-glibc239-20261109'`。
3. 变体指纹用在别的种子（101/103）上 → `scene_contract_mismatch`。
4. 未知指纹 → mismatch，`accepted` 列出两个。
5. 篡改变体文件（digest 不符）→ `ValueError`。
6. 变体表的 `base_contract.sha256` 与基础合约不符 → `ValueError`。
7. 非登记合约的 ref（用 tmp 生成的 v2 合约）→ 只接受它自己的指纹，不带变体。
8. `registered_contract_ref()` 返回值与改动前完全相同（锁住兼容性）。
9. 现有测试全部保持通过：`test_scene_contract.py`、`test_scene_gate.py`、`test_scene_reproducibility.py`、`test_contract.py`，
   以及 Memory 导入和 daily/bridge 中引用合约的测试。

## 验证（Claude 做）

- truares：上述测试；离线探针 101/102/103 → 全部 `verified`，`matched_variant == 'reference'`。
- qifei：把 worktree 中改动的文件拷进 qifei 的 scratch 文件夹（不改 qifei 工作区），用 qifei 的 python
  离线跑 101/102/103 → 101/103 `reference`，102 `zen5-glibc239-20261109`。
- 新的 `scene_fingerprint_check.py` 在两台机器上都打印 `matches_registered: True`。
- 磁盘：新增一个 JSON（<2 KB），没有运行产物。

## 不在本方案内

- 合并到 main、同步到 qifei、重启 8769/8860/8870：时机由中控台决定。
- Remote 重新提供 seed 102（回退 main `b7466c6`）：合并和 qifei 同步之后，由 Remote 线处理。
- 让复位后场景真正静止（报告里的方案 3）。
