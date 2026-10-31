# Author paths allowed in the official-env comparison (fixed list)

The local dev trust check (P5) accepts a differing file in the collaborator's cuRobo env only when,
after mapping the collaborator's own paths back to ONE of the author paths below, the file (or the
hash recorded for it in a RECORD) is byte-for-byte equal to the author's. No other path, prefix
pattern or wildcard is accepted. `author-paths.json` holds exactly these values; they come from the
author env's history and recorded hashes (11-01 review), not from the collaborator.

| key | path | why it appears | used for |
|---|---|---|---|
| `prefix` | `/home/truares/Documents/Codex/2026-10-11/goal-curobov2-nvidia-service-1-service/work/official-env` | the author's current official env | editable-install text files (`__editable___nvidia_curobo_*_finder.py`), `direct_url.json` |
| `library_root` | `/home/truares/Documents/Codex/2026-10-11/goal-curobov2-nvidia-service-1-service/outputs/runtime/official` | the official cuRobo `78fd485` checkout the editable install points to | the same editable-install files |
| `binary_prefix` (+ `binary_prefix_slot`) | `/home/truares/Documents/Codex/2026-10-11/new-chat/work/curobo-v2-env` | first conda clone target (`conda-meta/history`, 2026-10-11 10:18); conda wrote this path into the interpreter binary with same-length padding | `bin/python3.11` |
| `origin_prefix` | `/home/truares/Documents/Codex/2026-10-11/openarm-curobo-integration/.venv` | source of that clone; pytest/pygmentize launchers were installed there | RECORD hashes of `../../../bin/{pytest,py.test,pygmentize}` |
| `script_prefix` | `/home/truares/miniconda3/envs/maniskill` | the environment the clone source descends from; 40 launchers were installed with this interpreter | RECORD hashes of the 40 scripts in `r12-bin-shebangs.json` |
