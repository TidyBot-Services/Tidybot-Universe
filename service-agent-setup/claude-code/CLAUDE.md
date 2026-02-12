# Tidybot Service Development

You are developing backend services for the Tidybot Universe platform.

## Context

- **Services org:** https://github.com/TidyBot-Services — one repo per service
- **Services wishlist:** https://github.com/TidyBot-Services/backend_wishlist — open requests for new services
- **Skills org:** https://github.com/tidybot-skills — the skills that depend on your services
- **Agent server:** The unified API layer between AI agents and robot hardware (default: `http://localhost:8080`)

## Service Types

Services fall into these categories:

- **Hardware drivers** — arm servers, gripper servers, sensor interfaces
- **AI/ML models** — object detection, pose estimation, segmentation
- **Utility libraries** — coordinate transforms, trajectory planning, common helpers
- **Platform services** — agent server extensions, monitoring, logging

## Workflow

1. Read the wishlist `RULES.md` for contribution guidelines
2. Each service gets its own repo in the TidyBot-Services org
3. Services must include a README with setup instructions and API documentation
4. Test locally against the agent server before pushing
5. Update the wishlist item when the service is ready

## Safety

Services run below the agent server safety layer — they talk directly to hardware. Be careful with:

- Motor commands and force limits
- GPIO and electrical interfaces
- System resources (ports, file handles, processes)
- Network exposure (bind to localhost unless explicitly needed)

Always test with the robot in a safe configuration. Use the agent server's safety envelope when available.
