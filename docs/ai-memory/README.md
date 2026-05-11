# Tidybot Universe — AI Memory

This directory holds the project's shared knowledge — what we're building, why, and what we've learned. It's separate from `CLAUDE.md` (stable rules + commands) and from each individual's private Claude Code memory (`~/.claude/projects/.../memory/`).

## What goes where

| Where | Content | Audience |
|---|---|---|
| `CLAUDE.md` (top level) | Stable rules, startup routine, common commands | All collaborators + Claude |
| `docs/ai-memory/` (this dir) | Project history, decisions, module knowledge, lessons | All collaborators + Claude |
| `~/.claude/projects/<slug>/memory/` | Personal observations, hot takes, debug scratch | Just you |

Rule of thumb: **if a teammate cloning this repo would benefit, it goes in `docs/ai-memory/`. If it's "Claude noticed user prefers X", it stays in personal memory.**

## Files

- **`active-context.md`** — Current focus, recently completed, next steps. Updated at end of every substantial session. **Read first** at session start.
- **`progress.md`** — Append-only milestone log. Major capabilities shipped, known issues, deferred work.
- **`project-brief.md`** — What this project is, why it exists, high-level architecture. Mostly stable.
- **`decisions/`** — Architecture decision records (ADRs). One file per significant design choice. Numbered chronologically.
- **`modules/`** — Long-lived per-module reference. How agent_server / orchestrator / sim / etc work, gotchas, file pointers.
- **`patterns/`** — Cross-module lessons / anti-patterns. Reusable insights that don't belong to one module.

## Session workflow

**Start of session** (Claude does automatically per CLAUDE.md startup routine):
1. Read `active-context.md`
2. Read relevant `modules/<name>.md` if touching that area
3. Read relevant `decisions/NNNN-*.md` if asked "why is X this way"

**During session** — work normally.

**End of session** (use this prompt):

```
请收尾并更新项目记忆:
1. 更新 active-context.md(当前焦点 + 下一步)
2. 完成的 milestone 加进 progress.md
3. 新增重要决策 → decisions/NNNN-<title>.md
4. 个人观察(模型 quirk、调试细节)→ 留我私有 memory
5. 不把流水账塞 CLAUDE.md
```

## Hygiene

- **Active-context** stays small. Older context migrates to `progress.md` or relevant module docs.
- **Decisions are immutable** once shipped. If a decision is superseded, add a new ADR that references the old one — don't edit history.
- **Modules** are reference docs, not change logs. Update when architecture shifts, not for every commit.
- **Stale check** — periodically (monthly or per major version) sweep for outdated claims. Verify against code before relying on memory.
