# TOOLS.md - Service Agent Reference

## Systemd Service Management

All services run as systemd units. **Never use nohup, setsid, screen, or tmux for production services.**

### Creating a new service unit

```bash
# Write the unit file
sudo tee /etc/systemd/system/tidybot-<name>.service << 'EOF'
[Unit]
Description=TidyBot <Name> Service
After=network.target

[Service]
Type=simple
User=<your-user>
WorkingDirectory=/home/<your-user>/<service-name>
ExecStart=/home/<your-user>/<service-name>/venv/bin/python main.py
Restart=on-failure
RestartSec=10
Environment=CUDA_VISIBLE_DEVICES=0

[Install]
WantedBy=multi-user.target
EOF

# Enable and start (separate commands — do NOT use --now)
sudo systemctl daemon-reload
sudo systemctl enable tidybot-<name>.service
sudo systemctl start tidybot-<name>.service
```

### Sudoers setup (required for the agent)

The agent needs passwordless sudo for systemd operations:

```bash
# Add to /etc/sudoers.d/tidybot-services
<your-user> ALL=(ALL) NOPASSWD: /usr/bin/systemctl daemon-reload
<your-user> ALL=(ALL) NOPASSWD: /usr/bin/systemctl enable tidybot-*
<your-user> ALL=(ALL) NOPASSWD: /usr/bin/systemctl start tidybot-*
<your-user> ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop tidybot-*
<your-user> ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart tidybot-*
<your-user> ALL=(ALL) NOPASSWD: /usr/bin/systemctl status tidybot-*
<your-user> ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/systemd/system/tidybot-*
```

### Common commands

```bash
# Check all tidybot services
systemctl list-units 'tidybot-*' --all

# View logs
journalctl -u tidybot-<name> -f

# Restart a service
sudo systemctl restart tidybot-<name>
```

## Port Allocation

Services are assigned sequential ports starting at 8000. **Always check catalog.json for used ports before assigning a new one.**

```bash
# Check what's in use
gh api repos/TidyBot-Services/services_wishlist/contents/catalog.json \
  --jq '.content' | base64 -d | python3 -c "
import sys, json
cat = json.load(sys.stdin)
for name, svc in cat.get('capabilities', {}).items():
    print(f\"{svc.get('host', 'unknown'):40s} {name}\")
"
```

## GitHub API Patterns

**Always use `gh api` instead of `web_fetch` for GitHub** — web_fetch has caching issues.

```bash
# Fetch a file from a repo
gh api repos/TidyBot-Services/services_wishlist/contents/wishlist.json \
  --jq '.content' | base64 -d

# Update a file (requires current SHA)
SHA=$(gh api repos/<org>/<repo>/contents/<file> --jq '.sha')
echo '{"message":"update","content":"'$(base64 -w0 < file.json)'","sha":"'$SHA'"}' | \
  gh api repos/<org>/<repo>/contents/<file> -X PUT --input -

# Create a new repo
gh repo create TidyBot-Services/<name> --public --clone
```

## Python Environment

```bash
# Always create a venv per service
python3 -m venv venv
source venv/bin/activate

# CRITICAL: pin numpy<2 for torch compatibility
pip install 'numpy<2' torch torchvision

# After installing, freeze
pip freeze > requirements.txt
```

## GPU Monitoring

```bash
# Check VRAM usage
nvidia-smi

# Watch in real time
watch -n 1 nvidia-smi

# Check which processes are using GPU
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
```

## Service Health Checks

```bash
# Quick health check for a single service
curl -s http://localhost:<port>/health | python3 -m json.tool

# Check all services
for port in 8000 8001 8002 8003 8004 8005 8006 8007; do
  echo -n "Port $port: "
  curl -sf http://localhost:$port/health && echo " OK" || echo " DOWN"
done
```

---

_Update this file with your server-specific details: IP address, GPU model, username, port assignments._
