# Tidybot OpenClaw Setup

## Quick Start

```bash
./setup.sh
```

This will:
1. Install OpenClaw (if needed)
2. Run onboarding
3. Copy workspace templates to `~/.openclaw/workspace/`
4. Configure skills directory
5. Clear old sessions and restart

## Manual Setup

1. Install OpenClaw:
```bash
curl -fsSL https://openclaw.ai/install.sh | bash
```

2. Run onboarding:
```bash
openclaw onboard --install-daemon
```

3. Copy workspace files:
```bash
cp -r workspace/* ~/.openclaw/workspace/
```

4. Add skills path to config (`~/.openclaw/openclaw.json`):
```json
{
  "skills": {
    "load": {
      "extraDirs": ["~/.openclaw/workspace/skills"]
    }
  }
}
```

5. Clear existing session and restart:
```bash
rm -rf ~/.openclaw/agents/main/sessions/
rm -f ~/.openclaw/memory/main.sqlite
openclaw gateway restart
```

## What's Included

```
workspace/
├── AGENTS.md       # Agent behavior guidelines
├── SOUL.md         # Agent personality
├── USER.md         # User info template
├── ROBOT.md        # Tidybot hardware reference
├── IDENTITY.md     # Agent identity
├── TOOLS.md        # Local tool notes
├── HEARTBEAT.md    # Periodic task config
├── BOOTSTRAP.md    # First-run setup
├── BOOT.md         # Boot sequence
└── skills/
    └── tidybot-skill-dev/
        └── SKILL.md   # Skill dev + backend collaboration (clones both wishlists)
```
