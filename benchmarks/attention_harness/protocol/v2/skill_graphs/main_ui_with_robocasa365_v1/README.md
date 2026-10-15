# Primary UI catalog with RoboCasa365

This catalog preserves all 11 entries from the frozen bimanual stage-one daily catalog and appends the two reviewed RoboCasa365 Phase-1 development entries: `pick_place_counter_to_sink` and `open_cabinet`. Source catalog paths and SHA256 values are recorded in `catalog.json`. The original catalogs and their approved policies, configurations, models and Attention bindings remain unchanged.

The main launcher `benchmarks/attention_harness/run_ui.sh` selects this catalog by default; `ATTENTION_UI_CATALOG` still permits an explicit override. The standalone OpenArm launcher and earlier default catalog retain their existing selection behavior. When managing the primary server directly, pass this `catalog.json` using `--run-catalog` and retain the existing store, artifact root, port and camera arguments.

In **New run → Benchmark → RoboCasa365**, choose either task. Both retain their original `robocasa365_sim` source, pinned simulator environment, PandaOmron / HYBRID_MOBILE_BASE configuration and seed 101. Maximum development budgets remain 2 attempts, 1 assistance credit, 8,000 tokens, 30 seconds for a human reply, 120 seconds per attempt and 300 seconds for the whole case. These entries have no OpenArm/Franka selection or Attention switching binding.

Each new graph records one complete approved SDK policy as a task node, with its existing policy/configuration SHA256. It introduces no synthetic subskill execution. Trace, help, feedback, initial/final camera images and native evaluation continue through the existing 365 runner. Phase-1 replay contains saved images; this catalog does not add continuous video or establish task success, M6 acceptance or formal eligibility (`formal_eligible=false`). The independent 365 UI on port 8775 and its historical records are retained separately.

The existing local reviewed delivery under `/home/truares/Documents/Codex/2026-10-09/new-chat/outputs/robocasa365` is required. Missing files, changed hashes, a wrong simulator target or an unapproved robot choice are rejected by the existing launcher; no fallback is used.

Merge tests and preservation evidence: `outputs/robocasa365-main-ui-20261010/`. No real robot task is dispatched by these catalog/DOM tests.
