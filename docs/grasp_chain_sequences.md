# seed 102 抓取链路序列诊断

抓取搜索在底座侧移 0.25 m 时，找到了世界 TCP 四元数 `xyzw=(0.3007057995042732,0.9537169507482269,0,0)`：目标姿态在预抓取和抓取高度都有带碰撞 IK 解。此前 `rotate-first` 真实探针在侧移后的末端位置约 `(0.41,-2.526,1.089)` 原地旋转，第一步通过 8840 次查询的连续证书，第二步返回 `official_ik_non_success`。搜索只验证过目标姿态在物体上方和抓取高度的两个位置，因此新序列用来定位旋转发生的位置是否影响可达性；IK 解与抓取成功仍须分别验证。

`grasp_chain_probe --sequence` 有三个取值：

| 序列 | 张手后的动作 | 到达预抓取后的动作 |
| --- | --- | --- |
| `rotate-first`（默认） | 在当前位置分步旋转至目标四元数 | 平移至目标顶面上方 10 cm |
| `translate-first` | 保持初始姿态，分步平移至目标顶面上方 10 cm | 在该位置分步旋转；平移增量仅抵消观测到的 TCP 漂移 |
| `combined` | 按线性位置插值与四元数最短路球面插值，同时分步平移和旋转至预抓取位姿 | 直接下探 |

每个动作后重新读取公开 TCP 位姿并计算剩余增量。平移步的长度严格小于 0.05 m；`translate-first` 的旋转坐标不超过 0.5 rad；`combined` 的旋转坐标不超过 0.45 rad。`combined` 步数为 `max(1,ceil(distance/0.045),ceil(max_abs_rotvec_coordinate/0.45))`。所有序列接着分步下探、闭爪、抬高 10 cm。默认 `rotate-first` 的生成策略源码保留原字节内容。

显式使用非默认 `--rotation-step RAD` 时，三种序列都用 `0.9×RAD` 计算初始旋转步数，而每坐标指令仍不超过 `RAD`。旋转段每步重新观测残差，主循环至多在初始步数上增加 3 步；残差小于 0.01 rad 时提前结束。若主循环后残差仍超过 0.02 rad，最多追加 3 步限幅修正；仍超过 0.02 rad 则打印残差并抛出 `rotation did not converge`。这些额外动作逐次检查 SDK 调用预算。默认 `0.45` 的生成策略源码保持逐字节不变。

以下命令只打印策略及步数公式，不执行 reset 或物理动作：

```bash
for sequence in rotate-first translate-first combined; do
  PYTHONPATH="$PWD:/home/truares/桌面/AttentionBench-Workspace/development/attention_memory_service" \
    /home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python \
    -m benchmarks.attention_harness.diagnostics.grasp_chain_probe \
    --seed 102 --base-shift 0,0.25,0 --grasp-quat 0.3007057995042732,0.9537169507482269,0,0 \
    --oracle-from-reset --sequence "$sequence" --dry-run
done
```

要打印数字步数，提供张手后的公开 TCP 位姿及预抓取目标位置：

```json
{"eef_position":[0.4104425806153241,-2.5261967543350234,1.0894136503658094],"eef_quat":[0.0778981124810766,0.7601637435567035,-0.3813983056967324,-0.5202098609843968],"pregrasp_position":[0.4969098174320634,-2.37729484580756,1.0516263961490986]}
```

这些数值来自此前 seed 102、侧移 0.25 m、张手后的只读公开 trace 与当次 oracle 目标顶面；它们不代表新一次 reset。将 JSON 写入文件，再在上述命令后加 `--dry-run-state 文件路径`，该记录的接近段步数为 `rotate-first=7`、`translate-first=7`、`combined=4`。其中预抓取 z 按目标**顶面** `0.9516263961490986+0.10` 计算；此前报告的约 `1.0347` m 是以物体中心加 10 cm 的位置，不是本策略和抓取搜索使用的预抓取高度。真实执行仍会按每步观测修正剩余量。

真实执行会写 `results.json`、`results.md` 和 `policy.py`。`results.json` 的 `steps` 为每个左臂动作单列一行，包括步号、平移和旋转增量、公开结果与失败原因、证书查询数/耗时、该步目标 TCP 世界位置与 xyzw 姿态。没有生成连续证书的规划失败会保留空的证书字段。`stages` 继续汇总底座、张手、预抓取、旋转（适用时）、下探、闭爪、抬起和持物判定。`--oracle-from-reset` 只读 reset 并注入目标几何真值，报告标注“使用仿真真值,仅诊断”。真实探针会启动物理执行，本次改动的离线测试不验证其可达或抓取结果。
