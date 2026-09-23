# TidyBot Robosuite simulator service

This package is the Robosuite peer of `robocasa_sim`. It owns the official
Robosuite environment, task/controller lifecycle, reset/seed behavior,
camera/proprioception filtering, native success evaluator, and server-side
reference diagnostics.

```bash
python -m robosuite_sim --host 127.0.0.1 --port 8082
```

Control API:

- `GET /health`
- `GET /v1/capabilities`
- `POST /v1/reset`
- `POST /v1/step`
- `GET /v1/observation`
- `GET /v1/success`
- `GET /v1/metadata`
- `POST /v1/reference`
- `POST /v1/close`

The service does not contain an agent, benchmark protocol, model client, or
artifact writer. Those remain in AttentionHarness. Privileged object state and
the native evaluator never appear in policy observations.

Public camera observations use flat array keys:

- `<camera>_image`: uint8 RGB with OpenCV top-left origin
- `<camera>_depth`: metric depth in meters with the same top-left origin
- `<camera>_intrinsics`: 3x3 pinhole matrix
- `<camera>_pose_mat`: 4x4 OpenCV-camera-to-Robosuite-world transform

The capability document declares `object_oracle_visible=false`. Simulator
segmentation IDs, object poses, rewards, and evaluator internals are not
perception services and must not be exposed through the SDK.
