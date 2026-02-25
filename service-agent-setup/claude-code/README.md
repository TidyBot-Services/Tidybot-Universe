# Claude Code Setup — Service Development

[Claude Code](https://claude.ai/claude-code) is a CLI-based AI coding agent that works inside your terminal. You review and approve each change before it runs, making it a good fit for service development where code talks directly to hardware.

## Quick Start

```bash
# 1. Install Claude Code
npm install -g @anthropic-ai/claude-code

# 2. Start Claude Code in a service repo
cd <service-repo>
claude
```

## Setup

### 1. Install Claude Code

```bash
npm install -g @anthropic-ai/claude-code
```

### 2. Add a CLAUDE.md project file

Copy the included `CLAUDE.md` to your working directory for Tidybot service development context:

```bash
cp CLAUDE.md ~/your-service-workspace/CLAUDE.md
```

### 3. Start working

```bash
cd <service-repo>
claude
```

## What's Included

```
claude-code/
├── README.md       # You are here
└── CLAUDE.md       # Project instructions for Claude Code (Tidybot service dev context)
```

## Workflow

A typical service development session:

1. **Create a new repo** — one repo per service, in the [TidyBot-Services](https://github.com/TidyBot-Services) org
2. **Develop the service** — Claude Code writes main.py, client.py, service.yaml, Dockerfile; you review each change
3. **Build and test** — build Docker image, test locally
4. **Push to GitHub** — skill agents can now discover and deploy it

## Why Claude Code for Services?

Skills run above the agent server's safety layer (rewind, sandbox, safety envelope), so skill agents can experiment freely. Services run *below* that layer — they talk directly to hardware, manage system resources, and define the APIs that skills depend on. Claude Code's human-in-the-loop model means every change is reviewed before it touches your system.
