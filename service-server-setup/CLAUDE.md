# Service Catalog

A local documentation server that catalogs ML services deployed on remote GPU servers.

## Querying Services

The catalog runs at `http://localhost:8090`. Query it to discover available services:

```bash
# List all services
curl http://localhost:8090/services

# Get details for a specific service (port, endpoints, description)
curl http://localhost:8090/services/graspgen_server

# Get API endpoints for a service
curl http://localhost:8090/services/graspgen_server/endpoints

# Check server health and connectivity
curl http://localhost:8090/health

# Force a rescan (auto-scans every 30s)
curl -X POST http://localhost:8090/rescan

# HTML documentation page
open http://localhost:8090/docs/html
```

## Using a Discovered Service

Once you find a service, construct its URL from the server IP and port:

```python
# From the catalog response:
# {"name": "graspgen_server", "port": 8006, "server_ip": "10.102.245.84"}

# Use the service:
url = "http://10.102.245.84:8006"
response = http.post_json(f"{url}/generate_grasps", {"point_cloud": points})
```

## Service Status

| Status | Meaning |
|--------|---------|
| `running` | Port is active on the server, service is responding |
| `stopped` | Service code exists but is not currently running |
| `unknown` | Could not determine port from source code |
| `server_down` | Remote server is unreachable |

## Health Alerts

If the remote server becomes unreachable, the catalog will:
- Mark all services as `server_down`
- Return `"server_reachable": false` in `/health`
- Print an alert after 3 consecutive failures
- Automatically recover when the server comes back

## Starting the Catalog

If not already running:

```bash
cd ~/tidybot_uni/Tidybot-Universe/service-server-setup
python3 service_scanner.py
```

First-time setup:

```bash
bash setup.sh --server-ip 10.102.245.84 --username exx --service-dir /home/exx/Projects/vlmanip_server
```
