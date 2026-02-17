# Client SDK Specification

Every backend service MUST ship a `client.py` that skill agents can download and use directly.

## Requirements

1. **No external dependencies** — use only `urllib.request`, `urllib.error`, `json`, `base64` from the standard library
2. **Accept bytes input** — for image/file services, the primary method must accept `bytes` (not file paths)
3. **Include `health()` method** — returns `True` if the service is reachable, `False` otherwise
4. **Constructor takes `host`** — e.g., `Client(host="http://server:8000")`
5. **Type hints** — all public methods must have type hints
6. **Docstrings** — all public methods must have docstrings with usage examples

## Template

```python
"""<Service Name> Client SDK"""
import json
import urllib.request
import urllib.error
import base64


class Client:
    """Client for the <Service Name> service."""

    def __init__(self, host: str = "http://localhost:8000"):
        self.host = host.rstrip("/")

    def health(self) -> bool:
        """Check if the service is reachable."""
        try:
            req = urllib.request.Request(f"{self.host}/health")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def predict(self, image: bytes) -> dict:
        """
        Run prediction on an image.

        Args:
            image: Raw image bytes (JPEG or PNG)

        Returns:
            dict with prediction results

        Example:
            client = Client("http://server:8000")
            with open("image.jpg", "rb") as f:
                result = client.predict(f.read())
        """
        data = base64.b64encode(image).decode()
        payload = json.dumps({"image": data}).encode()
        req = urllib.request.Request(
            f"{self.host}/predict",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
```
