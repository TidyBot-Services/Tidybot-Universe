# Tidybot OpenClaw Setup

1. Install OpenClaw:
```
curl -fsSL https://openclaw.ai/install.sh | bash
```

2. Run onboarding:
```
openclaw onboard --install-daemon
```

3. Replace the workspace templates with the Tidybot versions:
```
unzip tidybot-templates.zip -o -d ~/.openclaw/workspace/
```

4. Clear any existing session so the agent picks up the new files fresh:
```
rm -rf ~/.openclaw/agents/main/sessions/
rm -f ~/.openclaw/memory/main.sqlite
```

5. Restart the gateway:
```
openclaw gateway restart
```
