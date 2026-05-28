#!/usr/bin/env bash
# =============================================================================
# TidyBot counter-to-cab — 一键启动所有服务
#
# 用法:
#   ./scripts/start_all.sh                    # 启动所有服务
#   ./scripts/start_all.sh --status           # 仅检查端口状态
#   ./scripts/start_all.sh --stop             # 停止所有服务
#
# 服务清单 (按启动顺序):
#   1. agent_server        :8080  — 代码执行 SDK
#   2. maniskill (sim)     :5500  — RoboCasa 任务环境
#   3. base_planner        :6100  — A* 路径规划
#   4. curobo              :7000  — cuRobo 运动规划
#   5. perception          :6000  — 感知服务
#   6. vlm_caption         :8095  — VLM 描述 (需 LITELLM_KEY)
#   7. dashboard           :8070  — 监控前端
#   8. orchestrator        :8765  — Agent 调度 (WebSocket)
#                          :8766  — HTTP API
#
# Agent 模型:
#   Dev:       penn-litellm/deepseek-ai/DeepSeek-V4-Flash
#   Evaluator: penn-litellm/Qwen/Qwen3-VL-235B-A22B-Instruct
# =============================================================================
set -euo pipefail

PROJECT_ROOT="/home/truares/Documents/tidybot-uni"
ORCH_DIR="$PROJECT_ROOT/Tidybot-Universe/skill-agent-setup/claude-code"
SIM_DIR="$PROJECT_ROOT/sims/maniskill"
DASHBOARD_DIR="$PROJECT_ROOT/TidyBot-Services.github.io"
CONDA_ENV="tidybot"
PID_DIR="$ORCH_DIR/.pids"

# --- 颜色 ---
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

check_port() { ss -tlnp 2>/dev/null | grep -q ":$1 " || netstat -tlnp 2>/dev/null | grep -q ":$1 "; }

# ---------------------------------------------------------------------------
# --status
# ---------------------------------------------------------------------------
cmd_status() {
    echo "=== 服务端口状态 ==="
    for svc in "8080:agent_server" "5500:sim (maniskill)" "6100:base_planner" \
               "7000:curobo" "6000:perception" "8095:vlm_caption" \
               "8070:dashboard" "8765:orchestrator(WS)" "8766:orchestrator(HTTP)"; do
        port="${svc%%:*}"
        name="${svc#*:}"
        if check_port "$port"; then
            echo -e "  ${GREEN}✓${NC} $port — $name"
        else
            echo -e "  ${RED}✗${NC} $port — $name"
        fi
    done
}

# ---------------------------------------------------------------------------
# --stop
# ---------------------------------------------------------------------------
cmd_stop() {
    echo "=== 停止所有服务 ==="
    if [ -d "$PID_DIR" ]; then
        for f in "$PID_DIR"/*.pid; do
            [ -f "$f" ] || continue
            pid=$(cat "$f")
            svc=$(basename "$f" .pid)
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null && echo "  已停止 $svc (pid=$pid)" || true
            fi
            rm -f "$f"
        done
    fi
    # 兜底：按端口杀
    for port in 8080 5500 6100 7000 6000 8095 8070 8765; do
        pid=$(ss -tlnp 2>/dev/null | grep ":$port " | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -1)
        [ -n "$pid" ] && kill "$pid" 2>/dev/null && echo "  已停止端口 $port (pid=$pid)" || true
    done
    echo "完成"
}

# ---------------------------------------------------------------------------
# 后台启动辅助 — start_bg <name> <port> <cmd...>
# ---------------------------------------------------------------------------
start_bg() {
    local name="$1"; local port="$2"; shift 2
    mkdir -p "$PID_DIR"
    if check_port "$port"; then
        echo -e "  ${YELLOW}[skip]${NC} $name — 端口 $port 已占用"
        return
    fi
    echo -e "  ${GREEN}[start]${NC} $name :$port"
    "$@" &
    local pid=$!
    echo "$pid" > "$PID_DIR/$name.pid"
    sleep 1
}

# ---------------------------------------------------------------------------
# --start (默认)
# ---------------------------------------------------------------------------
cmd_start() {
    echo "=== TidyBot 服务启动 ==="
    echo ""

    # 1. agent_server (8080) — 需要先启动，sim 依赖它
    start_bg "agent_server" 8080 \
        bash -c "source /home/truares/miniconda3/etc/profile.d/conda.sh && conda activate $CONDA_ENV && PYTHONPATH=\"$PROJECT_ROOT/protocols/franka_protocol:$PROJECT_ROOT/protocols/gripper_protocol:$PROJECT_ROOT/protocols/camera_protocol:\$PYTHONPATH\" python $PROJECT_ROOT/agent_server/server.py"

    # 等 agent_server 就绪
    echo -n "  等待 agent_server :8080 ..."
    for i in $(seq 1 15); do
        if check_port 8080; then echo -e " ${GREEN}OK${NC}"; break; fi
        sleep 1
        [ "$i" -eq 15 ] && echo -e " ${RED}TIMEOUT${NC}"
    done

    # 2. sim (5500) — 需要 tidybot conda 环境 (有 torch)
    start_bg "sim" 5500 \
        bash -c "source /home/truares/miniconda3/etc/profile.d/conda.sh && conda activate $CONDA_ENV && cd $SIM_DIR && python -m maniskill_server --task RoboCasa-Pn-P-Counter-To-Cab-v0"

    # 3. base_planner (6100)
    start_bg "base_planner" 6100 \
        bash -c "cd $PROJECT_ROOT/base_planner_service && python -m base_planner_service"

    # 4. curobo (7000) — 需要 tidybot conda 环境 (有 curobo + torch)
    start_bg "curobo" 7000 \
        bash -c "source /home/truares/miniconda3/etc/profile.d/conda.sh && conda activate $CONDA_ENV && cd $PROJECT_ROOT/curobo_service && python -m curobo_service"

    # 5. perception (6000)
    start_bg "perception" 6000 \
        bash -c "cd $PROJECT_ROOT/perception_service && python -m perception_service"

    # 6. vlm_caption (8095) — 可选，需要 LITELLM_KEY
    if [ -n "${LITELLM_KEY:-}" ]; then
        start_bg "vlm_caption" 8095 \
            bash -c "cd $PROJECT_ROOT/vlm_caption_service && python -m vlm_caption_server"
    else
        echo -e "  ${YELLOW}[skip]${NC} vlm_caption — 未设置 LITELLM_KEY (非必需)"
    fi

    # 7. dashboard (8070)
    start_bg "dashboard" 8070 \
        bash -c "cd $DASHBOARD_DIR && python -m http.server 8070"

    # 8. orchestrator (8765/8766)
    start_bg "orchestrator" 8765 \
        bash -c "cd $ORCH_DIR && HARNESS=openclaw python agent_orchestrator.py --graph graphs/counter-to-cab"

    echo ""
    echo "=== 启动完成，5 秒后检查状态 ==="
    sleep 5
    cmd_status
    echo ""
    echo "Dashboard:  http://localhost:8070/local/"

    # 自动触发 agent 任务
    echo "触发 agent 任务..."
    sleep 3
    curl -s -X POST http://localhost:8766/xbot-start | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'  已启动 {len(d.get(\"spawned\",[]))} 个 skill: {d.get(\"spawned\",[])}')" 2>/dev/null || echo "  触发失败（orchestrator 可能还没就绪，手动执行: curl -X POST http://localhost:8766/xbot-start）"
}

# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
case "${1:-start}" in
    --status|status)  cmd_status ;;
    --stop|stop)      cmd_stop ;;
    --start|start|"") cmd_start ;;
    *) echo "用法: $0 [--start|--status|--stop]" >&2; exit 1 ;;
esac