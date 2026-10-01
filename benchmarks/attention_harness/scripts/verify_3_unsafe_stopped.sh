#!/usr/bin/env bash
# 如何判定：独立 Safety 记录 delta_exceeds_limit/unsafe；只有首个失败 SDK 动作，无第二动作；Service 回收。
# 受控注入发生在首个动作的监测入口，超限指令被拒绝，不向模拟器实际发送危险位移。
# 如何停止：Ctrl-C；或用本脚本 --stop <启动时打印的产物目录>。退出自动 pkill 自己拥有的进程组。
set -Eeuo pipefail
VERIFY_CASE=3
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/personal_verify_common.sh"
personal_verify_main "$@"
