# TidyBot Service Development

Build and deploy a backend ML service for the TidyBot ecosystem.

## Before You Start

1. Read `docs/CLIENT_SDK_SPEC.md` — every client SDK must follow this spec
2. Read `docs/SERVICE_MANIFEST_SPEC.md` — every service needs a `service.yaml`
3. Check GPU VRAM usage: `curl http://<compute-node>:9000/gpus`

## Step-by-Step Build Workflow

### 1. Create the repo

```bash
gh repo create TidyBot-Services/<service-name> --public --clone
cd <service-name>
```

### 2. Build `main.py` (FastAPI server)

Every service MUST have:

- **`GET /health`** → `{"status": "ok"}` — health check endpoint
- **`POST /<action>`** — primary inference endpoint accepting base64-encoded image/data
- **Lifespan handler** — load model weights at startup, not per-request
- **CUDA device selection** — respect `CUDA_VISIBLE_DEVICES` env var
- **Error handling** — return proper HTTP error codes, not crash

Template structure:

```python
"""<Service Name> — TidyBot Backend Service"""
import base64, io, torch
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import numpy as np

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MODEL = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global MODEL
    MODEL = load_your_model()  # Load once at startup
    yield

app = FastAPI(title="<Service Name>", lifespan=lifespan)

@app.get("/health")
def health():
    return {"status": "ok"}

class PredictRequest(BaseModel):
    image: str  # base64 encoded

@app.post("/predict")
def predict(req: PredictRequest):
    img_bytes = base64.b64decode(req.image)
    # ... run inference ...
    return {"results": [...]}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=<PORT>)
```

### 3. Build `client.py` (Client SDK)

**Must follow `CLIENT_SDK_SPEC.md` exactly:**

- Use `urllib.request` only (NOT `requests`)
- Accept `bytes` input for images/data
- Include `health()` method
- Constructor takes `host` parameter
- Type hints and docstrings on all public methods

### 4. Create `service.yaml` (Deploy Manifest)

```yaml
name: <service-name>
version: 0.1.0
description: One-line description

requires:
  gpu: true
  vram_gb: 4

deploy:
  image: tidybot/<service-name>:0.1.0
  port: <PORT>
  health: /health
  ready_timeout: 120

client: client.py
```

See `docs/SERVICE_MANIFEST_SPEC.md` for all fields.

### 5. Create `Dockerfile`

```dockerfile
FROM nvidia/cuda:12.8.1-runtime-ubuntu22.04

RUN apt-get update && apt-get install -y python3 python3-pip && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install PyTorch with CUDA support
RUN pip3 install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cu128

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY main.py .
# COPY weights/ weights/  # if needed

EXPOSE <PORT>
CMD ["python3", "main.py"]
```

**Important:** Match the CUDA version to the compute node's GPU. RTX 5090 (Blackwell) needs CUDA 12.8+ and PyTorch 2.7+.

### 6. Test locally

```bash
# Build the image
docker build -t tidybot/<service-name>:0.1.0 .

# Run with GPU
docker run --gpus all -p <PORT>:<PORT> tidybot/<service-name>:0.1.0

# Test health
curl http://localhost:<PORT>/health

# Test with client SDK
python -c "
from client import Client
c = Client('http://localhost:<PORT>')
print('Health:', c.health())
"
```

### 7. Push to GitHub

```bash
echo "venv/" > .gitignore
echo "*.pt" >> .gitignore
echo "*.pth" >> .gitignore

git add -A
git commit -m "Initial <service-name> service"
git push -u origin main
```

### 8. Deploy via deploy-agent

```bash
curl -X POST http://<compute-node>:9000/deploy \
  -H "Content-Type: application/json" \
  -d '{
    "name": "<service-name>",
    "image": "tidybot/<service-name>:0.1.0",
    "port": <PORT>,
    "gpu": true,
    "vram_gb": 4,
    "health": "/health",
    "ready_timeout": 120
  }'
```

The deploy-agent assigns a GPU, starts the container, waits for the health check, and returns the endpoint URL.

## Common Pitfalls

- **CUDA version mismatch** — RTX 5090 needs PyTorch 2.7+ with CUDA 12.8. Always check `nvidia-smi` for the GPU's compute capability
- **Using `requests` in client.py** — must use `urllib` per spec
- **Hardcoding server IP in client.py** — must be a constructor parameter
- **Loading models per-request** — use lifespan/startup handler to load once
- **Not testing the client SDK** — always test client.py against the running server
- **Missing health endpoint** — deploy-agent will fail the deploy if `/health` doesn't return 200
