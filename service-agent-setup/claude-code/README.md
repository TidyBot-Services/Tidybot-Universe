# Claude Code Setup

[Claude Code](https://claude.ai/claude-code) is a CLI-based AI coding agent that works inside your terminal. You review and approve each change before it runs, making it a good fit for service development where code talks directly to hardware.

## Quick Start

```bash
# 1. Install Claude Code
npm install -g @anthropic-ai/claude-code

# 2. Clone the services wishlist
git clone https://github.com/TidyBot-Services/backend_wishlist.git

# 3. Start Claude Code in the wishlist repo
cd backend_wishlist
claude
```

## Setup

### 1. Install Claude Code

```bash
npm install -g @anthropic-ai/claude-code
```

### 2. Clone the services wishlist

The wishlist tracks what services are needed. Its `RULES.md` defines the contribution workflow.

```bash
git clone https://github.com/TidyBot-Services/backend_wishlist.git
```

### 3. Add a CLAUDE.md project file

Create a `CLAUDE.md` in your working directory to give Claude Code context about the Tidybot ecosystem:

```bash
cp CLAUDE.md ~/your-service-workspace/CLAUDE.md
```

Or create one manually with the key context (see the included `CLAUDE.md` for reference).

### 4. Start working

```bash
cd backend_wishlist
claude
```

Ask Claude Code to read `RULES.md`, then pick a wishlist item to work on.

## What's Included

```
claude-code/
├── README.md       # You are here
└── CLAUDE.md       # Project instructions for Claude Code (Tidybot service dev context)
```

## Workflow

A typical service development session looks like:

1. **Pick a wishlist item** — check `backend_wishlist` for open requests
2. **Create a new repo** — one repo per service, in the [TidyBot-Services](https://github.com/TidyBot-Services) org
3. **Develop the service** — Claude Code writes code, you review each change
4. **Test locally** — run the service and verify it works with the agent server
5. **Push and update the wishlist** — mark the item as complete

## Why Claude Code for Services?

Skills run above the agent server's safety layer (rewind, sandbox, safety envelope), so skill agents like OpenClaw can experiment freely. Services run *below* that layer — they talk directly to hardware, manage system resources, and define the APIs that skills depend on. Claude Code's human-in-the-loop model means every change is reviewed before it touches your system.
