Captured API responses from 2026-10-19 for the two Dev v2 experiments in the filenames.
Each `experiment.json` is the experiment endpoint; each `view.json` is its cell view endpoint.

The fixture copies omit large source/diff text, embedded images, observation bodies and base64 data. IDs, states, reasons, job/receipt structure, action order and relative timestamps, and recording timing arrays are retained.

The cell view does not embed the separately fetched execution trace. Attempt 4's real action events are in `versions[3].public_feedback.action_trace`; its real recording and replay timing are in `versions[3].recording`.
