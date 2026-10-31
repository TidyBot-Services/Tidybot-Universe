# Primary daily UI: RoboCasa365 up to 10 attempts

In **New run → Benchmark → RoboCasa365**, both existing tasks now allow 1–10 attempts, defaulting to 10. The UI receives `run_limits` from the approved profile; backend admission, CLI and scheduler independently enforce the same maximum. An eleventh attempt is rejected before dispatch.

The new `run_budget.json` is bound by SHA256 to the two existing simulator configurations and their approved SDK policy. It is copied into each CLI entry snapshot and its digest is recorded in the launch, entry lock and scheduler configuration. The simulator configurations, independent service environment and physics/safety limits are unchanged. Without the new manifest, legacy Phase-1 entries continue to permit at most 2 attempts.

Other limits remain: 1 assistance credit, 8,000 tokens, 30 seconds per human reply, 120 seconds per attempt and 300 seconds for the whole case. Ten is an upper bound: success, stopping, time or resource limits can end the run earlier. This update does not grant additional help or extend the total deadline.

Random escalation has a separate approved preregistration for each selected maximum from 1 to 10 (`total_failure_slots = max_attempts - 1`). It registers one potential request when slots exist, and zero when maximum attempts is 1. The existing retry threshold remains `k=1`. Chosen preregistration bytes and digest are rechecked before launching; no plan is changed after observing results.

The original 11 task/robot choices are retained. Seven OpenArm configurations are independently cloned to bind the current shared source bytes; their model, controller, parameters, task, camera, runtime, policy and safety fields are identical to their earlier versions. Their DAG roots bind the cloned configuration hashes. Eleven new Attention bindings preserve the same allowed policies and handover rules while attesting current code. Earlier approved configurations, DAGs and bindings remain unchanged and still reject source drift.

The mutable daily pointer `openarm_bimanual/current.json` selects this catalog and retains its previous pointer as provenance. The generic main launcher again delegates to this existing resolver, preserving explicit `ATTENTION_UI_CATALOG` overrides and avoiding a second default catalog selection. The primary managed service also uses this catalog. Historical runs and their source identities are never rewritten.

This is an engineering budget update (`formal_eligible=false`). The CLI's ten-attempt test uses an explicit isolated runner fixture reporting native failure; it does not claim ten physical task executions or task success. Real simulator/service behavior is unchanged. Evidence, red/green logs, preservation audit and actual browser checks are under `outputs/robocasa365-attempt10-20261010/`.
