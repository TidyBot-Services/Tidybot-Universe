"""OpenClaw Gateway WebSocket probe — PARTIAL (handshake blocked).

Status: captures connect.challenge + constructs a device-signed connect frame,
but the server closes with 1008 "invalid request frame" before responding.
Root cause: some subtle mismatch between our Ed25519 signing and the server's
expected v3 payload (likely a detail of `buildDeviceAuthPayloadV3` we haven't
replicated exactly — scopes ordering, token field, or platform normalization).

The official CLI (`openclaw sessions --all-agents`) performs the same handshake
successfully against this gateway, so the gateway is healthy and our issue is
purely in Python-side crypto replication.

This probe is committed as partial research — it demonstrates:
  1. The handshake flow: challenge (server) → connect (signed, client) → hello-ok
  2. The exact payload format (v3|deviceId|clientId|clientMode|role|scopes|ts|token|nonce|platform|family)
  3. Where a future Python client would need to debug (byte-compare with CLI's real frame via `openclaw proxy run`)

Key schemas confirmed from the npm package's .d.ts files:
  - SessionsSendParamsSchema: {key, message, thinking?, attachments?, timeoutMs?, idempotencyKey?}
  - SessionsMessagesSubscribeParamsSchema: {key}
  - SessionsAbortParamsSchema: {key, runId?}
  - Event types: "session.message" (role, content[{type, text|toolCall, ...}]), "session.tool"

Alternative to finishing this: spawn `openclaw agent --local` CLI as subprocess
(Path A — already proven by full_pipeline.py) and skip WS entirely.
"""
import argparse
import asyncio
import json
import sys
import time
import uuid

import websockets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
import base64

GATEWAY = "ws://127.0.0.1:18789"
SESSION_KEY = "agent:tidybot-poc:main"
DUMP_FILE = "/tmp/ws_events.ndjson"


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def load_identity():
    d = json.load(open("/home/truares/.openclaw/identity/device.json"))
    a = json.load(open("/home/truares/.openclaw/identity/device-auth.json"))
    priv = serialization.load_pem_private_key(d["privateKeyPem"].encode(), password=None)
    pub_pem = serialization.load_pem_public_key(d["publicKeyPem"].encode())
    pub_raw = pub_pem.public_bytes(encoding=serialization.Encoding.Raw,
                                   format=serialization.PublicFormat.Raw)
    return {
        "deviceId": d["deviceId"],
        "privateKey": priv,
        "publicKeyB64": b64url(pub_raw),
        "deviceToken": a["tokens"]["operator"]["token"],
        "scopes": a["tokens"]["operator"]["scopes"],
    }


def build_device_block(ident: dict, nonce: str, client_id: str, client_mode: str,
                        role: str, scopes: list[str], platform: str,
                        device_family: str = ""):
    """Mirror of openclaw's buildDeviceAuthPayloadV3 + signDevicePayload."""
    signed_at = int(time.time() * 1000)
    token = ident["deviceToken"]  # signatureToken = the device token we're proving
    payload = "|".join([
        "v3", ident["deviceId"], client_id, client_mode, role,
        ",".join(scopes), str(signed_at), token, nonce,
        platform.strip().lower(), device_family.strip().lower(),
    ])
    sig = ident["privateKey"].sign(payload.encode("utf-8"))
    return {
        "id": ident["deviceId"],
        "publicKey": ident["publicKeyB64"],
        "signature": b64url(sig),
        "signedAt": signed_at,
        "nonce": nonce,
    }


def rid() -> str:
    return uuid.uuid4().hex[:12]


def dump(frame: dict, tag: str = ""):
    frame["_client_tag"] = tag
    frame["_client_ts"] = time.time()
    with open(DUMP_FILE, "a") as f:
        f.write(json.dumps(frame, ensure_ascii=False) + "\n")


class WsClient:
    def __init__(self, ws):
        self.ws = ws
        self.pending: dict[str, asyncio.Future] = {}
        self.event_q: asyncio.Queue = asyncio.Queue()
        self.reader_task = asyncio.create_task(self._read_loop())
        self.alive = True

    async def _read_loop(self):
        try:
            async for raw in self.ws:
                try:
                    frame = json.loads(raw)
                except Exception:
                    continue
                dump(frame, tag="recv")
                ft = frame.get("type")
                if ft == "res":
                    fid = frame.get("id")
                    fut = self.pending.pop(fid, None)
                    if fut and not fut.done():
                        fut.set_result(frame)
                else:
                    await self.event_q.put(frame)
        except Exception as e:
            dump({"type": "_reader_error", "error": str(e)}, tag="reader")
        finally:
            self.alive = False
            for f in self.pending.values():
                if not f.done(): f.set_exception(ConnectionError("closed"))

    async def req(self, method: str, params: dict, timeout: float = 15.0) -> dict:
        req_id = rid()
        fut = asyncio.get_event_loop().create_future()
        self.pending[req_id] = fut
        frame = {"type": "req", "id": req_id, "method": method, "params": params}
        dump(frame, tag="send")
        await self.ws.send(json.dumps(frame))
        return await asyncio.wait_for(fut, timeout=timeout)

    async def fire(self, method: str, params: dict):
        """Fire-and-forget request (don't wait for response)."""
        req_id = rid()
        frame = {"type": "req", "id": req_id, "method": method, "params": params}
        dump(frame, tag="send-fire")
        await self.ws.send(json.dumps(frame))
        return req_id


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default="Reply with exactly: ws-probe-ok")
    ap.add_argument("--steer-after", type=float, default=None)
    ap.add_argument("--steer-prompt", default="Actually just say STEERED and stop.")
    ap.add_argument("--listen-seconds", type=float, default=90.0)
    args = ap.parse_args()

    open(DUMP_FILE, "w").close()
    ident = load_identity()
    print(f"[probe] loaded device identity id={ident['deviceId'][:12]}... scopes={ident['scopes']}")
    print(f"[probe] connecting to {GATEWAY}")

    CLIENT_ID = "cli"
    CLIENT_MODE = "cli"
    PLATFORM = "linux"
    ROLE = "operator"
    # Must match stored scopes exactly (server rejects if signed scopes differ from stored)
    SCOPES = ident["scopes"]

    async with websockets.connect(GATEWAY, max_size=25*1024*1024) as ws:
        c = WsClient(ws)

        # 1a. Wait for connect.challenge event (server sends this first)
        print("[probe] waiting for connect.challenge...")
        nonce = None
        while True:
            frame = await asyncio.wait_for(c.event_q.get(), timeout=10.0)
            if frame.get("event") == "connect.challenge":
                nonce = frame.get("payload", {}).get("nonce")
                print(f"[probe] ← connect.challenge nonce={nonce[:12]}...")
                break

        # 1b. Build signed device block and send connect
        device_block = build_device_block(
            ident, nonce, CLIENT_ID, CLIENT_MODE, ROLE, SCOPES, PLATFORM,
        )
        print("[probe] → connect (signed)")
        r = await c.req("connect", {
            "minProtocol": 3, "maxProtocol": 3,
            "client": {
                "id": CLIENT_ID, "version": "0.0.1",
                "platform": PLATFORM, "mode": CLIENT_MODE,
            },
            "caps": ["tool-events"],
            "role": ROLE,
            "scopes": SCOPES,
            "auth": {"deviceToken": ident["deviceToken"]},
            "device": device_block,
        })
        print(f"[probe] ← connect.ok={r.get('ok')}  err={r.get('error')}")
        if not r.get("ok"):
            return 1

        # 2. subscribe to events BEFORE sending
        print(f"[probe] → sessions.messages.subscribe key={SESSION_KEY}")
        r = await c.req("sessions.messages.subscribe", {"key": SESSION_KEY})
        print(f"[probe] ← subscribe.ok={r.get('ok')}  err={r.get('error')}  payload={str(r.get('payload'))[:200]}")

        # 3. send prompt (fire-and-forget — response comes later, we don't block)
        print(f"[probe] → sessions.send key={SESSION_KEY} msg={args.prompt[:60]}")
        await c.fire("sessions.send", {"key": SESSION_KEY, "message": args.prompt})

        # 4. event-stream loop
        t0 = time.time()
        steered = False
        evt_counts: dict[str, int] = {}
        last_evt_at = time.time()
        while time.time() - t0 < args.listen_seconds and c.alive:
            try:
                frame = await asyncio.wait_for(c.event_q.get(), timeout=2.0)
            except asyncio.TimeoutError:
                if time.time() - last_evt_at > 25:
                    print(f"[probe] {int(time.time()-t0)}s elapsed, no events for 25s — calling it done")
                    break
                continue
            last_evt_at = time.time()
            et = frame.get("event") or frame.get("type") or "?"
            evt_counts[et] = evt_counts.get(et, 0) + 1
            preview = str(frame.get("payload", frame))[:100].replace("\n", " ")
            print(f"  [t+{time.time()-t0:5.1f}s] {et:35s} #{evt_counts[et]:2d}  {preview}")

            if args.steer_after and not steered and (time.time() - t0) > args.steer_after:
                print(f"[probe] → sessions.steer (steer-after={args.steer_after}s)")
                try:
                    sr = await c.req("sessions.steer",
                                     {"key": SESSION_KEY, "message": args.steer_prompt},
                                     timeout=5.0)
                    print(f"[probe] ← steer.ok={sr.get('ok')} err={sr.get('error')}")
                except Exception as e:
                    print(f"[probe] steer raised: {e}")
                steered = True

        print(f"\n[probe] event counts: {evt_counts}")
        total = sum(1 for _ in open(DUMP_FILE))
        print(f"[probe] dumped {total} frames → {DUMP_FILE}")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
