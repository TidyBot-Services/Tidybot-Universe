---
name: tidybot-active-services
description: External services catalog — model endpoints, vision APIs, grasping backends, and Python client SDKs available to robot skills. Use when (1) a skill needs an external model or API, (2) checking what backend services are available, (3) debugging service timeouts or connection failures, (4) requesting a new service that doesn't exist yet, (5) downloading or using a service's Python client SDK.
---

# Active Services

## Catalog

Fetch the live catalog:
```
https://raw.githubusercontent.com/TidyBot-Services/services_wishlist/main/catalog.json
```

Each entry contains:
- `host` — HTTP endpoint
- `client_sdk` — Python client file URL (download and include in skill code)
- `api_docs` — API documentation

## Using a Service

1. Download `client_sdk` from the catalog entry.
2. Include it in skill code.
3. Call the service via `host` URL from robot sandbox.

## Requesting New Services

1. Clone: `git clone https://github.com/TidyBot-Services/services_wishlist.git ./services_wishlist`
2. Read `services_wishlist/RULES.md`.
3. Add request to `services_wishlist/wishlist.json`.

Wishlist: `https://raw.githubusercontent.com/TidyBot-Services/services_wishlist/main/wishlist.json`

## Known Services

- **Visuomotor keypoint joint policy:** `10.100.129.103:8500` — diffusion policy for arm control (intermittent — verify availability first)
