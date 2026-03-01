---
summary: "Robot hardware, sim, and API reference"
read_when:
  - Bootstrapping a workspace manually
---

# ROBOT.md - About the Robot

- **Name:** Tidybot
- **Hardware:** Franka Panda 7-DOF arm + mobile base + Robotiq gripper + cameras
- **API:** `http://<ROBOT_IP>:8080`
- **Robot IP:**
  *(fill in once known, e.g. 172.16.0.10)*
- **API Key:**
  *(fill in the API key provided by the robot operator)*

## Docs

**Before writing ANY code that touches the robot, read the guide first.** Do not guess APIs or invent endpoints.

- **Getting started:** `GET http://<ROBOT_IP>:8080/docs/guide/html`
- **SDK reference:** `GET http://<ROBOT_IP>:8080/code/sdk/`

> `web_fetch` blocks private IPs. Use `curl -L` via shell.

## Simulator

A MuJoCo simulator can stand in for the real robot. The sim server exposes the same protocol bridges as real hardware, so the agent_server connects to either transparently. **The API is identical** — code written for sim works on hardware and vice versa.

### When to use sim

- Developing and testing skills without the physical robot
- The hardware robot is unreachable
- Experimenting with new approaches before running on hardware

### Running the sim

The sim lives in the `sim/` directory of the tidybot_uni repo. Two terminals:

```bash
# Terminal 1 — sim server (MuJoCo physics + bridges)
cd sim
python3 -m sim_server --no-gui     # headless, or omit --no-gui for viewer

# Terminal 2 — agent server (same API as hardware)
cd agent_server
python3 server.py --no-service-manager
```

Once both are running, the API is at `http://localhost:8080` — same endpoints, same SDK, same lease system.

### Sim vs hardware

| | Hardware | Sim |
|---|---|---|
| API endpoint | `http://<ROBOT_IP>:8080` | `http://localhost:8080` |
| Startup | `start_robot.sh` | `python3 -m sim_server` + `server.py` |
| Gripper feedback | Real force/object detection | Approximate (position-based) |
| Cameras | RealSense RGB-D | MuJoCo rendered (color only) |
| Physics | Real world | MuJoCo (may differ near contacts) |

### Sim CLI options

```
--task NAME          Scene/env (default: BananaTestKitchen)
--robot NAME         Robot model (default: TidyVerse)
--layout N           Kitchen layout ID (default: 1)
--style N            Kitchen style ID (default: 1)
--no-gui             Headless (no MuJoCo viewer)
--no-base-bridge     Disable individual bridges
--no-franka-bridge
--no-gripper-bridge
--no-camera-bridge
```

## Details

For detailed hardware specs, coordinate frames, camera IDs, and morphology, see the `tidybot-robot-hardware` skill.
For full endpoint reference, see the `tidybot-robot-connection` skill.
For SDK method signatures, see the `tidybot-robot-sdk-ref` skill.

## Notes

*(Record robot-specific learnings here: joint limits, reliable grasp forces, workspace boundaries, quirks, calibration notes, etc.)*
