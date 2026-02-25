---
name: tidybot-active-services
description: External services — GPU model endpoints, vision APIs, grasping backends, and Python client SDKs available to robot skills. Use when (1) a skill needs an external model or API, (2) checking what backend services are available, (3) deploying a service that isn't running yet, (4) debugging service timeouts or connection failures.
---

# Active Services

## Discovering Services

Query the deploy-agent on compute nodes to see what's running:

```bash
curl http://<compute-node>:9000/services
```

Returns a list of running services with name, host URL, port, GPU assignment, and health status.

## Using a Service

1. Check if the service is running: `GET http://<compute-node>:9000/services/<name>`
2. If not running, read the service repo's `service.yaml` for deploy config
3. Deploy it: `POST http://<compute-node>:9000/deploy` with the service.yaml fields
4. Use the returned host URL with the service's `client.py`

## Deploying a Service

```bash
curl -X POST http://<compute-node>:9000/deploy \
  -H "Content-Type: application/json" \
  -d '{
    "name": "<service-name>",
    "image": "tidybot/<service-name>:0.1.0",
    "port": 8006,
    "gpu": true,
    "vram_gb": 4,
    "health": "/health"
  }'
```

The deploy-agent assigns a GPU, starts the container, waits for health check, and returns the endpoint URL.

## Checking GPU Status

```bash
curl http://<compute-node>:9000/gpus
```

Returns GPU IDs, VRAM total/used, and which services are assigned to each GPU.

## Service Repos

Each service is a repo under [TidyBot-Services](https://github.com/TidyBot-Services) containing:
- `service.yaml` — deploy manifest (image, port, GPU requirements)
- `client.py` — Python client SDK (urllib only, no external deps)
- `main.py` — FastAPI server
- `Dockerfile` — container build
