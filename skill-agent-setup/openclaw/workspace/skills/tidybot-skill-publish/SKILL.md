---
name: tidybot-skill-publish
description: Publish a tested robot skill to the tidybot-skills org. Use when the user approves publishing a skill from the dev/ folder.
---

# Tidybot Skill Publishing

Only publish when the user explicitly approves. Ask first: *"Want me to publish this so other Tidybots can use it?"*

## Prerequisites

- Skill has `dev-tb-` prefix with proper OpenClaw structure (`SKILL.md`, `scripts/main.py`, `scripts/deps.txt`)
- Skill has 70%+ cumulative success rate
- Stats were not invalidated by recent code changes (any code change resets stats — re-test first)
- GitHub CLI authenticated (`gh auth login`)

## Publishing Steps

1. **Rename from `dev-tb-` to `tb-`:** Move the skill folder from `dev-tb-my-skill/` to `tb-my-skill/`.

2. **Create a repo** in the [tidybot-skills](https://github.com/tidybot-skills) org. The repo name drops the `tb-` prefix (e.g. local `tb-pick-up-object` → remote `tidybot-skills/pick-up-object`):
   ```bash
   gh repo create tidybot-skills/<skill-name-without-tb-prefix> --public
   ```

3. **Push skill code** to the new repo

4. **Update `catalog.json`** in `tidybot-skills/wishlist`:
   ```json
   {
     "<skill-name>": {
       "repo": "tidybot-skills/<skill-name>",
       "description": "What the skill does",
       "author": "<your-agent-name>",
       "dependencies": ["dep1", "dep2"],
       "success_rate": 0,
       "total_trials": 0,
       "institutions_tested": 0
     }
   }
   ```
   Set `success_rate` and `total_trials` to `0` — public stats start fresh.

5. **Update `wishlist.json`** if this skill fulfills a wishlist item — set status to `"done"` and add the repo path

## Catalog Fields

| Field | Description |
|-------|-------------|
| repo | GitHub repo path (`tidybot-skills/<name>`) |
| description | What the skill does |
| author | Agent or person who built it |
| dependencies | List of skill repo names this depends on |
| success_rate | Percentage of successful trials (0–100) |
| total_trials | Total number of trial runs performed |
| institutions_tested | Number of distinct labs that tested this |

## Wishlist Status Values

| Status | Meaning |
|--------|---------|
| `pending` | Not started |
| `building` | Agent working on it |
| `done` | Available in catalog.json |
