"""Loopback JSON Service. Evaluator and lifecycle endpoints require owner token."""

import argparse, json, secrets, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .backend import OfficialBackend


def serve(backend, owner_token, public_token, host="127.0.0.1", port=0):
    lock = threading.RLock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.handle_request(False)

        def do_POST(self):
            self.handle_request(True)

        def handle_request(self, post):
            owner = self.path in (
                "/reset",
                "/native_success",
                "/stop",
                "/shutdown",
                "/journal",
            )
            token = owner_token if owner else public_token
            if not secrets.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + token
            ):
                return self.reply(403, {"error": "permission denied"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size > 2_000_000:
                    raise ValueError("request too large")
                data = json.loads(self.rfile.read(size)) if size else {}
                with lock:
                    if self.path == "/health":
                        result = {
                            "ok": True,
                            "service": "robocasa365_sim",
                            "formal_eligible": False,
                            "identity": backend.identity,
                        }
                    elif self.path == "/capabilities":
                        result = backend.capabilities()
                    elif self.path == "/reset" and post:
                        result = backend.reset(data["config"])
                    elif self.path == "/observation":
                        result = backend.observation()
                    elif self.path == "/step" and post:
                        result = backend.step(**data)
                    elif self.path == "/native_success":
                        result = backend.native_success()
                    elif self.path == "/stop" and post:
                        result = backend.stop()
                    elif self.path == "/shutdown" and post:
                        result = backend.stop()
                        threading.Thread(
                            target=self.server.shutdown, daemon=True
                        ).start()
                    else:
                        return self.reply(404, {"error": "unknown endpoint"})
                self.reply(200, result)
            except ValueError as exc:
                self.reply(400, {"error": str(exc)})
            except Exception as exc:
                self.reply(409, {"error": type(exc).__name__ + ": " + str(exc)})

        def reply(self, code, value):
            raw = json.dumps(value, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer((host, port), Handler)
    return server


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--robocasa-root", required=True)
    p.add_argument("--robosuite-root", required=True)
    p.add_argument("--asset-manifest", required=True)
    p.add_argument("--evidence-dir", required=True)
    p.add_argument("--token-file", required=True)
    p.add_argument("--ready-file", required=True)
    p.add_argument("--port", type=int, default=0)
    a = p.parse_args()
    from pathlib import Path

    tokens = json.loads(Path(a.token_file).read_text())
    backend = OfficialBackend(
        roots={"robocasa": a.robocasa_root, "robosuite": a.robosuite_root},
        asset_manifest=a.asset_manifest,
        evidence_dir=a.evidence_dir,
    )
    s = serve(backend, tokens["owner"], tokens["public"], port=a.port)
    if backend.recorder is not None:
        backend.recorder.context['origin'] = 'http://127.0.0.1:' + str(s.server_port)
    ready = Path(a.ready_file)
    ready.write_text(
        json.dumps(
            {
                "recording_supported": True,
                "port": s.server_port,
                "pid": __import__("os").getpid(),
                "identity": backend.identity,
            }
        )
    )
    try:
        s.serve_forever(poll_interval=0.1)
    finally:
        backend.stop()
        s.server_close()


if __name__ == "__main__":
    main()
