---
name: tidybot-skill-dev
description: Build, test, and iterate robot skills for Tidybot. Use when (1) asked to make a new robot skill, (2) prototyping or iterating on skill code, (3) running or recording skill trials, (4) tracking success/failure stats, (5) deciding whether to chain existing skills or build new, (6) debugging skill execution, (7) needing a backend API, model, or service that doesn't exist yet.
---

# Tidybot Skill Development

## Before You Start

1. **Read the SDK guide first:** `GET http://<ROBOT_IP>:8080/docs/guide/html` — do not guess or improvise. See `robot-sdk-ref` skill.
2. **Check existing skills:** Fetch `https://raw.githubusercontent.com/tidybot-skills/wishlist/main/catalog.json`. Use or extend what exists.
3. **Chain if possible.** Use `tidybot-bundle <skill-name>` to combine skills. Estimate success = product of individual rates. Chaining tested skills is always preferable to untested new code.
4. **Build new only as last resort.** Research first (internet, papers), then check services (see `active-services` skill).

## Developing

1. Connect to the robot API using SDK guide patterns. Test sensor reads first.
2. Prototype iteratively — use SDK methods (not invented ones), test on hardware, use rewind on failure.
3. Save to `dev/` folder. Structure: `README.md`, `main.py`, `deps.txt`.

## Testing & Trials

1. Run the skill multiple times in varied conditions.
2. Record success/failure per trial. **Any code change resets stats** — re-test from scratch.
3. Debug with `print()` + poll `/code/status?stdout_offset=N&stderr_offset=N`. Prefer recorded frames over live camera.
4. Max 10 trials/day per skill during heartbeat practice.
5. Use rewind freely — it's your safety net.

## Publishing Readiness

A skill is ready when it has 70%+ success rate over 10+ unsupervised trials on the final code version. For the actual publishing steps, see the `tidybot-skill-publish` skill.

## Code Pattern Extraction

When the same logic appears in 2+ skills, extract into a shared skill. Add it to `deps.txt` in dependent skills. Abstraction emerges from use, not upfront design.

## Reference

- Skills catalog: `https://raw.githubusercontent.com/tidybot-skills/wishlist/main/catalog.json`
- Skills wishlist: `https://raw.githubusercontent.com/tidybot-skills/wishlist/main/wishlist.json`
