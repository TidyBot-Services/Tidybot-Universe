# AttentionBench operations UI

`/ui/` is the single operator page. It reads one persisted `AttentionStore` and
refreshes run, attempts, requests, budgets, controls, and comparison data every
three seconds. The demo page remains a visual sample only.

From the repository root, in the environment installed by
`benchmarks/attention_harness/setup_env.sh`:

```bash
python -m benchmarks.attention_harness.ui_server \
  --store /path/to/attention.sqlite3 \
  --artifact-root /path/to/attention-artifacts \
  --orchestrator-url http://127.0.0.1:8766 \
  --robosuite-url http://127.0.0.1:8082
```

Start the legacy Orchestrator separately with its graph, for example
`python skill-agent-setup/claude-code/agent_orchestrator.py --graph /path/to/graph`.
The live page's Skill DAG panel now has **启动自动派发**. It calls the connected
Orchestrator's `/attention/auto-start` through the local UI server, only for
the active graph and with the page control token. The Orchestrator itself
selects ready nodes, starts Dev agents, evaluates their output, retries within
its configured limit, and unlocks downstream nodes. The page refreshes their
statuses and sessions; it does not implement a second DAG scheduler. On an
Orchestrator restart, start dispatch again from the page (or launch it with
`--autonomous` and use **派发就绪节点**). A historical graph view cannot dispatch.

The isolated two-node service smoke can be run without model calls:

```bash
python skill-agent-setup/claude-code/tests/attention_dag_process_smoke.py
python skill-agent-setup/claude-code/tests/attention_dag_process_smoke.py --scenario eval-fail
python skill-agent-setup/claude-code/tests/attention_dag_process_smoke.py --restart-ui-inflight
```

It runs the real Orchestrator and UI HTTP handlers with deterministic Dev/Eval
stand-ins, checks the dependency cascade, blocked downstream behavior, UI
restart (including while Eval is running), and graph reload, then writes evidence in
`artifacts/attentionbench-dag-smoke/`. The optional real-model canary uses
`--parcc` under the local PARCC key wrapper. It first probes the gateway and
stops early if the network is unavailable; that canary also uses a local fake
Agent Server and does not move a robot or count as benchmark performance.

```bash
env -u ANTHROPIC_API_KEY HARNESS=openclaw ~/bin/with-litellm.sh \
  python skill-agent-setup/claude-code/tests/attention_dag_process_smoke.py --parcc
```

For RoboCasa camera bridge, use `--robocasa-camera-ws ws://127.0.0.1:5580`
and optionally `--robocasa-camera-device maniskill_wrist`. Run the
seven-policy CLI with `--replay-camera-ws` for RoboCasa or `--record-replay`
for Robosuite to capture an `advisor_replay.mp4` for each attempt. Recording
uses the public RGB stream and requires `ffmpeg`. Without a recording, the
viewer shows camera images from authorized observation artifacts when present.

Open `http://127.0.0.1:8769/ui/`. The HTTP server is bound to loopback. Its
live page carries a per-server control token. A pending Live–Human-first
request accepts one hint, approve/deny, or interrupt response through
`POST /api/requests/{request_id}/respond`; Benchmark–Proxy requests remain
proxy-owned. The runner stores timeout, fallback, the response used, and its
linked next execution. Responses and traces survive a page refresh or server
restart because they are in SQLite.

For an active simulator run, `POST /api/runs/{run_id}/interrupt` persists a
stop request. The UI shows `requested` until the runner confirms `stopped`;
RoboCasa cancellable action jobs use the Agent Server cancel token. This is a
simulator control path, not a physical emergency-stop circuit. A physical
robot requires its own attested stop endpoint and independent hardware safety
system before enabling this control.

The replay endpoint serves only media referenced by the persisted
Advisor-visible TracePacket, from `--artifact-root`, after a SHA-256 check.
It never serves raw trace artifacts or arbitrary filesystem paths. Camera
endpoints send RGB frames only. The old Orchestrator Skill DAG and session
history are operator-only and never enter the AdvisorProxy packet. DAG control
and dispatch are now reachable from this single UI, while the legacy skill
execution pipeline remains separate from v2 benchmark attempts and results.

`Success ↔ assistance` aggregates terminal runs under matching task, target,
mode, models, and budgets, and flags whether the policy seed sets match. This
is a development readout until the full registered experiment is run. A trial
appears as resource blocked only when the scheduler persists a `work.blocked`
event after checking remaining simulator time.
