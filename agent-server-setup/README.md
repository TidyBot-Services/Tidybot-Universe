# Agent Server Setup

The agent server is the unified API layer between AI agents and robot hardware. It provides safety guardrails (rewind, safety envelope, lease system, code execution sandbox) so that skill agents can freely experiment without damaging hardware.

This directory contains scripts to set up the agent server's connection to the services ecosystem — specifically, the **service catalog sync** that automatically downloads service client SDKs as they become available.

## Quick Start

```bash
cd Tidybot-Universe/agent-server-setup
./setup.sh
```

This will:
1. Clone the [services wishlist](https://github.com/TidyBot-Services/services_wishlist) repo (shared catalog)
2. Create the `service_clients/` directory in your agent server
3. Install `sync_catalog.sh` and a cron job to keep service clients up to date
4. Run the first sync immediately

## How It Works

When service agents build new capabilities (YOLO detection, grasp generation, depth estimation, etc.), they publish client SDKs to the [services catalog](https://github.com/TidyBot-Services/services_wishlist). The sync script pulls the latest catalog and downloads new or updated client SDKs into your agent server's `service_clients/` directory, making them immediately available to skill agents.

```
Service agent publishes SDK → catalog.json updated → cron runs sync_catalog.sh → client.py downloaded → skill agent can use it
```

## Options

```bash
./setup.sh                                     # defaults
./setup.sh --agent-server ~/my-agent-server    # custom agent server path
./setup.sh --wishlist-dir ~/my-wishlist        # custom wishlist repo path
./setup.sh --interval 5                        # sync every 5 minutes (default: 2)
```

## What's Included

```
agent-server-setup/
├── README.md           # You are here
├── setup.sh            # One-command setup (clones wishlist, installs cron)
└── sync_catalog.sh     # Service catalog sync script (installed by setup.sh)
```

## Troubleshooting

**Check sync logs:**

```bash
tail -f ~/tidybot_army/sync_catalog.log
```

**Verify cron is running:**

```bash
crontab -l
```

**Run sync manually:**

```bash
~/tidybot_army/tidybot-agent-server/sync_catalog.sh
```

**Git pull fails:** Make sure you have SSH access to the TidyBot-Services GitHub org, or switch the wishlist remote to HTTPS.
