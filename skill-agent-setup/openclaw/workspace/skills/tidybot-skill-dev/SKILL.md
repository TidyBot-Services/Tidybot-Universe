---
name: tidybot-skill-dev
description: Build, create, or develop skills for Tidybot robot. Use when asked to make a new robot skill, add to the wishlist, or contribute to tidybot-skills. Also use when you need a backend API, model, or service that doesn't exist yet.
---

# Tidybot Skill Development

**FIRST clone both repos and read their RULES.md before writing any code.**

## Skills Wishlist (Frontend)

```bash
git clone https://github.com/tidybot-skills/wishlist.git ./wishlist
```

Clone into this skill's directory (`skills/tidybot-skill-dev/wishlist`). Then read `wishlist/RULES.md`.

This repo contains:
- `RULES.md` — Full workflow rules (repo structure, catalog, wishlist, multi-agent coordination)
- `catalog.json` — Catalog of all available skills
- `wishlist.json` — Skill requests and voting
- `CONTRIBUTING.md` — How to add skills

## Services Wishlist (Backend)

```bash
git clone https://github.com/TidyBot-Services/backend_wishlist.git ./backend_wishlist
```

Clone into this skill's directory (`skills/tidybot-skill-dev/backend_wishlist`). Then read `backend_wishlist/RULES.md`.

This repo contains:
- `RULES.md` — Full workflow for requesting and fulfilling backend services
- `catalog.json` — Catalog of available services (hardware, agent, software)
- `wishlist.json` — Requested services from frontend (skill) agents
- `CONTRIBUTING.md` — How to request (frontend) or fulfill (backend) services

Use this when you need an API endpoint, model, or SDK method that doesn't exist yet.

## Workflow

1. Clone both repos above
2. Read both RULES.md files
3. Check catalogs before building (avoid duplicates)
4. Do not start coding until you've done the above
