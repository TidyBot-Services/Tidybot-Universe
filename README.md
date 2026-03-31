<p align="center">
  <a href="https://tidybot-services.github.io/">
    <img src="banner.png" alt="Tidybot Universe" width="100%" />
  </a>
</p>

# Tidybot Universe

## The Bet

AI agents have already changed software engineering. They write code, debug it, ship it. The same revolution is coming for robotics — but robotics has constraints that software doesn't. We're building the framework that bridges that gap.

## Robotics Is Composable

Software became powerful when we stopped building monoliths and started composing small, reusable pieces. Robotics should work the same way. A "pick up object" skill is a building block. A "place on surface" skill is another. Chain them together and you get "clean the table." Chain that with navigation and you get "tidy the apartment."

An agent with access to the internet, trial-and-error, and a library of shared skills can do far more than any hand-coded robot program. Skills built by one robot benefit every robot in the community. That's the Tidybot Universe.

## Install (Sim + Claude Code)

Requires [conda](https://docs.conda.io/en/latest/miniconda.html) and [Claude Code](https://claude.ai/code). No hardware needed — runs entirely in simulation.

```bash
curl -fsSL https://raw.githubusercontent.com/TidyBot-Services/Tidybot-Universe/master/setup.sh | bash -s -- YOUR_ENV_NAME
```

Follow the instructions printed at the end — start Claude Code in the skill agent directory and type `/xbot-plan`.

## What Makes Robotics Agents Different

Software agents operate in a sandbox. Robotics agents operate in the real world. Three things change:

### 1. Safety

A software bug crashes a process. A robotics bug crashes into a wall. Every action must be reversible, bounded, and supervised. You can't just retry — you have to undo. And when something goes wrong, the robot must fail safe, not fail dangerous.

This also shapes how the agent interacts with the robot. The agent doesn't control the robot in a tight loop — that would be too slow. Instead, it submits a complete skill (Python code) that runs locally on the robot with the servers. But the agent isn't blind: it monitors execution in real time and holds an e-stop. If something goes wrong, it can kill the execution immediately. Write the plan, send it off, watch with your finger on the button.

### 2. Speed

Software is instant. The physical world is slow. Arms take seconds to move. Cameras need time to capture. Networks have latency. And the robot is a single physical body — you can't spin up ten copies to run in parallel. An agent framework for robotics must be designed around the reality that every action takes wall-clock time, the hardware is scarce, and multiple agents need to share it efficiently.

### 3. Resources

A software agent has the entire workstation. A robot has a small onboard computer strapped to a moving platform — not enough to run vision models, grasp planners, or language models. The intelligence has to live elsewhere, but the actions happen locally.

## How We Solve It

### The Agent

The agent is the brain. It reads sensors, reasons about the world, and decides what to do. Crucially, **the agent doesn't have to live on the robot.** The robot exposes a network API — any machine on the network can control it. Five people can each bring a laptop, each running a different agent, and develop skills on the same robot without conflict. The lease system handles turn-taking; the agent just needs an HTTP connection.

Our framework gives it:

- **Composable skills.** Skills are small, tested, shareable Python scripts. An agent chains existing skills before writing new code. The community grows the skill library over time.
- **A synchronous SDK over HTTP.** The agent submits Python code to the robot, which executes it locally. No ROS, no shared memory, no tight coupling. The agent can run anywhere — a laptop, a cloud server, a Raspberry Pi.

### The Robot

The robot is the body. Our framework wraps it in safety and structure:

- **Trajectory recording and rewind.** Every motion is recorded. Rewind provides undo for the physical world — replay the trajectory backwards to escape a bad state.
- **Workspace boundaries.** A convex hull defines where the robot is allowed to operate. Cross the boundary and the safety monitor intervenes automatically.
- **Auto-hold.** On any failure — code crash, timeout, exception — the robot holds its current position. It never goes limp.
- **Lease system.** One agent controls the robot at a time. Others queue. When a lease ends, the robot rewinds and returns to a known home state. This makes it safe for multiple agents to share scarce hardware without stepping on each other.
- **Simulator with identical API.** The sim exposes the exact same interface as the real robot. Agents can iterate in sim at full speed — no waiting for hardware, no risk of damage — then transfer to the real thing without changing a line of code. This is how you make development fast when the hardware is slow.

### The Services

The services are the muscles and senses that don't fit on the robot's little computer:

- **GPU services on demand.** Vision models, grasp planners, segmentation — they run on powerful remote servers and expose simple HTTP APIs.
- **Agent-driven deployment.** The agent discovers what services exist, deploys what it needs, and calls them. The robot's onboard computer just orchestrates.

## Known Issues

- **Kill button doesn't stop evaluator mid-run.** The evaluator runs as an inline coroutine inside the dev agent's task (not a separate `AgentState`), so killing the dev agent during evaluation cancels the task but the SDK client may still flush buffered messages to the dashboard logs. The evaluator needs its own cancellation token or the broadcast should check agent liveness before writing.

## More

See **[Getting Started](GETTING_STARTED.md)** for the full ecosystem overview, hardware setup, and service development.
