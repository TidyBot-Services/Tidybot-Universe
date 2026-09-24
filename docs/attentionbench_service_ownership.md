# AttentionBench service ownership

| Component | Source of truth | Runtime boundary |
| --- | --- | --- |
| Robosuite simulator | [TidyBot-Services/robosuite_sim](https://github.com/TidyBot-Services/robosuite_sim), v2 Memory API commit `12bc69afe83c8398988be3eee637f91c14e9bf19` | Separate HTTP process exposing `/v1`; opt-in `/v2/perceive_gt` |
| AttentionHarness, shared SDK adapters, Memory Agent | This Universe repository | Harness / agent code, not standalone services |
| Memory Service v2 | [TidyBot-Services/attention_memory_service](https://github.com/TidyBot-Services/attention_memory_service), pinned at `c64f61ec7e35cf54d22052d536f7e094d119258f` | Authenticated HTTP daemon or in-process gateway; SQLite and memory artifacts are authoritative |

The Robosuite service is independently installable from its repository. For an
external process, use a dedicated environment with the service's pinned
dependencies, then pass its address to the Universe runner:

```bash
pip install 'git+https://github.com/TidyBot-Services/robosuite_sim.git@12bc69afe83c8398988be3eee637f91c14e9bf19'
python -I -m robosuite_sim --host 127.0.0.1 --port 8082
python -m benchmarks.attention_harness.external_robosuite_runner --service-url http://127.0.0.1:8082 \
  --task cube_lift --seed 101 --policy no-op
```

The recommended entry point is now
`python -m benchmarks.attention_harness.external_robosuite_runner`; its child
process uses isolated Python mode to load the pinned installed service rather
than the checkout snapshot. The original frozen v1 runner's default remains
unchanged. The in-tree `robosuite_sim` files are an immutable AttentionBench v1
snapshot covered by the freeze manifest. Keep
them until the v1 verification and historical replay contract is retired; do
not make two divergent copies of the v1 implementation. New service changes
belong in the service repository and should be consumed by explicit version or
commit pin.

Memory Service now owns its v2 implementation, storage decoder, artifact
package, HTTP API, and client in its independent repository. Universe retains
the frozen v1 store and thin v2 compatibility import paths. The RoboCasa v2
runner imports the external package. It uses the in-process gateway by default
or the authenticated HTTP daemon when `--memory-service-url` is provided.
Remote runs compare an opaque store ID before execution to prevent using a
daemon pointed at a different AttentionBench SQLite file. The package commit is pinned in
`benchmarks/attention_harness/setup_env.sh` so independent source changes do
not silently alter experiment semantics.
