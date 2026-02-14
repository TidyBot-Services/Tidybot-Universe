---
name: tidybot-skill-dev
description: Build, create, or develop skills for Tidybot robot. Use when asked to make a new robot skill, add to the wishlist, or contribute to tidybot-skills. Also use when you need a backend API, model, or service that doesn't exist yet.
---

# Tidybot Skill Development

## Before You Start

1. **Read the SDK guide first:** `GET http://<ROBOT_IP>:8080/docs/guide/html` — this documents the actual API, available methods, and correct usage patterns. Do not guess or improvise. Use what the guide provides.
2. **Check existing skills:** Fetch the catalog at `https://raw.githubusercontent.com/tidybot-skills/wishlist/main/catalog.json`. If a skill already does what you need (or something close), use it or extend it. Do not rebuild from scratch what already works.
3. **Check the SDK reference** for detailed method signatures: `GET http://<ROBOT_IP>:8080/code/sdk/markdown`

## Developing a Skill

1. **Start with the robot.** Connect to the API using the patterns from the SDK guide, test sensor reads, understand what you're working with.
2. **Prototype iteratively.** Write code using SDK methods (not invented ones), test on hardware, use rewind when things go wrong. Log what works.
3. **Structure your skill** once it's working:
   - `README.md` — what it does, how to use it
   - `main.py` — entry point
   - `deps.txt` — dependencies (including other skills if you build on them)

## Testing

- Run the skill multiple times in different conditions
- Before publishing, accumulate at least 10 successful unsupervised runs
- Use rewind freely — it's your safety net for experimentation

## Publishing

When you're confident the skill works reliably:

1. **Fetch the full workflow rules:**
   ```
   https://raw.githubusercontent.com/tidybot-skills/wishlist/main/RULES.md
   ```
   This defines repo structure, catalog updates, wishlist status changes, and multi-agent coordination.

2. **Create a repo** in the [tidybot-skills](https://github.com/tidybot-skills) org
3. **Update `catalog.json`** in tidybot-skills/wishlist with the new skill
4. **Update `wishlist.json`** if this skill fulfills a wishlist item

## Using Services

Services are online APIs (models, vision, grasping, etc.) with downloadable Python client SDKs. Fetch the catalog to see what's available:
```
https://raw.githubusercontent.com/TidyBot-Services/services_wishlist/main/catalog.json
```
Each entry has `host` (HTTP endpoint), `client_sdk` (Python client file URL), and `api_docs`. Download the client SDK and use it in your skill code.

## Requesting New Services

If your skill needs a model, API, or driver that doesn't exist:

1. Clone the services wishlist:
   ```bash
   git clone https://github.com/TidyBot-Services/services_wishlist.git ./services_wishlist
   ```
2. Read `services_wishlist/RULES.md` for the request workflow
3. Add your request to `services_wishlist/wishlist.json`

## Reference

- [Skills catalog](https://github.com/tidybot-skills/wishlist) — `catalog.json` lists all available skills
- [Services catalog](https://github.com/TidyBot-Services/services_wishlist) — `catalog.json` lists available services
- [Skills wishlist](https://github.com/tidybot-skills/wishlist) — `wishlist.json` for requested skills
- [Services wishlist](https://github.com/TidyBot-Services/services_wishlist) — `wishlist.json` for requested services
