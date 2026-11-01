"""Private service entry. Invoke with the locked interpreter and -I."""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time

# -I discards environment/PYTHONPATH and cwd. Only this explicit checkout is added.
ROOT = Path(__file__).resolve().parents[1]
if not sys.dont_write_bytecode or any(ROOT.rglob('*.pyc')) or any(ROOT.rglob('*.pyo')):
    raise RuntimeError('Private service requires -B and a source tree without bytecode caches')
sys.path.insert(0, str(ROOT))
from curobo_v2.identity import (IdentityError, digest, install_import_guard, load_lock,
                                sign, verify_files, verify_runtime, authorize_service_import)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", required=True)
    parser.add_argument("--robot-config", required=True)
    parser.add_argument("--socket-fd", required=True, type=int)
    parser.add_argument("--evidence", required=True)
    parser.add_argument('--test-response-fault',choices=['none','http500_after_success','hold_after_success'],default='none')
    parser.add_argument('--test-gpu-activity',choices=['disabled','enabled'],default='disabled')
    parser.add_argument("--tier")
    parser.add_argument("--boundary")
    parser.add_argument("--trust-registry")
    parser.add_argument("--trust-registry-sha256")
    args = parser.parse_args()
    owner_pid = int(os.environ.pop("CUROBO_V2_OWNER_PID", "0"))
    # The planner owns no physical state. Kill this private worker if its owner
    # dies, including a hard-killed simulator, to avoid an orphan GPU service.
    libc = ctypes.CDLL(None, use_errno=True)
    if not sys.platform.startswith("linux") or libc.prctl(1, 9, 0, 0, 0) != 0 or os.getppid() != owner_pid:
        raise IdentityError("Cannot bind cuRoboV2 worker lifetime to its owner process")
    if args.trust_registry is not None or args.trust_registry_sha256 is not None:
        from curobo_v2 import identity as trust
        registry = Path(args.trust_registry or '')
        if not registry.is_absolute() or digest(registry) != args.trust_registry_sha256:
            raise IdentityError('Owner deployment registry digest mismatch')
        trust.DEPLOYMENT_REGISTRY = registry
        trust._registry_digest = args.trust_registry_sha256
    from curobo_v2.dev_local import require_local_workspace
    require_local_workspace(json.loads(Path(args.lock).read_bytes()))
    lock, lock_sha = load_lock(args.lock, tier=args.tier, boundary=args.boundary)
    runtime = verify_runtime(lock)
    import curobo._src.runtime as gpu_runtime
    gpu_runtime.kernel_backend = "cuda_core"
    gpu_runtime.cuda_core_backend = True
    gpu_runtime.cache_dir = str(Path(args.evidence) / "runtime_cache")
    from curobo._src.curobolib.backends import get_backend_name
    if get_backend_name() != "cuda_core":
        raise IdentityError("Only the declared V2 cuda.core backend may run")
    install_import_guard()
    robot_config = str(Path(args.robot_config).resolve())
    if lock["robot_configs"].get(robot_config) != digest(Path(robot_config)):
        raise IdentityError("Robot configuration is not locked")
    token = os.environ.pop("CUROBO_V2_INSTANCE_TOKEN", "")
    nonce = os.environ.pop("CUROBO_V2_INSTANCE_NONCE", "")
    if len(token) != 64 or len(nonce) != 32:
        raise IdentityError("Owned service token/nonce is missing")
    # Config is required, never inherited from CUROBO_* environment defaults.
    for key in list(os.environ):
        if key.startswith("CUROBO_"):
            del os.environ[key]
    os.environ.update(CUROBO_ROBOT_CFG=robot_config, CUROBO_ASSETS_DIR=lock["assets_root"],
                      CUROBO_USE_CUDA_GRAPH="1", CUROBO_DEVICE="cuda:0")
    authorize_service_import(lock, robot_config)
    directory = Path(args.evidence)
    directory.mkdir(parents=True, exist_ok=True)
    from curobo_v2.sweep import configure_evidence
    configure_evidence(directory)
    sys.path.insert(0, lock["service_root"])
    from curobo_service.server import app
    from fastapi import Request
    from fastapi.responses import JSONResponse
    import uvicorn
    identity = {"runtime": runtime, "lock_sha256": lock_sha, "nonce": nonce,
                "pid": os.getpid(), "service_root": lock["service_root"],
                "robot_config": robot_config, "robot_config_sha256": digest(Path(robot_config)),
                "kernel_backend": "cuda_core",'test_response_fault':args.test_response_fault}
    directory = Path(args.evidence)
    directory.mkdir(parents=True, exist_ok=True)
    identity['test_gpu_activity']=args.test_gpu_activity=='enabled'
    if identity['test_gpu_activity']:
        from curobo_v2.gpu_activity import configure
        configure(directory)
    (directory / "loaded_identity.json").write_text(json.dumps(identity, indent=2) + "\n")
    failed = None
    test_fault_used = False

    @app.middleware("http")
    async def authenticated(request: Request, call_next):
        nonlocal failed,test_fault_used
        if (request.headers.get("authorization") != "Bearer " + token
                or request.headers.get("x-curobo-instance") != nonce):
            return JSONResponse(status_code=403, content={"error": "cuRoboV2 instance authentication required"})
        try:
            if failed:
                raise IdentityError(failed)
            verify_files(lock)
            verify_runtime(lock, full=False)
            if (gpu_runtime.kernel_backend != "cuda_core" or not gpu_runtime.cuda_core_backend
                    or get_backend_name() != "cuda_core"):
                raise IdentityError("V2 GPU backend identity changed; no fallback permitted")
            response = await call_next(request)
            # Explicit, signed, default-disabled service transport fault test.
            # The official solver and adapter have already produced the actual
            # result. Never synthesize a solver result or release a failed path.
            if (not test_fault_used and args.test_response_fault!='none'
                    and request.url.path=='/plan/joint'and response.status_code==200):
                body=b''.join([chunk async for chunk in response.body_iterator])
                result=json.loads(body)
                if result.get('status')=='success'and result.get('trajectory')is not None:
                    test_fault_used=True
                    record=dict(test_response_fault=args.test_response_fault,nonce=nonce,
                        time_ns=time.time_ns(),official_response_sha256=hashlib.sha256(body).hexdigest(),
                        official_response_raw=body.decode(),official_response=result,
                        scope='Genuine accepted official V2 result; diagnostic evidence only, no dispatch capability issued')
                    (directory/'test_response_result.json').write_text(json.dumps(record,indent=2)+'\n')
                    if args.test_response_fault=='hold_after_success':
                        until=time.monotonic()+2.
                        release=directory/'test_release_response'
                        while time.monotonic()<until:
                            if release.is_file()and release.read_text()==nonce:break
                            await asyncio.sleep(.005)
                        else:raise RuntimeError('Explicit service response test gate exceeded its two-second bound')
                        response=JSONResponse(status_code=200,content=result)
                    else:
                        response=JSONResponse(status_code=500,content=dict(status='failed',trajectory=None,
                            reason='controlled_service_response_fault',test_response_fault=args.test_response_fault))
                else:response=JSONResponse(status_code=response.status_code,content=result)
            if response.status_code >= 500:
                failed = f"Service exception HTTP {response.status_code}; this instance is latched and cannot yield a usable trajectory"
            response.headers["x-curobo-instance"] = nonce
            response.headers["x-curobo-lock"] = lock_sha
            return response
        except Exception as exc:
            failed = f"{type(exc).__name__}: {exc}"
            return JSONResponse(status_code=503, content={"error": failed})

    @app.get("/identity")
    def loaded_identity(challenge: str):
        if not 16 <= len(challenge) <= 128:
            return JSONResponse(status_code=422, content={"error": "invalid identity challenge"})
        return {"identity": identity, "signature": sign(token, identity, challenge)}

    class EvidenceApp:
        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await app(scope, receive, send)
            request_body, response_body, status = [], [], None
            started = time.monotonic()
            async def incoming():
                message = await receive()
                if message["type"] == "http.request":
                    request_body.append(message.get("body", b""))
                return message
            async def outgoing(message):
                nonlocal status
                if message["type"] == "http.response.start":
                    status = message["status"]
                if message["type"] == "http.response.body":
                    response_body.append(message.get("body", b""))
                await send(message)
            try:
                await app(scope, incoming, outgoing)
            finally:
                modules = {name:str(Path(module.__file__).resolve()) for name,module in list(sys.modules.items())
                           if (name=='curobo' or name.startswith('curobo.')) and getattr(module,'__file__',None)}
                (directory/'loaded_modules.json').write_text(json.dumps(modules,indent=2)+'\n')
                (directory/'native-maps.txt').write_text(Path('/proc/self/maps').read_text())
                # Headers contain credentials and are intentionally never recorded.
                with (directory / "planner_http.jsonl").open("a") as stream:
                    stream.write(json.dumps({"time_ns": time.time_ns(), "elapsed_seconds": time.monotonic()-started, "path": scope["path"],
                        "method": scope["method"], "status": status, "nonce": nonce,
                        "lock_sha256": lock_sha, "request": b"".join(request_body).decode(errors="replace"),
                        "response": b"".join(response_body).decode(errors="replace"),
                        **({'execution_settings_sha256': json.loads(b''.join(response_body)).get('execution_settings_sha256', 'legacy')}
                           if scope['path'].startswith('/plan') and response_body else {})}) + "\n")

    listener = socket.socket(fileno=args.socket_fd)
    config = uvicorn.Config(EvidenceApp(), host="127.0.0.1", log_level="info")
    uvicorn.Server(config).run(sockets=[listener])


if __name__ == "__main__":
    main()
