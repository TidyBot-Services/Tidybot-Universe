#!/usr/bin/env bash
# 如何判定：一次 /v1/step 后 robot0_eef_pos 数值变化；原生 /v1/success 返回布尔；无残留 Service。
# 如何停止：Ctrl-C；或用本脚本 --stop <启动时打印的产物目录>。退出自动 pkill 自己拥有的进程组。
set -Eeuo pipefail
VERIFY_CASE=1
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/personal_verify_common.sh"
personal_verify_main "$@"
