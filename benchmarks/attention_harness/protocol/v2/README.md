# AttentionBench v2: perception tracks

This is a new experiment definition, not an amendment to the frozen v1
Robosuite non-oracle result. The v1 freeze manifest and its evidence remain
unchanged.

`sim_gt` means that a simulator may supply object identity and position,
including through RoboCasa `/perceive`. It is the **GT-perception
AttentionBench** track: the experimental question is when to request help and
how to use trace and memory, not visual recognition. `vision` means an RGB-D
detector and calibrated coordinate conversion. Real hardware is `vision` only.

The mode is required at run creation, never changes during the run, and cannot
fall back to `sim_gt` when vision fails. Both modes expose the same
`sensors.find_objects()` object shape; positions are in the backend's declared
control frame. The `source` field, run artifact, SDK event trace, and memory
applicability retain provenance. Native success and evaluator debug are
evaluation-only. Neither the legacy v1 non-oracle score nor real-robot vision
success rates may be pooled with the v2 simulator GT score.

The RoboCasa GT track is not frozen or held-out eligible yet. First complete
the two-task, five-development-seed chain check; then meet 25/25 native
successes per task on the frozen development split. Privileged teleport
probes do not count as policy successes.
