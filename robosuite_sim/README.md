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
