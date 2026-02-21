---
name: robot-connection
description: Robot connection credentials and API endpoints — IP address, API key, base URL, and all key REST endpoints. Use when (1) making API calls to the robot, (2) executing code on the robot via /code/execute, (3) debugging connection issues or timeouts, (4) needing the robot IP or API key mid-session, (5) polling execution status or retrieving recorded frames, or (6) fetching SDK docs or the getting-started guide.
---

# Robot Connection

- **Robot IP:** *(fill in from ROBOT.md)*
- **API Key:** *(fill in from ROBOT.md)*
- **Base URL:** `http://<ROBOT_IP>:8080`

## Key Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/code/execute` | POST | Run Python code on the robot |
| `/code/status?stdout_offset=N&stderr_offset=N` | GET | Poll execution output |
| `/code/recordings/{execution_id}` | GET | Execution recording metadata |
| `/code/recordings/{execution_id}/frames/{filename}` | GET | Retrieve recorded camera frame (JPEG) |
| `/code/sdk/` | GET | SDK reference (browsable) |
| `/code/sdk/markdown` | GET | SDK reference as markdown |
| `/docs/guide/html` | GET | Getting-started guide |
| `/cameras/{id}/frame` | GET | Live camera frame (use sparingly) |

## Notes

- `web_fetch` blocks private IPs. Use `curl -L` via shell for robot endpoints.
- Prefer `print()` + polling `/code/status` over live camera polling.
- Recorded frames save automatically at 0.5 Hz during execution — use those instead of live polls.
