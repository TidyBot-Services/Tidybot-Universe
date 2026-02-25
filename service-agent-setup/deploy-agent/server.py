"""Deploy Agent — lightweight service deployment daemon for compute nodes.

Skill agents call this to deploy, query, and stop services.
Requires Docker access (mount /var/run/docker.sock).
"""

import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import docker
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Deploy Agent", version="0.1.0")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

STATE_DIR = Path(os.environ.get("DEPLOY_STATE_DIR", "/var/lib/deploy-agent"))
CONTAINER_PREFIX = "tidybot-"
PORT_RANGE_START = 8000
PORT_RANGE_END = 9000

# ---------------------------------------------------------------------------
# Docker client
# ---------------------------------------------------------------------------

_docker: docker.DockerClient | None = None


def get_docker() -> docker.DockerClient:
    global _docker
    if _docker is None:
        _docker = docker.from_env()
    return _docker


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

@dataclass
class ServiceRecord:
    name: str
    image: str
    port: int  # host port
    container_port: int  # container internal port
    gpu: int | None  # GPU device index, or None
    health: str
    container_id: str


_services: dict[str, ServiceRecord] = {}


def _state_file() -> Path:
    return STATE_DIR / "services.json"


def _save_state():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    data = {}
    for name, rec in _services.items():
        data[name] = {
            "name": rec.name,
            "image": rec.image,
            "port": rec.port,
            "container_port": rec.container_port,
            "gpu": rec.gpu,
            "health": rec.health,
            "container_id": rec.container_id,
        }
    _state_file().write_text(json.dumps(data, indent=2))


def _load_state():
    """Load state and reconcile with running containers."""
    if not _state_file().exists():
        return

    data = json.loads(_state_file().read_text())
    client = get_docker()

    for name, info in data.items():
        try:
            container = client.containers.get(info["container_id"])
            if container.status == "running":
                _services[name] = ServiceRecord(**info)
        except docker.errors.NotFound:
            pass  # container gone, skip

    _save_state()


# ---------------------------------------------------------------------------
# GPU helpers
# ---------------------------------------------------------------------------

def _get_gpu_info() -> list[dict]:
    """Query nvidia-smi for GPU status."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return []

        gpus = []
        for line in result.stdout.strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) != 4:
                continue
            idx = int(parts[0])
            # Find which services are on this GPU
            services_on_gpu = [
                s.name for s in _services.values() if s.gpu == idx
            ]
            gpus.append({
                "id": idx,
                "name": parts[1],
                "vram_total_gb": round(int(parts[2]) / 1024, 1),
                "vram_used_gb": round(int(parts[3]) / 1024, 1),
                "services": services_on_gpu,
            })
        return gpus
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


def _pick_gpu(vram_gb: int) -> int | None:
    """Pick the GPU with most free VRAM that meets the requirement."""
    gpus = _get_gpu_info()
    if not gpus:
        return None

    best = None
    best_free = -1
    for gpu in gpus:
        free = gpu["vram_total_gb"] - gpu["vram_used_gb"]
        if free >= vram_gb and free > best_free:
            best = gpu["id"]
            best_free = free

    return best


# ---------------------------------------------------------------------------
# Port helpers
# ---------------------------------------------------------------------------

def _port_in_use(port: int) -> bool:
    """Check if a port is currently in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _pick_port(preferred: int) -> int:
    """Return preferred port if free, otherwise find the next available."""
    if not _port_in_use(preferred) and preferred not in {s.port for s in _services.values()}:
        return preferred

    for port in range(PORT_RANGE_START, PORT_RANGE_END):
        if port not in {s.port for s in _services.values()} and not _port_in_use(port):
            return port

    raise HTTPException(503, "No available ports")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def _wait_for_health(host: str, path: str, timeout: int) -> bool:
    """Poll a health endpoint until it returns 200 or timeout."""
    import urllib.request
    import urllib.error

    url = f"{host}{path}"
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(2)

    return False


def _check_health(rec: ServiceRecord) -> str:
    """Quick health check on a running service."""
    import urllib.request

    url = f"http://127.0.0.1:{rec.port}{rec.health}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=3) as resp:
            return "healthy" if resp.status == 200 else "unhealthy"
    except Exception:
        return "unhealthy"


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------

class DeployRequest(BaseModel):
    name: str
    image: str
    port: int = 8000
    gpu: bool = False
    vram_gb: int = 0
    env: dict[str, str] = {}
    volumes: list[str] = []
    health: str = "/health"
    ready_timeout: int = 60
    command: str | None = None


class StopRequest(BaseModel):
    name: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    gpus = _get_gpu_info()
    return {
        "status": "ok",
        "hostname": socket.gethostname(),
        "gpus": len(gpus),
        "services_running": len(_services),
    }


@app.get("/services")
def list_services():
    result = []
    for rec in _services.values():
        status = _check_health(rec)
        result.append({
            "name": rec.name,
            "host": f"http://{_get_host_ip()}:{rec.port}",
            "port": rec.port,
            "gpu": rec.gpu,
            "status": status,
            "image": rec.image,
        })
    return result


@app.get("/services/{name}")
def get_service(name: str):
    if name not in _services:
        raise HTTPException(404, f"Service '{name}' not deployed")
    rec = _services[name]
    status = _check_health(rec)
    return {
        "name": rec.name,
        "host": f"http://{_get_host_ip()}:{rec.port}",
        "port": rec.port,
        "gpu": rec.gpu,
        "status": status,
        "image": rec.image,
        "container_id": rec.container_id,
    }


@app.post("/deploy")
def deploy(req: DeployRequest):
    # If already running and healthy, return existing
    if req.name in _services:
        rec = _services[req.name]
        status = _check_health(rec)
        if status == "healthy":
            return {
                "ok": True,
                "name": rec.name,
                "host": f"http://{_get_host_ip()}:{rec.port}",
                "gpu": rec.gpu,
                "status": "healthy",
                "already_running": True,
            }
        # Unhealthy — remove and redeploy
        _stop_container(req.name)

    # GPU assignment
    gpu_id = None
    if req.gpu:
        gpu_id = _pick_gpu(req.vram_gb)
        if gpu_id is None:
            raise HTTPException(503, f"No GPU with {req.vram_gb}GB free VRAM available")

    # Port assignment
    host_port = _pick_port(req.port)

    # Pull image (skip if already available locally)
    client = get_docker()
    try:
        client.images.get(req.image)
        print(f"[deploy] Image {req.image} found locally")
    except docker.errors.ImageNotFound:
        print(f"[deploy] Pulling {req.image}...")
        try:
            client.images.pull(req.image)
        except Exception as e:
            raise HTTPException(404, f"Image '{req.image}' not found: {e}")

    # Build container config
    container_name = f"{CONTAINER_PREFIX}{req.name}"
    environment = dict(req.env)
    device_requests = []

    if gpu_id is not None:
        environment["CUDA_VISIBLE_DEVICES"] = str(gpu_id)
        device_requests = [
            docker.types.DeviceRequest(
                device_ids=[str(gpu_id)],
                capabilities=[["gpu"]],
            )
        ]

    # Parse volumes
    volumes = {}
    for v in req.volumes:
        parts = v.split(":")
        if len(parts) == 2:
            volumes[parts[0]] = {"bind": parts[1], "mode": "rw"}

    # Remove stale container with same name
    try:
        old = client.containers.get(container_name)
        old.remove(force=True)
    except docker.errors.NotFound:
        pass

    # Run container
    print(f"[deploy] Starting {container_name} on port {host_port}, GPU {gpu_id}...")
    container = client.containers.run(
        req.image,
        name=container_name,
        detach=True,
        ports={f"{req.port}/tcp": host_port},
        environment=environment,
        volumes=volumes,
        device_requests=device_requests or None,
        command=req.command,
        restart_policy={"Name": "unless-stopped"},
    )

    # Wait for healthy
    host_url = f"http://127.0.0.1:{host_port}"
    print(f"[deploy] Waiting for {host_url}{req.health} (timeout {req.ready_timeout}s)...")
    healthy = _wait_for_health(host_url, req.health, req.ready_timeout)

    if not healthy:
        # Clean up failed deploy
        container.remove(force=True)
        raise HTTPException(503, f"Service '{req.name}' failed health check after {req.ready_timeout}s")

    # Record
    rec = ServiceRecord(
        name=req.name,
        image=req.image,
        port=host_port,
        container_port=req.port,
        gpu=gpu_id,
        health=req.health,
        container_id=container.id,
    )
    _services[req.name] = rec
    _save_state()

    print(f"[deploy] {req.name} ready at {host_url}")
    return {
        "ok": True,
        "name": req.name,
        "host": f"http://{_get_host_ip()}:{host_port}",
        "gpu": gpu_id,
        "status": "healthy",
        "already_running": False,
    }


@app.post("/stop")
def stop(req: StopRequest):
    if req.name not in _services:
        raise HTTPException(404, f"Service '{req.name}' not deployed")
    _stop_container(req.name)
    return {"ok": True, "name": req.name}


@app.get("/gpus")
def gpus():
    return _get_gpu_info()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stop_container(name: str):
    """Stop and remove a service container."""
    if name not in _services:
        return
    rec = _services[name]
    client = get_docker()
    try:
        container = client.containers.get(rec.container_id)
        container.remove(force=True)
    except docker.errors.NotFound:
        pass
    del _services[name]
    _save_state()
    print(f"[deploy] Stopped {name}")


def _get_host_ip() -> str:
    """Get the host's LAN IP address."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
def startup():
    _load_state()
    print(f"[deploy-agent] {len(_services)} services restored from state")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Deploy Agent")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
