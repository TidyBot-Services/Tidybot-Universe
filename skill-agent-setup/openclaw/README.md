# OpenClaw Setup

[OpenClaw](https://openclaw.ai) is an AI agent platform that runs on your machine and connects to your robot through the agent server. Your agent can develop skills, test them on hardware, and share them with the community.

## Quick Start

```bash
# Add Tidybot to an existing OpenClaw workspace
./setup.sh

# Or start fresh (wipes workspace, sessions, memory — keeps auth)
./setup.sh --fresh
```

**Integrate mode** (default) adds Tidybot files and patches to your existing workspace without touching your sessions or memory.

**Fresh mode** (`--fresh`) wipes the workspace, sessions, and memory, regenerates default OpenClaw files, then applies Tidybot additions. Auth and config are preserved.

Both modes will:
- Install OpenClaw and run onboarding if needed
- Copy Tidybot files (MISSION.md, ROBOT.md, HEARTBEAT.md, skills/, docs/) to `~/.openclaw/workspace/`
- Patch the default AGENTS.md with Tidybot session checklist items
- Configure the skills directory
- Restart the OpenClaw gateway

Once complete, open a chat with your agent via `openclaw dashboard`.

## What's Included

These files are Tidybot-specific additions to the OpenClaw workspace. The default OpenClaw files (AGENTS.md, BOOT.md, BOOTSTRAP.md, TOOLS.md, IDENTITY.md, USER.md) are created during onboarding — `setup.sh` patches AGENTS.md with Tidybot additions rather than replacing it.

```
workspace/
├── MISSION.md      # Tidybot Universe mission and organic skill flow
├── ROBOT.md        # Robot hardware reference
├── HEARTBEAT.md    # Tidybot skills maintenance tasks
├── skills/
│   ├── tidybot-skill-dev/
│   │   └── SKILL.md        # Skill development and publishing workflow
│   └── tidybot-bundle      # Bundles a skill + dependencies into one script
└── docs/
    └── tidybot-bundle.md   # tidybot-bundle documentation
```

## What Happens Next

Once your agent is running, it will:

1. Introduce itself and get to know you
2. Read the robot documentation from the agent server
3. Ask about your wishlist — what do you want the robot to do?
4. Check the [skills catalog](https://github.com/tidybot-skills) for existing skills
5. Build new skills for your wishlist items
6. Test them on your robot — safely, with rewind as a safety net
7. Share them back so others can use them too
