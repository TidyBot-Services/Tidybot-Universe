#!/usr/bin/env bash
# sync_catalog.sh — Pull latest catalog.json and download new service clients.
# Intended to run via cron (e.g. every 2 minutes).
#
# Usage:
#   ./sync_catalog.sh                          # uses defaults
#   WISHLIST_DIR=/path/to/wishlist ./sync_catalog.sh
#   SERVICES_DIR=/path/to/service_clients ./sync_catalog.sh

set -euo pipefail

# --- Configuration (override via environment) ---
WISHLIST_DIR="${WISHLIST_DIR:-$HOME/tidybot_army/backend_wishlist}"
CATALOG="$WISHLIST_DIR/catalog.json"
SERVICES_DIR="${SERVICES_DIR:-$HOME/tidybot_army/tidybot-agent-server/service_clients}"
LOG="${SYNC_CATALOG_LOG:-$HOME/tidybot_army/sync_catalog.log}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# --- Pull latest catalog ---
cd "$WISHLIST_DIR"
if ! git pull --ff-only origin main >> "$LOG" 2>&1; then
    log "ERROR: git pull failed"
    exit 1
fi

if [ ! -f "$CATALOG" ]; then
    log "ERROR: catalog.json not found"
    exit 1
fi

# --- Parse capabilities and download new clients ---
python3 - "$CATALOG" "$SERVICES_DIR" >> "$LOG" 2>&1 <<'PYEOF'
import json, sys, os, hashlib, urllib.request, pathlib

catalog_path = sys.argv[1]
services_dir = sys.argv[2]

with open(catalog_path) as f:
    catalog = json.load(f)

capabilities = catalog.get("capabilities", {})
if not capabilities:
    print("No capabilities in catalog.")
    sys.exit(0)

def file_hash(path):
    """SHA-256 hash of a local file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()

for name, info in capabilities.items():
    client_url = info.get("client_sdk")
    if not client_url:
        print(f"[{name}] No client_sdk URL, skipping.")
        continue

    # Normalize name: yolo-detection -> yolo_detection
    safe_name = name.replace("-", "_")
    dest_dir = os.path.join(services_dir, safe_name)
    dest_file = os.path.join(dest_dir, "client.py")

    try:
        # Download into memory
        with urllib.request.urlopen(client_url, timeout=15) as resp:
            remote_bytes = resp.read()
    except Exception as e:
        print(f"[{name}] ERROR downloading client: {e}")
        continue

    remote_hash = hashlib.sha256(remote_bytes).hexdigest()

    # Compare with local file if it exists
    if os.path.exists(dest_file):
        local_hash = file_hash(dest_file)
        if local_hash == remote_hash:
            continue  # unchanged
        print(f"[{name}] UPDATED client.py detected (hash changed). Replacing ...")
    else:
        print(f"[{name}] NEW service detected. Downloading client.py ...")

    os.makedirs(dest_dir, exist_ok=True)

    # Ensure __init__.py exists so directory is a valid Python package
    init_path = os.path.join(dest_dir, "__init__.py")
    if not os.path.exists(init_path):
        pathlib.Path(init_path).touch()

    try:
        with open(dest_file, "wb") as f:
            f.write(remote_bytes)
        print(f"[{name}] Saved to {dest_file}")

        # Write a metadata file with service info
        meta = {
            "name": name,
            "host": info.get("host", ""),
            "description": info.get("description", ""),
            "endpoints": info.get("endpoints", []),
            "version": info.get("version", ""),
            "service_repo": info.get("service_repo", ""),
            "api_docs": info.get("api_docs", ""),
        }
        meta_path = os.path.join(dest_dir, "service.json")
        with open(meta_path, "w") as mf:
            json.dump(meta, mf, indent=2)
        print(f"[{name}] Wrote metadata to {meta_path}")

    except Exception as e:
        print(f"[{name}] ERROR writing client: {e}")

PYEOF

log "Sync complete."
