---
name: tidybot-sim-manager
description: Start, stop, restart, and health-check the MuJoCo sim server and agent server. Use when (1) launching the sim for skill development or testing, (2) restarting after a crash, (3) checking if the sim is running, (4) shutting down cleanly.
---

# Sim Manager

## Environment

- **Conda env:** `/home/yifei/miniconda3/envs/tidybot-sim/bin/python`
- **Sim dir:** `~/tidybot_uni/sim`
- **Agent server dir:** `~/tidybot_uni/agent_server`
- **DISPLAY:** `:1` (for GUI mode)

## Ports

| Port | Service | Protocol |
|------|---------|----------|
| 50000 | Base bridge | RPC |
| 5555 | Franka CMD | ZMQ REP |
| 5556 | Franka State | ZMQ PUB |
| 5557 | Franka Stream | ZMQ SUB |
| 5570 | Gripper CMD | ZMQ REP |
| 5571 | Gripper State | ZMQ PUB |
| 5580 | Camera bridge | WebSocket |
| 8080 | Agent server API | HTTP |
| 8081 | Sim HTTP API (reset/health) | HTTP |

## Starting

**Order matters: sim server first, then agent server.**

### Step 1: Kill stale processes

```bash
fuser -k 5555/tcp 5556/tcp 5557/tcp 5570/tcp 5571/tcp 5580/tcp 8080/tcp 8081/tcp 50000/tcp 2>/dev/null
sleep 2
```

### Step 2: Start sim server

```bash
cd ~/tidybot_uni/robocasa_sim && DISPLAY=:1 /home/yifei/miniconda3/envs/tidybot-sim/bin/python -u -m sim_server 2>&1
```

- Use `pty: true` for unbuffered output
- Use `--no-gui` for headless mode (no MuJoCo viewer)
- **Takes ~90 seconds** to compile the kitchen scene — wait for `[sim] Entering physics loop`
- Custom scene: `--task BananaTestKitchen --layout 1 --style 1`

### Step 3: Start agent server

```bash
cd ~/tidybot_uni/agent_server && /home/yifei/miniconda3/envs/tidybot-sim/bin/python -u server.py --no-service-manager 2>&1
```

- Wait for `Uvicorn running on http://0.0.0.0:8080`
- Ignore mocap warnings (no mocap in sim)
- Ignore arm monitor warnings (they settle after a few seconds)

## Health Checks

```bash
curl localhost:8081/health    # Sim server
curl localhost:8080/health    # Agent server
```

## Stopping

Kill both processes. Then clear ports to avoid conflicts on next start:

```bash
fuser -k 8080/tcp 8081/tcp 5555/tcp 5556/tcp 5557/tcp 5570/tcp 5571/tcp 5580/tcp 50000/tcp 2>/dev/null
```

## Sim Reset API

- `POST localhost:8081/reset` — soft reset (instant, restores initial state)
- `POST localhost:8081/reset/hard` — hard reset (~2s, full scene reload)
- Reset also happens **automatically on lease release** via the agent server

## Git Push (for sim and agent_server repos)

```bash
GIT_SSH_COMMAND="ssh -i ~/.ssh/thinkpad_docker_noetic_github -p 443 -o StrictHostKeyChecking=no" git push
```

## Common Issues

| Problem | Fix |
|---------|-----|
| `Address already in use` on startup | Kill stale ports (Step 1) |
| No output for 90+ seconds | Normal — kitchen scene compilation is slow |
| No MuJoCo viewer window | Set `DISPLAY=:1`, don't use `--no-gui` |
| Agent server can't connect to backends | Sim server isn't running yet — start it first |
| Lease expires before you can release | Idle timeout is 15s — extend lease or be quick |
| `Permission denied (publickey)` on push | Use the SSH command above |
