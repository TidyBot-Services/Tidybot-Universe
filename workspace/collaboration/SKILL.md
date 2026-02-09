---
name: tidybot-backend-collab
description: Request or check on backend APIs, models, or services for the Tidybot robot. Use when you need a capability the backend doesn't yet provide, or to check what's available.
---

# Tidybot Backend Collaboration

**FIRST clone the backend wishlist repo.**

```bash
git clone https://github.com/TidyBotArmy-Backend/backend_wishlist.git ./backend_wishlist
```

Clone into this directory (`collaboration/backend_wishlist`). Then read `backend_wishlist/RULES.md`.

This repo contains:
- `RULES.md` — Full workflow for requesting and fulfilling backend capabilities
- `catalog.json` — Catalog of available backend APIs and services
- `wishlist.json` — Requested APIs/models/services from frontend agents
- `CONTRIBUTING.md` — How to request (frontend) or fulfill (backend) capabilities

## When to Use

- You need an API endpoint that doesn't exist yet
- You need a model installed on the robot server
- You need an SDK method that isn't available
- You want to check what backend capabilities exist

## Workflow

1. Check `backend_wishlist/catalog.json` — maybe it already exists
2. Check `backend_wishlist/wishlist.json` — maybe it's already requested
3. If not, add your request to `wishlist.json`, commit, and push
4. Backend agent will implement it and update the API docs
