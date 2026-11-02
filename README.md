<p align="center">
  <a href="https://tidybot-services.github.io/">
    <img src="banner.png" alt="Tidybot Universe" width="100%" />
  </a>
</p>

# Tidybot Universe

## AttentionBench research entry

AttentionBench studies when a robot should ask a human for help, and how help can
become reusable experience, trading task success against human attention.

**The research code lives on the `attentionbench/baseline-20261015` branch, not on
the default branch.**

- Browse: <https://github.com/TidyBot-Services/Tidybot-Universe/tree/attentionbench/baseline-20261015>
- Download ZIP: <https://github.com/TidyBot-Services/Tidybot-Universe/archive/refs/heads/attentionbench/baseline-20261015.zip>
- Clone: `git clone -b attentionbench/baseline-20261015 https://github.com/TidyBot-Services/Tidybot-Universe.git`

Current scope (2026-10-31): RoboCasa365 kitchen tasks with the OpenArm365 robot
(`pick_place_counter_to_sink` and others), an "agent mode" where a Codex agent writes
and revises the robot policy, a self-evaluator (Eval), a simulated human helper
(AdvisorProxy), and a Memory service that turns help into validated, reusable notes.

**Reproduction status:** reproduced from this repository in a clean Ubuntu 22.04 GPU
container on 2026-11-01 (L2). Reproduction on a second machine (L3) is pending.
All runs so far are development tier, not formal results.

From a machine with the requirements below, choose an absolute `WS` path and run
these three lines (replace `/absolute/WS` with that path):

```bash
mkdir -p /absolute/WS/repos && git clone -b attentionbench/baseline-20261015 https://github.com/TidyBot-Services/Tidybot-Universe.git /absolute/WS/repos/attentionbench
/absolute/WS/repos/attentionbench/scripts/attentionbench/setup.sh --workspace /absolute/WS
/absolute/WS/repos/attentionbench/scripts/attentionbench/run.sh --workspace /absolute/WS
```

`run.sh` runs one agent attempt for up to 45 minutes (`--minutes N` to change), then cancels it and stops everything.
Use `scripts/attentionbench/stop.sh --workspace /absolute/WS` to cancel running agent
workers and stop the UI and Memory services.

| Component | Version or source | Installed by | Size and time |
|---|---|---|---|
| OS | Ubuntu 22.04, Linux x86_64 | You | Existing host |
| NVIDIA GPU and driver | 580.x on RTX 5070 Ti verified; other combinations untested | You | Hardware and driver installation vary |
| Git | Ubuntu 22.04 package | You | Small; minutes |
| Python with development headers | 3.10+; `python3-dev` | You | Hundreds of MB; minutes |
| C compiler | `build-essential` | You | Hundreds of MB; minutes |
| uv | Current compatible installer | You | Small; minutes |
| Conda | Miniconda, with locked cuRobo environment | You; setup builds environment | About 15 GB for cuRobo; tens of minutes |
| bwrap and user namespaces | `bubblewrap`; working user, PID and network namespace probe | You | Small; minutes |
| Codex CLI and account | `codex-cli 0.165.0`; access to `gpt-6.1-sol`. setup.sh and run.sh make one short Codex call when no recent quota reading exists (uses your quota). | You | Small; login and warm-up minutes |
| Disk | About 70 GB free for the first install (about 51 GB workspace plus about 15 GB of uv/pip/conda download caches; reclaim the caches afterwards with `uv cache clean; pip cache purge; conda clean -a`). `setup.sh` checks this first. | You | Asset transfer, hashing and installation took 15 minutes in a clean GPU container with local asset copies |
| Official RoboCasa assets and simulator | Asset lists pinned by SHA256 in `attentionbench.lock.json` (`assets.*`) | setup | About 25 GB with the simulator environment; transfer and hashing can take tens of minutes or longer |
| cuRobo native assets | 70,254 files checked against the planner lock | setup | About 8.2 GB |
| Official cuRobo source | Commit `78fd485fa82d9b9a063fb4985e371814587e666a` | setup | Checkout and build time vary |
| `robocasa_tasks` source | Commit `b18bbf1585c42e370ae45ababdddad700cc2c71d` | setup | Checkout time varies |

| Machine-specific remnant | Handling |
|---|---|
| Author absolute paths | Remain only in frozen evidence, logs, legacy tools and formal-tier pinned planner paths. [Exemptions](config/attentionbench/machine-path-exemptions.json) list reasons; `test_no_machine_paths` guards them. Collaborator runs do not read those paths. |
| Credentials and addresses | No keys, tailnet addresses or e-mail addresses are tracked (scanned over the branch history). The author lab's LLM proxy host appears in provider code and docs; collaborators use their own Codex account instead. |
| Ports | UI and Memory bind loopback at 8769 and 8878 by default; `--ui-port` and `--memory-port` change them. |
| Memory keys | Generated separately for each workspace by setup. |

See the [collaborator setup guide](docs/attentionbench_collaborator_setup.md) for details
and Docker instructions.

Start with the [project guide](docs/attentionbench_project_guide.md) for the research
question, system modules, Service repositories and current evidence. See
[module progress](docs/attentionbench_progress.md) and [STATUS.md](STATUS.md) for the
full history.

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
