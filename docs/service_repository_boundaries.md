# Service repository boundary audit

This document records the decision for deployable code previously kept in
Tidybot-Universe. The rule is one authoritative repository per deployable
service, not one repository per client, adapter, SDK method, or agent role.

| Component | Authority | Universe role |
| --- | --- | --- |
| Memory Service v2 | [attention_memory_service](https://github.com/TidyBot-Services/attention_memory_service) | Version-pinned dependency, compatibility imports, Memory Agent, and benchmark runner |
| Robosuite simulator | [robosuite_sim](https://github.com/TidyBot-Services/robosuite_sim) | Version-pinned external runner; frozen v1 source snapshot retained for hash verification |
| Deploy Agent | [deploy-agent](https://github.com/TidyBot-Services/deploy-agent) | Installation instructions and API specifications only |
| Service catalog scanner | Retired | Migration note only; old source recoverable from Git history |

RoboCasa, cuRobo, perception, and hardware services already have independent
repositories. Universe contains their clients, adapters, setup instructions,
and evaluation harnesses rather than a second server implementation.

## Orchestrator assessment

`skill-agent-setup/claude-code/agent_orchestrator.py` is an agent application
that also listens on WebSocket port 8765 and HTTP port 8766. It is not a peer
robot-capability service and is not split in this migration. It assumes a
Universe workspace layout, graph files, dashboard integration, and the
agent-server checkout. If it needs an independent release or deployment
lifecycle, extract it as an **orchestrator application** with its own package,
state directory, client contract, and regression tests; do not label the dev
and evaluator roles as separate services merely because the app spawns them.

`tidybot_sdk` is a candidate for a versioned shared-library repository, but
it is not a service. Its separate packaging should be coordinated with the
existing `agent_server/robot_sdk` migration rather than counted as another
service extraction.
