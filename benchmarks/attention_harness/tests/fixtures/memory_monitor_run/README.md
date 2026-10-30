# Synthetic Memory monitor run

This is authored test data, not a copy of a historical run. The experiment ID,
note ID, and last retrieval timestamp preserve the existing monitor assertions;
they do not identify an artifact dependency. No service credentials are present.
The fake `api_key: top-secret` and exception text used to check scrubbing remain
planted by the test itself.

`run.json` contains one cell, five valid versions, five settled failed execute
jobs, five settled eval jobs, and retrieval/result-skipped/eval events. The test
creates the exact tables and constraints with `DevV2Store` in `tmp_path` and
inserts these minimal monitor-facing payloads. Unused goal/experiment tables are
empty; the fake Bridge supplies the experiment envelope as before. `$artifact`
references become temporary absolute paths with SHA-256 digests of the copied
files. There are no references to external runs or models.

Each version has a packet containing the same test-seeded note, with a distinct
synthetic grant and matching development version. Version 1 intentionally has
an empty inline `versions` list to exercise the monitor's legacy packet fallback;
versions 2–5 exercise inline entries and grant-based skipped-result deduplication.
The last retrieval is exactly `1792472286.534213`. Skipped results do not count as
new reads or advance last-used time. Every eval references a completed negative
review; execution receipts record failure independently of eval outcome.

The five packets and shared eval review are the only files the monitor reads
from the run. All are small, plain synthetic JSON. The first test retains its
historical name so existing test selections continue to work; its ledger is now
entirely synthetic.
