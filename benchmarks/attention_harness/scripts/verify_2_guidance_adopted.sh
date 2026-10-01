#!/usr/bin/env bash
# 如何判定：同 seed 的 autonomous 与 reactive_help 收据、实际 SDK 参数不同；分别打印原生结果。
# 测试回复 ≠ 真实 GLM；动作改变 ≠ 任务成功。open_settle_steps 在冻结 Robosuite hint 中不可调。
# 如何停止：Ctrl-C；或用本脚本 --stop <启动时打印的产物目录>。退出自动 pkill 自己拥有的进程组。
set -Eeuo pipefail
VERIFY_CASE=2
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/personal_verify_common.sh"
personal_verify_main "$@"
