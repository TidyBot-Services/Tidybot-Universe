# Module — agent_server

The FastAPI hardware server. All skill code goes through here.

## What it does

- Exposes unified HTTP/WebSocket API on **port 8080** (`0.0.0.0`)
- Implements the lease system (single-writer access to robot/sim)
- Runs submitted code in isolated subprocesses (`code_executor.py`)
- Records executions to `logs/code_executions/<exec_id>/` (frames + state + stdout)
- Provides the `robot_sdk` Python module imported by submitted code
- Hosts the dashboard at `/services/dashboard`
- Exposes service docs at `/code/sdk/markdown` and `/docs/guide/html`

## Key code paths

| Path | Role |
|---|---|
| `server.py` | FastAPI app, lifecycle hooks |
| `code_executor.py` | Subprocess management, stdout/stderr capture, exec recording |
| `execution_recorder.py` | Camera frames + state_log writer |
| `lease.py` | Single-writer lock with non-blocking ticket queue |
| `backends/` | franka, base, gripper, mocap, cameras — abstract over sim vs real |
| `robot_sdk/` | The API surface presented to user code |
| `routes/code_routes.py` | `/code/submit`, `/code/jobs/{id}`, `/code/sdk/markdown` |
| `routes/lease_routes.py` | `/lease/acquire`, `/release`, `/extend`, `/status` |
| `routes/rewind_routes.py` | Rewind / replay endpoints |

## Recording dir contents (per exec)

```
logs/code_executions/<exec_id>/
├── metadata.json         # timing, frame_count, cameras list (NOT stdout)
├── state_log.jsonl       # robot state samples at 10Hz
├── stdout.log            # captured subprocess stdout (added 2026-05-09)
├── stderr.log            # captured subprocess stderr (added 2026-05-09)
├── 0000_base_camera.jpg  # frames at 5Hz
├── 0000_wrist_camera.jpg
└── ...
```

The `stdout.log` / `stderr.log` files were added 2026-05-09 — see `decisions/0005-stdout-via-files.md`. Old exec dirs from before that date won't have them.

## Lease semantics

- Single active holder at any time. New requests queue via `/lease/queue/{ticket_id}`.
- `/code/submit` doesn't require a lease — the server acquires/releases internally per job.
- `/code/execute` (low-level) requires explicit lease management via `X-Lease-Id` header.
- Lease auto-expires on idle (configurable) or max-duration.

## Code execution flow

1. Agent calls `POST /code/submit` with `{code, holder, [timeout]}`
2. Server queues the job, returns `job_id`
3. When dequeued: server wraps user code with SDK init boilerplate, writes to temp file, spawns subprocess with `python3 -u`
4. Subprocess writes to camera/state via `robot_sdk` calls hitting the bridges
5. ExecutionRecorder captures cameras + state in background
6. On subprocess exit: stdout/stderr captured, written to `stdout.log`/`stderr.log` + returned via `/code/jobs/{id}` API
7. Optional auto-reset between jobs

## Safety

Each backend has rate / limit checks. Violations trigger:
1. Immediate stop of running code
2. Logged + reported in `/state` and `/lease/status`
3. Auto-recovery for some classes (arm collision → recover via FrankaDesk)

Real-hardware safety:
- Workspace boundary checked on each command
- Force limits enforced by Franka controller
- E-stop wired to hardware level (can't be bypassed in software)

## Common quirks

- **ArmMonitor imports `pylibfranka` in sim** → SIGTERMs dev executions when arm enters error state (sim doesn't have pylibfranka). See `~/.claude/projects/.../memory/project_sim_infra_bugs.md`.
- **Robotiq finger lock mismatch**: cuRobo URDF locks `panda_finger_joint1/2` at 0.04 rad, but Robotiq has different joint semantics. See same memory.
- **Stdout file vs API**: stdout returned via API (`result.stdout`) AND now also written to `stdout.log`. They should match. If they diverge (rare), trust the API.

## Restart procedure

```bash
# Find PID
PID=$(ss -tnlp 2>/dev/null | grep ':8080 ' | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
kill $PID
# Wait for port release
# Start fresh
cd ~/文档/Tidybot-Universe/agent_server
conda run -n maniskill --no-capture-output \
  env LD_PRELOAD=$HOME/miniconda3/envs/maniskill/lib/libstdc++.so.6 \
  PYTHONUNBUFFERED=1 \
  python3 server.py --no-service-manager > /tmp/agent_server.log 2>&1 &
```
