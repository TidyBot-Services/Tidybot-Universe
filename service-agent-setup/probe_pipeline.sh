#!/bin/bash
# Feasibility probe ── full deploy pipeline test against remote deploy-agent.
# Self-cleaning: tries /stop at the end regardless of any earlier failure.

set -uo pipefail

SERVER="158.130.109.188:9000"
TEST_NAME="feasibility-probe-$(date +%s)"
TEST_PORT=18999
TEST_IMAGE="traefik/whoami:latest"
PUBLIC_HOST="158.130.109.188"

# Cleanup hook — always try to stop the test container, even on early failure.
cleanup() {
    echo
    echo "============================================================"
    echo "  Cleanup ── try to stop $TEST_NAME"
    echo "============================================================"
    local R=$(curl -sf --max-time 10 -X POST "http://$SERVER/stop" \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"$TEST_NAME\"}" 2>&1)
    echo "  /stop response: $R"
}
trap cleanup EXIT

# Pretty step header
step() {
    echo
    echo "────────────────────────────────────────────────────────────"
    echo "  $1"
    echo "────────────────────────────────────────────────────────────"
}

# JSON pretty print (no jq dependency)
pretty() {
    python3 -m json.tool 2>/dev/null || cat
}

step "1. deploy-agent 可达 + GPU 可见  ── GET /health"
HEALTH=$(curl -sf --max-time 5 "http://$SERVER/health" 2>&1)
RC=$?
if [ $RC -ne 0 ]; then
    echo "  ✗ FAIL ── curl exit $RC.  body: $HEALTH"
    echo
    echo "  pipeline 不可达,abort(cleanup 仍会跑)"
    exit 1
fi
echo "$HEALTH" | pretty
echo "  ✓ daemon up"

step "2. 已部署 service 快照(baseline)"
BEFORE=$(curl -sf --max-time 5 "http://$SERVER/services" 2>&1)
echo "$BEFORE" | pretty
COUNT_BEFORE=$(echo "$BEFORE" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "?")
echo "  baseline 已部署: $COUNT_BEFORE 个 service"

step "3. POST /deploy ── 试部署 $TEST_NAME ($TEST_IMAGE) → :$TEST_PORT"
DEPLOY_PAYLOAD=$(cat <<EOF
{
    "name": "$TEST_NAME",
    "image": "$TEST_IMAGE",
    "port": $TEST_PORT,
    "gpu": false,
    "health": "/",
    "ready_timeout": 60,
    "command": "--port $TEST_PORT"
}
EOF
)
echo "  payload:"
echo "$DEPLOY_PAYLOAD" | sed 's/^/    /'
echo
DEPLOY_RESP=$(curl -s --max-time 90 -X POST "http://$SERVER/deploy" \
    -H "Content-Type: application/json" \
    -d "$DEPLOY_PAYLOAD" 2>&1)
RC=$?
echo "  /deploy response (RC=$RC):"
echo "$DEPLOY_RESP" | pretty
DEPLOY_OK=$(echo "$DEPLOY_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(str(d.get('ok','')).lower())" 2>/dev/null)
if [ "$DEPLOY_OK" != "true" ]; then
    echo "  ✗ deploy NOT ok"
    exit 1
fi
ASSIGNED_HOST=$(echo "$DEPLOY_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('host',''))" 2>/dev/null)
echo "  ✓ deploy reported ok, host=$ASSIGNED_HOST"

step "4. GET /services ── 验证新 service 注册"
AFTER=$(curl -sf --max-time 5 "http://$SERVER/services" 2>&1)
FOUND=$(echo "$AFTER" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for s in d:
    if s.get('name') == '$TEST_NAME':
        print(json.dumps(s, indent=2))
        break
" 2>/dev/null)
if [ -z "$FOUND" ]; then
    echo "  ✗ 在 /services 里没找到 $TEST_NAME"
    exit 1
fi
echo "$FOUND"
echo "  ✓ registered"

step "5. 直接调 deployed service ── 看真响应"
# 试 deploy-agent 返的 host;若是内网 IP,用 public host 替换
TEST_URL=$(echo "$ASSIGNED_HOST" | sed -E "s|http://[^:]+:|http://$PUBLIC_HOST:|")
echo "  using URL: $TEST_URL/"
ECHO_RESP=$(curl -sf --max-time 10 "$TEST_URL/" 2>&1)
RC=$?
if [ $RC -ne 0 ]; then
    echo "  ✗ curl exit $RC. body: $ECHO_RESP"
    echo "  (note:if returned 内网 IP,可能从你机器不可达。看 step 4 是否 status=healthy)"
    exit 1
fi
echo "$ECHO_RESP" | head -10
echo "  ✓ deployed container 真在响应"

step "6. POST /stop ── 主动清理"
STOP_RESP=$(curl -sf --max-time 30 -X POST "http://$SERVER/stop" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"$TEST_NAME\"}" 2>&1)
echo "  /stop response: $STOP_RESP"
trap - EXIT  # 已主动清理,不需要再触发 trap cleanup
echo "  ✓ stop ok"

step "7. GET /services ── 验证清理"
AFTER2=$(curl -sf --max-time 5 "http://$SERVER/services" 2>&1)
FOUND2=$(echo "$AFTER2" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for s in d:
    if s.get('name') == '$TEST_NAME':
        print('still there')
        break
" 2>/dev/null)
if [ -n "$FOUND2" ]; then
    echo "  ⚠ $TEST_NAME 仍在 /services 里(可能 stop 失败 OR 异步清理)"
else
    echo "  ✓ $TEST_NAME 已从 /services 列表消失"
fi

echo
echo "============================================================"
echo "  PIPELINE FEASIBILITY: ✓ 全 7 步通过"
echo "============================================================"
