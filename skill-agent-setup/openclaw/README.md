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
- Copy the Tidybot workspace customizations to `~/.openclaw/workspace/`
- Configure the skills directory
- Clear existing sessions for a fresh start
- Restart the OpenClaw gateway

Once complete, open a chat with your agent via `openclaw dashboard`.

## Manual Setup

If you prefer to set things up step by step:

### 1. Install OpenClaw

```bash
curl -fsSL https://openclaw.ai/install.sh | bash
```

### 2. Run onboarding

```bash
openclaw onboard --install-daemon
```

### 3. Copy workspace customizations

Copy the Tidybot-specific files into your workspace (the default templates from onboarding are kept):

```bash
cp -r workspace/* ~/.openclaw/workspace/
```

### 4. Configure the skills directory

```bash
openclaw config set skills.load.extraDirs '["~/.openclaw/workspace/skills"]'
```

If the command fails, add the following to your OpenClaw config manually:

```json
{
  "skills": {
    "load": {
      "extraDirs": ["~/.openclaw/workspace/skills"]
    }
  }
}
```

### 5. Clear existing sessions

So the agent picks up the new workspace files fresh:

```bash
rm -rf ~/.openclaw/agents/main/sessions/
rm -f ~/.openclaw/memory/main.sqlite
```

### 6. Restart the gateway

```bash
openclaw gateway restart
```

### 7. Open a chat

```bash
openclaw dashboard
```

## What's Included

These files customize or extend the default OpenClaw workspace with Tidybot-specific content. Files not listed here (BOOT.md, BOOTSTRAP.md, TOOLS.md, IDENTITY.md, USER.md) are standard OpenClaw templates created during onboarding.

```
workspace/
├── AGENTS.md       # Agent behavior guidelines (adds Tidybot skill workflow)
├── SOUL.md         # Agent personality (adds orchestration protocol)
├── ROBOT.md        # Robot hardware reference (new)
├── HEARTBEAT.md    # Tidybot skills maintenance tasks (new)
└── skills/
    └── tidybot-skill-dev/
        └── SKILL.md    # Skill + service development workflow (new)
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
