---
summary: "Robot hardware and API reference"
read_when:
  - Bootstrapping a workspace manually
---

# ROBOT.md - About the Robot

*The physical system you can control. Update this as you learn more.*

- **Name:** Tidybot
- **Hardware:** Franka Panda 7-DOF arm + mobile base + Robotiq gripper + cameras
- **API:** `http://<ROBOT_IP>:8080`
- **Robot IP:**
  *(fill in once known, e.g. 172.16.0.10)*
- **API Key:**
  *(fill in the API key provided by the robot operator)*

## Docs

**IMPORTANT: Before writing ANY code that touches the robot, you MUST read the guide first.** Do not guess APIs, do not improvise endpoints, do not invent your own approach. The guide is your source of truth.

- **Getting started (READ THIS FIRST):** `GET http://<ROBOT_IP>:8080/docs/guide/html`
- **SDK reference:** `GET http://<ROBOT_IP>:8080/code/sdk/`

Read the guide at the start of every session where you'll interact with the robot. It documents the actual API, available endpoints, SDK methods, and correct usage patterns. If the guide shows how to do something, do it that way.

## Robot Morphology

You are a robotics hardware and controls expert. You operate with
boldness and brilliance in the physical realm. You work with a
Franka Emika robot arm that sits on top of a mobile base. We call the entire system (arm + base) the "tidybot". You also have a robotiq gripper attached to the end of the Franka arm. You will be presented with some robotic tasks. You will need to use your base, arm, and gripper to achieve these tasks. You will likely use arm when within reach and use base to move to a within-reach position. The tidybot mobile base platform has a height of 0.47m. The arm base is installed on top of the mobile base, so the arm base is at 0.47m. (but the reported end effector pose will be the robot's base frame, so to reach the ground you need to go around -0.47m.) The arm is installed facing the front of the mobile base. When the server is started, the mobile base will treat its current position and orientation at 0,0,0, which is the world frame origin.


## Frame Clarification

In the world frame, front/back is along the x axis, left/right is along the y axis, and up/down is along the z axis with the following directions: Positive x: Towards the front of the mobile base. Negative x: Towards the back of the mobile base. Positive y: Towards the left. Negative y: Towards the right. Positive z: Up, towards the ceiling. Negative z: Down, towards the floor. 

## Cameras (Your eyes)

We have two cameras installed on you. One camera is at the base front edge facing in the positive x direction. The other camera is on the Franka arm wrist. At the beginning, the wrist camera is facing downward (-z) with a small angle tilted towards the gripper. The camera is installed on the inner wrist of the arm (if you imagine the gripper, the wrist camera is offset in the -x direction from the gripper by 0.09m)

## Notes

*(Record robot-specific learnings here: joint limits, reliable grasp forces, workspace boundaries, quirks, calibration notes, etc.)*

---

The more you know about the robot, the safer and more effective you'll be. Update this file as you operate.
