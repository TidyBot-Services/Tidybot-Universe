# 0006 — Shared AI-Memory Tree Separate From CLAUDE.md

**Status:** Accepted (bootstrapped 2026-05-12)
**Date:** 2026-05-12

## Context

CLAUDE.md kept accreting session-by-session details — work logs, transient notes, debug commentary — and ballooned to 215 lines. New sessions had to wade through stale content to find the actual project rules. Personal Claude Code auto-memory (`~/.claude/projects/<slug>/memory/`) was filling up too, with a mix of:

- Genuinely-personal observations (user style preferences, model quirks)
- Patterns that would benefit teammates (debugging gotchas, decisions, module reference)
- Status snapshots (current focus, milestone log)

The latter two categories shouldn't be locked in one user's local cache — they're project knowledge.

Two separate problems compounded:
1. **CLAUDE.md was too long** to be useful as quick-reference rules.
2. **Shared project knowledge had no home** — it either lived in one person's auto-memory (invisible to others) or got jammed into CLAUDE.md (cluttering the rules).

## Decision

**Three-tier memory architecture, each with a clear responsibility:**

| Tier | Where | Audience | Content |
|---|---|---|---|
| 1 | `CLAUDE.md` (top-level) | All collaborators + Claude | Stable rules, startup routine, common commands. Stays <200 lines. |
| 2 | `docs/ai-memory/` | All collaborators + Claude | Project history, decisions, module reference, lessons. Git-tracked. |
| 3 | `~/.claude/projects/<slug>/memory/` | One user's local Claude only | Personal observations, model quirks, debug scratch, hot takes. Private. |

Tier 2 (`docs/ai-memory/`) sub-structure:

```
docs/ai-memory/
├── README.md             # how the tree is organized
├── active-context.md     # current focus, recently completed, next steps
├── progress.md           # append-only milestone log
├── project-brief.md      # what + why + architecture (stable)
├── decisions/            # ADR-style records (NNNN-<title>.md)
├── modules/              # per-module reference (agent-server, sim, etc.)
└── patterns/             # cross-module lessons / anti-patterns
```

**Startup routine** (encoded in CLAUDE.md so new sessions discover it):

1. Read CLAUDE.md (this file)
2. Read `docs/ai-memory/active-context.md` (current focus)
3. Read `docs/ai-memory/modules/<name>.md` only when touching that area
4. Read `docs/ai-memory/decisions/` only when asking "why is X this way"

**End-of-session prompt** (user invokes; I distribute):

```
请收尾并更新项目记忆:
1. 更新 docs/ai-memory/active-context.md(当前焦点 + 下一步)
2. 完成的 milestone 加进 docs/ai-memory/progress.md
3. 新增重要决策 → docs/ai-memory/decisions/NNNN-<title>.md
4. 个人观察 → 私有 auto-memory(~/.claude/...)
5. 不把流水账塞 CLAUDE.md
```

## Consequences

- **CLAUDE.md stays useful as rules** — new sessions can read it fast.
- **Team gets shared context** — clone the repo, get all the project knowledge.
- **Personal observations stay personal** — model hot takes and user-specific preferences don't leak into team-visible files.
- **Maintenance overhead** — three places to keep consistent. Mitigated by:
  - active-context.md is the *only* file expected to be updated every session
  - decisions are immutable once shipped (supersede with new ADRs)
  - modules update only when architecture shifts
- **Discovery problem solved** — CLAUDE.md's startup routine tells new sessions exactly where to look. Previously, project knowledge was hidden in chat history.

## Alternatives Considered

- **Keep everything in CLAUDE.md**: tried, didn't scale. The file grew to 215 lines and was still missing context anyone joining the project would need.
- **Use only personal auto-memory**: works for solo, breaks for teams. Knowledge is locked in one person's machine.
- **Use a wiki / Notion / external tool**: rejected — adds another auth dance, breaks the "one repo, all context" model, doesn't version with code.
- **Cline-style "memory bank"** (single `memory-bank/` dir with ad-hoc files): close to what we did, but lacks the explicit decision/module/pattern separation. Cline's pattern is the inspiration; we just made the categories explicit.

## Implementation notes

- Top-level `CLAUDE.md` was a symlink to `common/CLAUDE.md`. The actual content lives in the `common/` repo; the symlink keeps it visible at project root.
- `.gitignore` previously ignored top-level CLAUDE.md (per-machine config pattern). Removed for this decision — CLAUDE.md is now project-level instruction, same as subdirectory CLAUDE.md files (`skill-agent-setup/claude-code/CLAUDE.md` etc.).
- Other per-machine files (`AGENTS.md`, `SOUL.md`, `IDENTITY.md`, `USER.md`, `HEARTBEAT.md`, `TOOLS.md`) remain ignored — they ARE meant to be local-only.

## Related

- `README.md` in this directory — how the tree is organized
- CLAUDE.md — Memory Policy section
- Commits: `cf0be50` (docs/ai-memory/ bootstrap) + `bd0a784` (gitignore + symlink) + `26566f2` in common repo (CLAUDE.md content)
