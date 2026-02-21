---
summary: "Robot hardware and API reference"
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

## Details

For detailed hardware specs, coordinate frames, camera IDs, and morphology, see the `robot-hardware` skill.
For full endpoint reference, see the `robot-connection` skill.
For SDK method signatures, see the `robot-sdk-ref` skill.

## Notes

*(Record robot-specific learnings here: joint limits, reliable grasp forces, workspace boundaries, quirks, calibration notes, etc.)*
