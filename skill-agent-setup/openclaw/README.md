# OpenClaw Setup

[OpenClaw](https://openclaw.ai) is an AI agent platform that runs on your machine and connects to your robot through the agent server. Your agent can develop skills, test them on hardware, and share them with the community.

## Quick Start

```bash
# 1. Install OpenClaw
curl -fsSL https://openclaw.ai/install.sh | bash

# 2. Run this setup script
./setup.sh
```

The setup script will:
- Run OpenClaw onboarding (if not already done)
- Copy Tidybot-specific files (MISSION.md, ROBOT.md, HEARTBEAT.md, skills/) to `~/.openclaw/workspace/`
- Patch the default AGENTS.md with Tidybot session checklist items
- Configure the skills directory
- Clear existing sessions for a fresh start
- Restart the OpenClaw gateway

Once complete, open a chat with your agent via `openclaw dashboard`.

## What's Included

These files are Tidybot-specific additions to the OpenClaw workspace. The default OpenClaw files (AGENTS.md, BOOT.md, BOOTSTRAP.md, TOOLS.md, IDENTITY.md, USER.md) are created during onboarding — `setup.sh` patches AGENTS.md with Tidybot additions rather than replacing it.

```
workspace/
├── MISSION.md      # Tidybot Universe mission and organic skill flow
├── ROBOT.md        # Robot hardware reference
├── HEARTBEAT.md    # Tidybot skills maintenance tasks
└── skills/
    └── tidybot-skill-dev/
        └── SKILL.md    # Skill development and publishing workflow
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
