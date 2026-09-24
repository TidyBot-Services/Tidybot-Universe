# AttentionBench service ownership

| Component | Source of truth | Runtime boundary |
| --- | --- | --- |
| Robosuite simulator | [TidyBot-Services/robosuite_sim](https://github.com/TidyBot-Services/robosuite_sim), initial commit `fbc547489b65d0b3a8fd4e870f4c9b5761738cf1` | Separate HTTP process exposing `/v1` |
| AttentionHarness, shared SDK adapters, Memory Agent | This Universe repository | Harness / agent code, not standalone services |
| Memory Service v2 | This Universe repository, `benchmarks/attention_harness/memory_service_api.py` and related `memory_v2` code | Authenticated HTTP daemon or in-process client; SQLite and memory artifacts are authoritative |

The Robosuite service is independently installable from its repository. For an
external process, use a dedicated environment with the service's pinned
dependencies, then pass its address to the Universe runner:

```bash
pip install 'git+https://github.com/TidyBot-Services/robosuite_sim.git@fbc547489b65d0b3a8fd4e870f4c9b5761738cf1'
python -m robosuite_sim --host 127.0.0.1 --port 8082
python -m benchmarks.attention_harness.runner --service-url http://127.0.0.1:8082 \
  --task cube_lift --seed 101 --policy no-op
```

The default managed-process path still uses the in-tree package. The in-tree `robosuite_sim` files are an
immutable AttentionBench v1 snapshot covered by the freeze manifest. Keep
them until the v1 verification and historical replay contract is retired; do
not make two divergent copies of the v1 implementation. New service changes
belong in the service repository and should be consumed by explicit version or
commit pin.

Memory Service already has a process and API boundary but **not** independent
source ownership: it imports the v2 manager and shared AttentionBench core in
this repository. A separate `attention_memory_service` repository should be
created only after that dependency boundary is made installable and versioned,
with the same v2 tests and artifact migration checks passing against the
external package. Copying the daemon alone would create a second, untested
authority over SQLite and memory promotion. Until extraction, Universe is the
single source of truth for Memory Service.
