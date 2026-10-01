#!/usr/bin/env bash
# Shared launcher. Never source this file to run an experiment directly.

personal_verify_group_owned() {
    [[ "$1" =~ ^[1-9][0-9]*$ ]] || return 1
    python3 - "$VERIFY_OUT" "$1" <<'PY'
import os, sys
from pathlib import Path
marker = ("ATTENTION_PERSONAL_VERIFY_OUT=" + sys.argv[1]).encode()
for proc in Path('/proc').glob('[0-9]*'):
    try:
        if os.getpgid(int(proc.name)) == int(sys.argv[2]) and marker in (proc / 'environ').read_bytes().split(b'\0'):
            sys.exit(0)
    except (OSError, ValueError):
        pass
sys.exit(1)
PY
}

personal_verify_cleanup() {
    local original_rc="$1" group remaining file failed=0
    trap - EXIT INT TERM
    # Only groups registered by this invocation; do not pkill -f shared services.
    if [[ -f "$VERIFY_OUT/driver_group" ]]; then
        group=$(cat "$VERIFY_OUT/driver_group")
        if personal_verify_group_owned "$group"; then
            pkill -TERM -g "$group" 2>/dev/null || true
            kill -TERM "$group" 2>/dev/null || true
        fi
    fi
    # Recover a Service created just before an interrupt, before Popen's PID
    # registry write. Match the unique inherited output marker, never names alone.
    python3 - "$VERIFY_OUT" <<'PY' >> "$VERIFY_OUT/service_groups"
import os, sys
from pathlib import Path
marker = ("ATTENTION_PERSONAL_VERIFY_OUT=" + sys.argv[1]).encode()
for proc in Path('/proc').glob('[0-9]*'):
    try:
        env = (proc / 'environ').read_bytes().split(b'\0')
        cmd = (proc / 'cmdline').read_bytes().split(b'\0')
        if marker in env and b'robosuite_sim' in cmd and b'-m' in cmd:
            print(os.getpgid(int(proc.name)))
    except (OSError, ValueError):
        pass
PY
    if [[ -f "$VERIFY_OUT/service_groups" ]]; then
        while read -r group; do
            personal_verify_group_owned "$group" && pkill -TERM -g "$group" 2>/dev/null || true
        done < "$VERIFY_OUT/service_groups"
    fi
    sleep 2
    for file in driver_group service_groups; do
        [[ -f "$VERIFY_OUT/$file" ]] || continue
        while read -r group; do
            personal_verify_group_owned "$group" && pkill -KILL -g "$group" 2>/dev/null || true
        done < "$VERIFY_OUT/$file"
    done
    [[ -z "${VERIFY_DRIVER_PID:-}" ]] || wait "$VERIFY_DRIVER_PID" 2>/dev/null || true
    sleep 1
    for file in driver_group service_groups; do
        [[ -f "$VERIFY_OUT/$file" ]] || continue
        while read -r group; do
            if [[ "$group" =~ ^[1-9][0-9]*$ ]] && pgrep -g "$group" >/dev/null; then
                echo "清理失败：进程组 $group 仍在。"
                ps -o pid,ppid,pgid,stat,args -g "$group" || true
                failed=1
            fi
        done < "$VERIFY_OUT/$file"
    done
    remaining=$(pgrep -af '[p]ython[^ ]* -m robosuite_sim([ ]|$)' || true)
    if [[ -n "$remaining" ]]; then
        echo "仍有 Robosuite Service（若属于其他工作，不自动杀）：$remaining"
        failed=1
    fi
    printf '{"owned_process_groups_gone":%s,"robosuite_processes_absent":%s,"original_exit_code":%s}\n' \
        "$([[ "$failed" == 0 ]] && echo true || echo false)" \
        "$([[ -z "$remaining" ]] && echo true || echo false)" "$original_rc" \
        > "$VERIFY_OUT/shell_cleanup.json"
    cat "$VERIFY_OUT/shell_cleanup.json"
    echo "产物保留：$VERIFY_OUT"
    [[ "$failed" == 0 ]] || return 3
    return "$original_rc"
}

personal_verify_main() {
    local script_dir repo contract harness contract_pythonpath service_root rc=0 existing
    script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
    repo=$(cd -- "$script_dir/../../.." && pwd)
    contract="$repo/benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_guidance_v1_1_dev_2026-10-01/execution_contract.json"
    case "$VERIFY_CASE" in
        1) echo '如何判定：reset cube_lift seed 101 → step 一个动作 → eef 数值变化 + 原生结果 + 无 Service 残留。';;
        2) echo '如何判定：autonomous 无 guidance vs reactive_help 测试回复，打印三项收据参数、SDK 动作及原生结果。'
           echo '预期：grasp_offset_m / approach_tolerance_m 改变；open_settle_steps=10 保持默认。测试回复 ≠ 真实 GLM；动作改变 ≠ 任务成功。';;
        3) echo '如何判定：独立 Safety unsafe=1、delta_exceeds_limit；SDK 首个动作失败、无第二动作；Service 回收。';;
    esac
    if [[ "${1:-}" == --stop ]]; then
        [[ "$#" == 2 && -d "$2" && -f "$2/verification_owner" ]] || { echo '用法：本脚本 --stop <本脚本的产物目录>'; return 2; }
        VERIFY_OUT=$(cd -- "$2" && pwd)
        [[ "$(cat "$VERIFY_OUT/verification_owner")" == "personal-verify-$VERIFY_CASE" ]] || { echo '拒绝：目录不属于此验证。'; return 2; }
        personal_verify_cleanup 130
        return $?
    fi
    [[ "$#" == 0 ]] || { echo '用法：本脚本（运行）；本脚本 --stop <产物目录>（停止）'; return 2; }
    for command in python3 setsid pkill pgrep; do
        command -v "$command" >/dev/null || { echo "缺少命令：$command"; return 2; }
    done
    existing=$(pgrep -af '[p]ython[^ ]* -m robosuite_sim([ ]|$)' || true)
    [[ -z "$existing" ]] || { echo "已有 Robosuite Service，请结束后再运行：$existing"; return 2; }
    # The contract records the resolved binary. Reuse an observed venv alias of
    # that exact SHA so simulator dependencies remain visible to child Services.
    harness=$(python3 - "$contract" "$repo" <<'PY'
import hashlib, json, os, sys
from pathlib import Path
contract = json.loads(Path(sys.argv[1]).read_text())
expected = contract['harness_python']
evidence = Path(sys.argv[2]) / 'benchmarks/attention_harness/protocol/v2/evidence/real_glm_advisor_closure_2026-09-27.json'
candidates = []
if evidence.is_file():
    for run in json.loads(evidence.read_text())['runs']:
        if run['suite'] == 'robosuite':
            candidates += [Path(arg) for arg in run['command_argv'] if 'python' in Path(arg).name]
candidates.append(Path(expected['path']))
for path in candidates:
    if path.is_file() and os.access(path, os.X_OK) and path.resolve() == Path(expected['path']).resolve() and hashlib.sha256(path.read_bytes()).hexdigest() == expected['sha256']:
        print(path)
        break
else:
    raise SystemExit('没有与执行契约 SHA/解析路径一致的 Python 入口')
PY
)
    contract_pythonpath=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["environment"]["PYTHONPATH"])' "$contract")
    service_root=$(python3 -c 'import json,pathlib,sys; p=pathlib.Path(sys.argv[1]); d=json.loads(p.read_text()); print(json.load(open(d["service_versions"]["path"]))["robosuite_service"]["path"])' "$contract")
    [[ -x "$harness" ]] || { echo "执行契约的 Python 不存在：$harness"; return 2; }
    VERIFY_OUT=$(mktemp -d "${TMPDIR:-/tmp}/attention-personal-verify-$VERIFY_CASE.XXXXXX")
    echo "personal-verify-$VERIFY_CASE" > "$VERIFY_OUT/verification_owner"
    echo "产物目录：$VERIFY_OUT"
    echo "如何停止：Ctrl-C；另一个终端：$script_dir/verify_${VERIFY_CASE}_$(case "$VERIFY_CASE" in 1) echo system_runs;; 2) echo guidance_adopted;; 3) echo unsafe_stopped;; esac).sh --stop $VERIFY_OUT"
    trap 'rc=$?; personal_verify_cleanup "$rc"; exit $?' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    unset PARCC_API_KEY LITELLM_KEY OPENAI_API_KEY
    export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
    export PYTHONPATH="$service_root:$contract_pythonpath"
    export ATTENTION_PERSONAL_VERIFY_OUT="$VERIFY_OUT"
    cd -- "$repo"
    setsid "$harness" -u "$script_dir/personal_verify.py" --case "$VERIFY_CASE" --out "$VERIFY_OUT" &
    VERIFY_DRIVER_PID=$!
    echo "$VERIFY_DRIVER_PID" > "$VERIFY_OUT/driver_group"
    wait "$VERIFY_DRIVER_PID" || rc=$?
    exit "$rc"
}
