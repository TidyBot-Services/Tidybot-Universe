#!/usr/bin/env python3
"""Service Catalog Server — discovers and documents ML services on remote GPU servers.

Scans a remote service directory via SSH, extracts API documentation from
Python files, and serves a live catalog via FastAPI on localhost.

Usage:
    python3 service_scanner.py                    # Start doc server (default :8090)
    python3 service_scanner.py --scan-once        # Scan and print, then exit
    python3 service_scanner.py --port 9090        # Custom port
    python3 service_scanner.py --config path.json # Custom config
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class EndpointInfo:
    path: str
    method: str
    description: str = ""

@dataclass
class ServiceInfo:
    name: str
    directory: str
    description: str = ""
    port: Optional[int] = None
    endpoints: list = field(default_factory=list)
    status: str = "unknown"        # running | stopped | unknown | server_down
    has_dockerfile: bool = False
    has_service_yaml: bool = False
    readme_excerpt: str = ""
    requirements: list = field(default_factory=list)
    last_scanned: str = ""

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_config: dict = {}
_services: dict[str, ServiceInfo] = {}
_lock = threading.Lock()
_last_scan_time: str = ""
_server_reachable: bool = True
_consecutive_failures: int = 0
_last_successful_contact: str = ""
_alert_message: str = ""

FAILURE_THRESHOLD = 3  # consecutive SSH failures before alert

# ---------------------------------------------------------------------------
# SSH layer
# ---------------------------------------------------------------------------

def _ssh_exec(cmd: str, timeout: int = 10) -> tuple[bool, str]:
    """Execute a command on the remote server via SSH.

    Returns (success, stdout_or_error).
    """
    ssh_target = f"{_config['username']}@{_config['server_ip']}"
    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
             ssh_target, cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "SSH timeout"
    except Exception as e:
        return False, str(e)


def _ssh_read_file(remote_path: str) -> Optional[str]:
    """Read a file from the remote server. Returns None on failure."""
    ok, content = _ssh_exec(f"cat {shlex.quote(remote_path)}")
    return content if ok else None

# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

SERVICE_MARKERS = {"server.py", "main.py", "app.py", "Dockerfile", "service.yaml",
                   "requirements.txt", "start_server.sh"}

# Additional patterns — files matching these indicate a service even if exact names differ
SERVICE_MARKER_PATTERNS = [
    re.compile(r".*_server\.py$"),      # graspgen_server.py, yolo_server.py
    re.compile(r".*_service\.py$"),     # yolo_service.py
    re.compile(r"start_.*\.sh$"),       # start_yolo_server.sh
    re.compile(r".*\.pth$"),            # model weights indicate ML service
]

ROUTE_PATTERN = re.compile(
    r'@(?:app|router)\.(get|post|put|delete|patch)\(\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)

PORT_ARGPARSE_PATTERN = re.compile(
    r'["\']--port["\'].*?default\s*=\s*(\d+)', re.DOTALL
)

PORT_UVICORN_PATTERN = re.compile(
    r'uvicorn\.run\(.*?port\s*=\s*(\d+)', re.DOTALL
)

PORT_EXPOSE_PATTERN = re.compile(r'EXPOSE\s+(\d+)')


def _check_ssh_connectivity() -> bool:
    """Quick SSH connectivity check."""
    ok, _ = _ssh_exec("echo ok", timeout=8)
    return ok


def _check_running_ports() -> set[int]:
    """Get set of ports with active listeners on the remote server."""
    ok, output = _ssh_exec(
        "ss -tlnp 2>/dev/null | awk 'NR>1 {print $4}' | grep -oP ':\\K[0-9]+' | sort -un",
        timeout=8,
    )
    if not ok:
        return set()
    ports = set()
    for line in output.splitlines():
        try:
            ports.add(int(line.strip()))
        except ValueError:
            pass
    return ports


def _is_service_dir(name: str, files: set[str]) -> bool:
    """Check if a set of filenames looks like a service."""
    markers = files & SERVICE_MARKERS
    # Also check pattern-based markers
    for f in files:
        for pat in SERVICE_MARKER_PATTERNS:
            if pat.match(f):
                markers.add(f)
    # A single server.py with "server" in the directory name is enough
    if "server.py" in markers and "server" in name.lower():
        return True
    return len(markers) >= 2 or "service.yaml" in markers


def _extract_port(server_content: str, dockerfile_content: Optional[str] = None,
                  service_yaml: Optional[dict] = None) -> Optional[int]:
    """Extract port from service files."""
    # Priority 1: service.yaml
    if service_yaml:
        try:
            port = service_yaml.get("deploy", {}).get("port")
            if port:
                return int(port)
        except (TypeError, ValueError):
            pass

    # Priority 2: argparse --port default
    m = PORT_ARGPARSE_PATTERN.search(server_content)
    if m:
        return int(m.group(1))

    # Priority 3: uvicorn.run(port=)
    m = PORT_UVICORN_PATTERN.search(server_content)
    if m:
        return int(m.group(1))

    # Priority 4: Dockerfile EXPOSE
    if dockerfile_content:
        m = PORT_EXPOSE_PATTERN.search(dockerfile_content)
        if m:
            return int(m.group(1))

    return None


def _extract_endpoints(server_content: str) -> list[EndpointInfo]:
    """Parse FastAPI route decorators from Python source."""
    endpoints = []
    lines = server_content.splitlines()

    for i, line in enumerate(lines):
        m = ROUTE_PATTERN.search(line)
        if m:
            method = m.group(1).upper()
            path = m.group(2)

            # Look for docstring in the next few lines
            desc = ""
            for j in range(i + 1, min(i + 10, len(lines))):
                stripped = lines[j].strip()
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    desc = stripped.strip('"').strip("'")
                    break
                if stripped.startswith("def ") or stripped.startswith("@"):
                    break

            endpoints.append(EndpointInfo(path=path, method=method, description=desc))

    return endpoints


def _extract_description(readme: Optional[str], service_yaml: Optional[dict]) -> str:
    """Get description from README or service.yaml."""
    if service_yaml and "description" in service_yaml:
        return str(service_yaml["description"])
    if readme:
        # First non-empty, non-heading line
        for line in readme.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("="):
                return line[:200]
    return ""


def scan_services() -> dict[str, ServiceInfo]:
    """Full scan of the remote service directory."""
    service_dir = _config["service_dir"]
    now = datetime.now().isoformat()

    # Batch: list all subdirs and their files in one SSH call
    ok, output = _ssh_exec(
        f"cd {shlex.quote(service_dir)} && "
        f"for d in */; do "
        f"  [ -d \"$d\" ] && echo \"DIR:$d\" && ls -1 \"$d\" 2>/dev/null; "
        f"done",
        timeout=15,
    )
    if not ok:
        return {}

    # Parse directory listing
    dirs: dict[str, set[str]] = {}
    current_dir = None
    for line in output.splitlines():
        if line.startswith("DIR:"):
            current_dir = line[4:].rstrip("/")
            dirs[current_dir] = set()
        elif current_dir is not None:
            dirs[current_dir].add(line.strip())

    # Check running ports
    running_ports = _check_running_ports()

    # Process each directory
    services = {}
    for name, files in dirs.items():
        if not _is_service_dir(name, files):
            continue

        remote_dir = f"{service_dir}/{name}"
        info = ServiceInfo(name=name, directory=remote_dir, last_scanned=now)

        # Read key files — check exact names first, then pattern matches
        server_content = ""
        py_candidates = ["server.py", "main.py", "app.py"]
        # Add pattern-matched Python files (e.g., yolo_server.py, yolo_service.py)
        for f in files:
            if f.endswith(".py") and ("server" in f.lower() or "service" in f.lower()):
                if f not in py_candidates:
                    py_candidates.append(f)
        for py_file in py_candidates:
            if py_file in files:
                content = _ssh_read_file(f"{remote_dir}/{py_file}")
                if content:
                    server_content = content
                    break

        readme = _ssh_read_file(f"{remote_dir}/README.md") if "README.md" in files else None
        info.readme_excerpt = (readme or "")[:500]

        dockerfile = _ssh_read_file(f"{remote_dir}/Dockerfile") if "Dockerfile" in files else None
        info.has_dockerfile = "Dockerfile" in files

        service_yaml = None
        if "service.yaml" in files:
            info.has_service_yaml = True
            yaml_content = _ssh_read_file(f"{remote_dir}/service.yaml")
            if yaml_content:
                try:
                    import yaml
                    service_yaml = yaml.safe_load(yaml_content)
                except Exception:
                    pass

        if "requirements.txt" in files:
            req_content = _ssh_read_file(f"{remote_dir}/requirements.txt")
            if req_content:
                info.requirements = [l.strip() for l in req_content.splitlines() if l.strip() and not l.startswith("#")]

        # Extract info
        info.port = _extract_port(server_content, dockerfile, service_yaml)
        info.endpoints = _extract_endpoints(server_content)
        info.description = _extract_description(readme, service_yaml)

        # Check running status
        if info.port and info.port in running_ports:
            info.status = "running"
            # Optionally ping /health
            try:
                import urllib.request
                url = f"http://{_config['server_ip']}:{info.port}/health"
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=3) as resp:
                    if resp.status == 200:
                        info.status = "running"
            except Exception:
                info.status = "running"  # port is open even if /health doesn't exist
        elif info.port:
            info.status = "stopped"
        else:
            info.status = "unknown"

        services[name] = info

    return services


# ---------------------------------------------------------------------------
# Background poller
# ---------------------------------------------------------------------------

def _poll_loop():
    """Background polling loop."""
    global _services, _last_scan_time, _server_reachable, _consecutive_failures
    global _last_successful_contact, _alert_message

    interval = _config.get("poll_interval_seconds", 30)

    while True:
        time.sleep(interval)

        # Check connectivity first
        if not _check_ssh_connectivity():
            _consecutive_failures += 1
            _server_reachable = False

            if _consecutive_failures >= FAILURE_THRESHOLD:
                _alert_message = f"Server unreachable since {_last_successful_contact}"
                print(f"[ALERT] Remote server {_config['server_ip']} is unreachable! "
                      f"Last successful contact: {_last_successful_contact} "
                      f"({_consecutive_failures} consecutive failures)")

                # Mark all services as server_down
                with _lock:
                    for svc in _services.values():
                        svc.status = "server_down"
            else:
                print(f"[service-catalog] SSH failed ({_consecutive_failures}/{FAILURE_THRESHOLD})")

            continue

        # Connectivity restored
        if not _server_reachable:
            print(f"[service-catalog] Server {_config['server_ip']} is reachable again")
            _alert_message = ""

        _server_reachable = True
        _consecutive_failures = 0
        _last_successful_contact = datetime.now().isoformat()

        try:
            new_services = scan_services()
            _last_scan_time = datetime.now().isoformat()

            # Diff
            with _lock:
                old_names = set(_services.keys())
                new_names = set(new_services.keys())

                added = new_names - old_names
                removed = old_names - new_names
                for name in added:
                    print(f"[service-catalog] NEW service: {name} (port={new_services[name].port})")
                for name in removed:
                    print(f"[service-catalog] REMOVED service: {name}")

                # Check status changes
                for name in old_names & new_names:
                    if _services[name].status != new_services[name].status:
                        print(f"[service-catalog] {name}: {_services[name].status} → {new_services[name].status}")

                _services = new_services

        except Exception as e:
            print(f"[service-catalog] Scan error: {e}")


def _start_poller():
    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Service Catalog",
    description="Live catalog of ML services on remote GPU servers",
    version="0.1.0",
)


@app.on_event("startup")
def startup():
    global _services, _last_scan_time, _last_successful_contact, _server_reachable

    print(f"[service-catalog] Scanning {_config['server_ip']}:{_config['service_dir']}...")
    _services = scan_services()
    _last_scan_time = datetime.now().isoformat()
    _last_successful_contact = _last_scan_time
    _server_reachable = True
    print(f"[service-catalog] Found {len(_services)} services")

    _start_poller()
    print(f"[service-catalog] Background poller started (every {_config.get('poll_interval_seconds', 30)}s)")


@app.get("/health")
def health():
    result = {
        "status": "ok",
        "server_ip": _config.get("server_ip"),
        "server_reachable": _server_reachable,
        "services_cataloged": len(_services),
        "last_scan": _last_scan_time,
        "last_successful_contact": _last_successful_contact,
    }
    if _alert_message:
        result["alert"] = _alert_message
    return result


@app.get("/services")
def list_services():
    with _lock:
        return {
            "server_ip": _config.get("server_ip"),
            "server_reachable": _server_reachable,
            "services": [
                {
                    "name": s.name,
                    "description": s.description,
                    "port": s.port,
                    "status": s.status,
                    "endpoint_count": len(s.endpoints),
                    "has_dockerfile": s.has_dockerfile,
                    "url": f"http://{_config['server_ip']}:{s.port}" if s.port else None,
                }
                for s in _services.values()
            ],
            "count": len(_services),
            "last_scan": _last_scan_time,
        }


@app.get("/services/{name}")
def get_service(name: str):
    with _lock:
        if name not in _services:
            raise HTTPException(404, f"Service '{name}' not found")
        s = _services[name]
        return {
            **asdict(s),
            "url": f"http://{_config['server_ip']}:{s.port}" if s.port else None,
            "endpoints": [asdict(e) for e in s.endpoints],
        }


@app.get("/services/{name}/endpoints")
def get_service_endpoints(name: str):
    with _lock:
        if name not in _services:
            raise HTTPException(404, f"Service '{name}' not found")
        s = _services[name]
        return {
            "name": s.name,
            "port": s.port,
            "url": f"http://{_config['server_ip']}:{s.port}" if s.port else None,
            "endpoints": [asdict(e) for e in s.endpoints],
        }


@app.post("/rescan")
def trigger_rescan():
    global _services, _last_scan_time
    try:
        new_services = scan_services()
        with _lock:
            old_count = len(_services)
            _services = new_services
        _last_scan_time = datetime.now().isoformat()
        return {
            "ok": True,
            "services_found": len(_services),
            "previous_count": old_count,
        }
    except Exception as e:
        raise HTTPException(500, f"Scan failed: {e}")


@app.get("/docs/html", response_class=HTMLResponse)
def docs_page():
    with _lock:
        rows = ""
        for s in sorted(_services.values(), key=lambda x: x.name):
            status_color = {"running": "#2ecc71", "stopped": "#e74c3c",
                            "unknown": "#95a5a6", "server_down": "#e74c3c"}.get(s.status, "#95a5a6")
            eps = ", ".join(f"{e.method} {e.path}" for e in s.endpoints[:5])
            url = f"http://{_config['server_ip']}:{s.port}" if s.port else "N/A"
            rows += f"""
            <tr>
                <td><strong>{s.name}</strong></td>
                <td>{s.description[:80]}</td>
                <td>{s.port or 'N/A'}</td>
                <td><span style="color:{status_color}; font-weight:bold">{s.status}</span></td>
                <td><code>{eps or 'none detected'}</code></td>
                <td><code>{url}</code></td>
            </tr>"""

        alert_banner = ""
        if _alert_message:
            alert_banner = f'<div style="background:#e74c3c;color:white;padding:12px;margin-bottom:16px;border-radius:4px">⚠ {_alert_message}</div>'

        return f"""<!DOCTYPE html>
<html><head><title>Service Catalog — {_config.get('server_ip')}</title>
<style>
body {{ font-family: -apple-system, sans-serif; margin: 40px; background: #f5f5f5; }}
h1 {{ color: #2c3e50; }}
table {{ border-collapse: collapse; width: 100%; background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
th, td {{ padding: 10px 14px; text-align: left; border-bottom: 1px solid #eee; }}
th {{ background: #2c3e50; color: white; }}
tr:hover {{ background: #f8f9fa; }}
code {{ background: #eee; padding: 2px 6px; border-radius: 3px; font-size: 0.85em; }}
.meta {{ color: #7f8c8d; font-size: 0.9em; margin-bottom: 20px; }}
</style></head><body>
<h1>Service Catalog</h1>
{alert_banner}
<p class="meta">Server: <strong>{_config.get('server_ip')}</strong> &nbsp;|&nbsp;
   Services: <strong>{len(_services)}</strong> &nbsp;|&nbsp;
   Last scan: {_last_scan_time or 'never'} &nbsp;|&nbsp;
   API: <a href="/services">/services</a></p>
<table>
<tr><th>Name</th><th>Description</th><th>Port</th><th>Status</th><th>Endpoints</th><th>URL</th></tr>
{rows}
</table>
<p class="meta" style="margin-top:20px">Auto-refreshes every {_config.get('poll_interval_seconds', 30)}s.
<a href="/rescan" onclick="fetch('/rescan',{{method:'POST'}}).then(()=>location.reload());return false;">Force rescan</a></p>
</body></html>"""


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_config(config_path: Optional[str] = None) -> dict:
    paths = []
    if config_path:
        paths.append(config_path)
    paths.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
    paths.append(os.path.expanduser("~/.config/tidybot/service-server.json"))

    for p in paths:
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f)

    raise FileNotFoundError(
        "No config.json found. Run setup.sh first:\n"
        "  bash setup.sh --server-ip IP --username USER --service-dir DIR"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global _config

    parser = argparse.ArgumentParser(description="Service Catalog Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--config", default=None, help="Path to config.json")
    parser.add_argument("--scan-once", action="store_true",
                        help="Scan and print results, then exit")
    args = parser.parse_args()

    _config = _load_config(args.config)

    port = args.port or _config.get("local_port", 8090)

    if args.scan_once:
        print(f"Scanning {_config['server_ip']}:{_config['service_dir']}...")
        services = scan_services()
        if not services:
            print("  No services found (or SSH failed)")
            return

        print(f"\nFound {len(services)} services:\n")
        running_ports = _check_running_ports()
        for name, info in sorted(services.items()):
            status = info.status
            eps = len(info.endpoints)
            print(f"  {name:30s}  port={str(info.port or '?'):6s}  "
                  f"status={status:8s}  endpoints={eps}  "
                  f"{'[Dockerfile]' if info.has_dockerfile else ''}")
            for ep in info.endpoints:
                print(f"    {ep.method:6s} {ep.path:30s}  {ep.description[:50]}")
        return

    print(f"Starting Service Catalog on http://{args.host}:{port}")
    print(f"  Remote: {_config['username']}@{_config['server_ip']}:{_config['service_dir']}")
    print(f"  Poll interval: {_config.get('poll_interval_seconds', 30)}s")
    print(f"  Docs: http://{args.host}:{port}/docs/html")

    uvicorn.run(app, host=args.host, port=port, log_level="info")


if __name__ == "__main__":
    main()
