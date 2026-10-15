"""Display-only native RoboCasa camera; pinned observation/SDK streams stay intact.

Executed as a standalone wrapper around the approved maniskill_server entry point.
Rendering stays on its physics thread. HTTP readers only receive a cached JPEG.
"""
from __future__ import annotations

import io
import json
import os
import threading
import time
from collections import deque
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WIDTH, HEIGHT, FPS = 1920, 1080, 5
CAMERA_NAME = "attentionbench_human"


def configure_human_camera(env, camera_class):
    """Clone the base camera mount/frustum into the human registry only."""
    source = env._sensors["base_camera"]
    if source.config.intrinsic is not None:
        raise ValueError("human view requires the approved base camera FOV")
    config = replace(source.config, uid=CAMERA_NAME, width=WIDTH, height=HEIGHT,
                     mount=source.entity, entity_uid=None)
    camera = camera_class(config, env.scene)
    env._human_render_cameras = {CAMERA_NAME: camera}
    env.scene.human_render_cameras = env._human_render_cameras


class HumanCamera:
    def __init__(self):
        self.lock = threading.Lock()
        self.frame = None
        self.captured_at = None
        self.sequence = 0
        self.error = None
        self.next_capture = 0.0
        self.render_ms = deque(maxlen=300)
        self.policy_sizes = {}

    def invalidate(self):
        """A reconfigured scene cannot inherit a JPEG from its previous reset."""
        with self.lock:
            self.frame = None
            self.captured_at = None
            self.error = None
            self.policy_sizes = {}
            self.next_capture = 0.0

    def capture(self, env, *, now=None):
        now = time.monotonic() if now is None else now
        if now < self.next_capture:
            return
        self.next_capture = now + 1 / FPS
        started = time.monotonic()
        try:
            import numpy as np
            from PIL import Image
            env.scene.update_render(update_sensors=False, update_human_render_cameras=True)
            rgb = env.scene.get_human_render_camera_images(camera_name=CAMERA_NAME)[CAMERA_NAME]
            if hasattr(rgb, "cpu"):
                rgb = rgb.cpu().numpy()
            rgb = np.asarray(rgb)[0]
            if rgb.shape != (HEIGHT, WIDTH, 3) or rgb.dtype != np.uint8:
                raise ValueError("human render did not return native 1920x1080 uint8 RGB")
            # Timestamp the actual capture, never a later HTTP read.
            captured_at = time.time()
            output = io.BytesIO()
            Image.fromarray(rgb).save(output, format="JPEG", quality=90)
            sizes = {name: [sensor.config.width, sensor.config.height]
                     for name, sensor in env._sensors.items()}
            with self.lock:
                self.frame = output.getvalue()
                self.captured_at = captured_at
                self.sequence += 1
                self.error = None
                self.policy_sizes = sizes
                self.render_ms.append((time.monotonic() - started) * 1000)
        except Exception as error:
            message = f"{type(error).__name__}: {error}"[:500]
            with self.lock:
                previous = self.error
                self.error = message
            if message != previous:
                print(f"[human-camera] {message}", flush=True)

    def snapshot(self):
        with self.lock:
            state = {"width": WIDTH, "height": HEIGHT, "requested_fps": FPS,
                     "sequence": self.sequence, "captured_at": self.captured_at,
                     "error": self.error, "policy_camera_sizes": self.policy_sizes,
                     "render_ms": list(self.render_ms)}
            return state, self.frame

    def serve(self, port):
        camera = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                state, frame = camera.snapshot()
                if self.path == "/state":
                    body = json.dumps(state).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                elif self.path == "/frame" and frame is not None:
                    body = frame
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("X-Captured-At", str(state["captured_at"]))
                    self.send_header("X-Sequence", str(state["sequence"]))
                else:
                    body = b"Human camera unavailable"
                    self.send_response(503 if self.path == "/frame" else 404)
                    self.send_header("Content-Type", "text/plain")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True,
                                  name="human-camera-http")
        thread.start()
        return server, thread


def main():
    # Imports are deferred: this wrapper uses the pinned simulator's Python and
    # PYTHONPATH, with no edit to its checkout or installed ManiSkill package.
    from mani_skill.envs.sapien_env import BaseEnv
    from mani_skill.sensors.camera import Camera
    from maniskill_server.server import ManiskillServer
    from maniskill_server.__main__ import main as simulator_main
    camera = HumanCamera()
    setup = BaseEnv._setup_sensors
    update = ManiskillServer._update_state
    def setup_with_display(env, options):
        setup(env, options)
        configure_human_camera(env, Camera)
        camera.invalidate()
    def update_with_display(server, obs, env_idx=0):
        update(server, obs, env_idx=env_idx)
        if env_idx == 0:
            camera.capture(server.env.unwrapped)
    BaseEnv._setup_sensors = setup_with_display
    ManiskillServer._update_state = update_with_display
    http, thread = camera.serve(int(os.environ["ATTENTIONBENCH_HUMAN_CAMERA_PORT"]))
    try:
        simulator_main()
    finally:
        http.shutdown()
        http.server_close()
        thread.join(2)


if __name__ == "__main__":
    main()
