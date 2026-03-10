---
name: tidybot-commit-push
description: Audit all Tidybot repos for uncommitted/unpushed changes, diff workspace skills and personality files against the Tidybot-Universe setup repo, and summarize. Use when (1) the user says "push tidybot" or "commit tidybot", (2) reviewing what's changed before pushing, (3) syncing workspace files to the public repo.
---

# Tidybot Commit & Push

Audit all repos, diff workspace against the template repo, and help commit/push changes.

## Repo Layout

All repos live under `~/tidybot_uni/`:

### Core repos (git)
- `agent_server` → TidyBot-Services/agent_server
- `common` → TidyBot-Services/common
- `deploy-agent` → TidyBot-Services/deploy-agent
- `sim` → TidyBot-Services/sim
- `system_logger` → TidyBot-Services/system_logger

### Published skills (`~/tidybot_uni/skills/<name>/`)
Each is its own repo under `Tidybot-Skills/` org.

### Services (`~/tidybot_uni/services/<name>/`)
Each is its own repo under `TidyBot-Services/` org.

### Marketing (`~/tidybot_uni/marketing/`)
- `Tidybot-Universe` → TidyBot-Services/Tidybot-Universe (**has workspace template**)
- `TidyBot-Services-.github` → TidyBot-Services/.github
- `TidyBot-Services.github.io` → TidyBot-Services/TidyBot-Services.github.io
- `tidybot-skills-.github` → tidybot-skills/.github

### Workspace template location
`~/tidybot_uni/marketing/Tidybot-Universe/skill-agent-setup/openclaw/workspace/`

Contains the template versions of skills and personality files that ship with new setups.

## Procedure

### Step 1: Check all repos for uncommitted changes and unpushed commits

For each git repo in:
- `~/tidybot_uni/{agent_server,common,deploy-agent,sim,system_logger}`
- `~/tidybot_uni/skills/*/`
- `~/tidybot_uni/services/*/`
- `~/tidybot_uni/marketing/*/`

Run `git status --short` and check `git rev-list --count origin/<branch>..HEAD`.
Report only repos with changes or unpushed commits.

### Step 2: Diff workspace files against Tidybot-Universe template

Compare live workspace (`~/.openclaw/workspace/`) against the template repo:

**Personality files:** HEARTBEAT.md, MISSION.md, ROBOT.md
- ROBOT.md and connection skills will have real IPs/keys filled in — that's expected. Flag only structural/content changes.

**Skills:** Compare all `tidybot-*` skills in both locations.
- Report: identical, differs (show diff), workspace-only, repo-only.
- `tb-*` robot execution skills are workspace-only (not in template) — that's normal.

### Step 3: Present summary

Format as a clear summary:
- Repos with uncommitted changes (with `git diff --stat`)
- Repos with unpushed commits (with `git log --oneline`)
- Workspace ↔ template diffs (only meaningful content diffs, skip expected IP/key fills)
- Skills in workspace but not in template (candidates to add)

### Step 4: Ask what to do

Offer to:
1. Commit & push specific repos
2. Sync specific workspace files to the template repo
3. Skip / do nothing

## Git Push (SSH key for tidybot repos)

Some repos need the special SSH key:
```bash
GIT_SSH_COMMAND="ssh -i ~/.ssh/thinkpad_docker_noetic_github -p 443 -o StrictHostKeyChecking=no" git push
```

The Tidybot-Universe repo uses HTTPS, so normal `git push` works.
