# v2.1 formal base-policy candidates — awaiting operator review

This is an approval package, not an approval or a qualifying run. The current
checkout is `feature/attention-native-robosuite` at
`fbf0887513d55177ca7ba1ff4dd5cf067baf7e1e`. The committed v2.1 admission
protocol is `formal_admission_v2_1_2026-09-29.json`, SHA-256
`d2b5a68d7943b6cdf382dcc0217d99d72ff3d00ccb56f9a729d66ee42f9c0124`.
The pre-existing untracked advisor UI document and image were left untouched.

| Candidate | Per-file exact SHA-256 | Index file SHA-256 |
| --- | --- | --- |
| Robosuite `cube_lift` | [`robosuite_cube_lift/sha256_index.json`](robosuite_cube_lift/sha256_index.json) | `fa3b3f1797dc1638c3716ef7c34968823e8f0ab6fef6c03e6dfaab1499e66ad5` |
| RoboCasa `counter_to_sink` | [`robocasa_counter_to_sink/sha256_index.json`](robocasa_counter_to_sink/sha256_index.json) | `f8a6bf157ebe1dda7f579c20d77e21bc320eb0d0b6639383d86abd0f8c91f6e0` |

Each directory contains the proposed policy source, a byte-fixed seed-101
simulator config, an honest manually authored generation receipt with zero model
and simulator calls, the exact M1 entry lock returned by the production
preflight, allowed observations/actions and budget in `review.json`, a static
validation report, and a SHA-256 index covering every payload file. The index
SHA-256 values above identify the indexes themselves. `m1_entry_lock.json`
contains both the M1 canonical identity digest and bytes with their own SHA-256;
these are different hashes.

The older short generated RoboCasa candidate targets `yogurt` and has no sink
placement, while its old config points to obsolete Service revisions. It is
structurally unsuitable for the present task. These new candidates were
authored from the committed protocol, task source, and public SDK contracts.
No historical native-success result was used to select or edit either policy.
The Robosuite candidate has a simple cube grasp and step-bounded lift; its grasp
geometry and reachability may fail. The RoboCasa candidate maps public
instruction words to an SDK object name, then uses a public `spout` landmark
and bounded arm/base commands. Its vocabulary is narrow, the spout is an
imperfect basin proxy, and the path may be unreachable. See each `review.json`
for the exact allowlist and risks.

The attached M1 locks select the `autonomous` attention condition for seed
101 only. They are candidate preflights, not signed operator approvals.
The v2.1 protocol requires exact seed-specific task configs and M1 locks
before qualifying cases; seeds 102–125 and other attention conditions are
**not locked by these files**. No chain, profile, held-out, or seven-condition
run is authorized by this package. All formal eligibility remains false.

Static AST validation, source/receipt digest matching, M1 preflight
recomputation, and synthetic public-SDK action tests passed. Existing M1
focused tests: 16 passed, 16 deselected. No simulator attempt or native
evaluator result was produced for these candidates. Operator review should
either approve both exact indexes above or request revisions; any changed
file requires new digests and a fresh review.
