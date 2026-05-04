"""Poll a curobo_service /health endpoint until it returns 200.

Used by start scripts and CI: we don't want to fire requests at a service that
is still loading torch (~3s) before uvicorn even binds the port.
"""
import argparse
import json
import sys
import time
import urllib.request
import urllib.error


def wait_ready(url: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/health", timeout=1.5) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            last_err = e
            time.sleep(0.5)
    raise TimeoutError(f"{url} not ready after {timeout}s: {last_err}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:7000")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--warmup", action="store_true",
                    help="Also POST /warmup and wait for completion")
    args = ap.parse_args()

    info = wait_ready(args.url, args.timeout)
    print(f"[ready] {args.url} -> {json.dumps(info)}")

    if args.warmup:
        print(f"[warmup] POST {args.url}/warmup ...")
        t0 = time.time()
        req = urllib.request.Request(
            args.url + "/warmup",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            out = json.loads(resp.read())
        print(f"[warmup] {time.time()-t0:.1f}s -> {json.dumps(out)}")


if __name__ == "__main__":
    sys.exit(main())
