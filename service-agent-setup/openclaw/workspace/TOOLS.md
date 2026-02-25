# TOOLS.md - Service Agent Reference

## Deploy-Agent API

The deploy-agent runs on each compute node at port 9000. All service lifecycle management goes through it.

```bash
# Check node health
curl -s http://<compute-node>:9000/health

# List running services
curl -s http://<compute-node>:9000/services

# Get single service status
curl -s http://<compute-node>:9000/services/<name>

# Deploy a service
curl -s -X POST http://<compute-node>:9000/deploy \
  -H "Content-Type: application/json" \
  -d '{"name": "grasp-service", "image": "tidybot/grasp-service:0.1.0", "port": 8006, "gpu": true, "vram_gb": 4}'

# Stop a service
curl -s -X POST http://<compute-node>:9000/stop \
  -H "Content-Type: application/json" \
  -d '{"name": "grasp-service"}'

# Check GPU status
curl -s http://<compute-node>:9000/gpus
```

## Docker (for building images via SSH)

```bash
# Build a service image
docker build -t tidybot/<service-name>:0.1.0 .

# Run locally for testing
docker run --gpus all -p 8006:8006 tidybot/<service-name>:0.1.0

# Check running containers
docker ps --filter name=tidybot-

# View container logs
docker logs tidybot-<service-name>

# Remove old images
docker image prune
```

## GitHub API Patterns

**Always use `gh api` instead of `web_fetch` for GitHub** — web_fetch has caching issues.

```bash
# Fetch a file from a repo
gh api repos/TidyBot-Services/<repo>/contents/<file> --jq '.content' | base64 -d

# Update a file (requires current SHA)
SHA=$(gh api repos/<org>/<repo>/contents/<file> --jq '.sha')
echo '{"message":"update","content":"'$(base64 -w0 < file.json)'","sha":"'$SHA'"}' | \
  gh api repos/<org>/<repo>/contents/<file> -X PUT --input -

# Create a new repo
gh repo create TidyBot-Services/<name> --public --clone
```

## Python Environment (for local development)

```bash
# Always create a venv per service
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install fastapi uvicorn torch torchvision

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

# Check all services via deploy-agent
curl -s http://localhost:9000/services | python3 -m json.tool
```

---

_Update this file with your server-specific details: compute node IP, GPU model, port assignments._
