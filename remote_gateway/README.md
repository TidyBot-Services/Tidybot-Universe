# Public gateway

Browsers reach this process only. It logs people in, shows each person their own runs, and turns what they ask for into
requests it signs and sends to one harness. The harness refuses anything unsigned. The page is the main UI's own page, served
here with this person's session in place of the operator token, so New run and the run page have one implementation.

The design and its reasons are in `docs/DESIGN-public-remote-20261102.md` in the workspace notes. This is stage P0 there:
it runs on loopback. **It is not ready for other people yet**; see the list at the end.

## Run it

Python standard library only. Paths below are examples.

`gateway.json`:

```json
{"state_dir": "gateway-state", "upstream": "http://127.0.0.1:8860", "ui_dir": "benchmarks/attention_harness/ui",
 "listen_port": 8861, "cookie_secure": false, "stop_files": [], "sim_lock": "/path/to/sim.lock"}
```

`cookie_secure` stays true (the default) anywhere a browser reaches this over TLS; false is for plain loopback only.
`stop_files` are paths whose existence refuses new runs. The campus instance reads only its own stop file; it does not read the operator's `no-new-runs` flag.

New settings are opt-in, so existing configurations keep their behavior:

- `trusted_proxy` defaults to `false`. When true, a loopback TCP peer may supply one valid IP in `X-Real-IP`; missing or invalid headers fall back to the peer. Other peers cannot supply it. This address is used for login and anonymous request limits, never written to the audit log. Local accounts can forge this header; Unix sockets remain future work.
- `limits.logins_per_code_per_hour` (5) and `limits.logins_global_per_hour` (200) add failure budgets alongside `logins_per_hour` (10). All count failures within the current hour. The code budget uses a SHA-256 hash of the first six submitted characters; no clear prefix is retained. These counters live in memory and reset on restart.
- `disk_path` defaults to `null` and disables disk admission checks. When set, free space there must be at least `min_free_gb` (default 0, measured in GiB). Low space or a failed check refuses creation/start with `disk_low` (503), holds the queue, and shows a notice on the page and in `/api/lobby`. Existing runs continue.
- `retention_days` defaults to 7 and supplies the default age for the offline `prune` command below.
- `lab_codex` defaults to `null`: lab non-PARCC roles still receive `lab_source_not_offered`, and New run lists no Lab Codex option. An object such as `{"model":"gpt-6.1-sol","effort":"high","runs_per_day":6,"stop_percent":77}` adds “Lab Codex · gpt-6.1-sol” next to PARCC in New run; PARCC remains the default. Each role (Dev, Eval, Advisor) runs on the source the person chose. Lab OpenAI roles must use exactly the configured model or receive `lab_model_not_offered` (400). Options expose `hosted.lab_codex` with `model`, `reasoning_effort`, `quota_stop_percent` and `run_cap_points` (the harness's 5-point per-run cap). If any role uses Lab Codex, the gateway sets Dev reasoning effort and the quota stop percentage from configuration; runs without a Lab Codex role keep the public defaults. The current harness fixes Eval and Advisor Codex effort at high, so configuration requires `effort: "high"`. Supported common models are `gpt-6.1-sol` and `gpt-6-astra`.
- The gateway records whether each rebuilt run uses Lab Codex and counts only those runs successfully started in the current UTC calendar day, globally and persistently across restarts. At the Codex cap, creating or manually starting a Lab Codex run returns `codex_daily_limit` (429); a queued Lab Codex run keeps its place and blocks dispatch from the head until the next UTC day. PARCC-only runs and runs using only the person's own keys remain eligible for creation and start; the lobby notice describes the cap as applying to Lab Codex. Failed starts do not consume this count. Disabling `lab_codex` also prevents previously created Lab Codex runs from starting. The harness's quota checks and per-run quota stop remain in force.

```bash
python -m remote_gateway --config gateway.json secret
```

The harness is a separate instance with its own data, never the operator's: its own store, its own run root, no Memory.
It gets the secret on a file descriptor, not in its environment or arguments.

```bash
python -m benchmarks.attention_harness.init_store --store public/attention.sqlite3 --artifact-root public/artifacts
```

```bash
python -m benchmarks.attention_harness.ui_server --store public/attention.sqlite3 --artifact-root public/artifacts --run-catalog public/catalog.json --dev-v2-root public/runs --dev-v2-per-run-budget --port 8860 --public-gateway-mode --gateway-secret-fd 3 --sim-lock /path/to/sim.lock --run-slots 3 3< gateway-state/gateway.secret
```

```bash
python -m remote_gateway --config gateway.json serve
```

```bash
python -m remote_gateway --config gateway.json invite --days 14
```

That prints an invite id and the code. The code is shown once and stored only as a hash. One code is one person.
`invites`, `revoke-invite`, `revoke-user`, `pause` and `resume` do what they say; `pause` refuses new runs and leaves running ones alone.

## Run it as services

`deploy/` holds systemd user unit templates, so the gateway and harness can outlive a shell. Replace `__SRC__` (the checkout), `__PY__` (its Python) and `__DATA__` (the data folder). Restarting the harness ends a run in progress. Nothing in this repository installs or enables a unit automatically.

`deploy/campus/` is the S0 package for a gateway and harness on the same run host. It includes a config example (Secure cookies, trusted loopback nginx, 3 versions/run, 3 runs/person/day, 5 help requests, 3 open slots, 10 login failures/address/hour, 150 GiB disk floor, 7-day retention, 6 Codex starts/UTC day and a 77% stop line), two user units with `LimitCORE=0` and `Restart=on-failure`, and nginx/ufw templates. Replace every placeholder before review. `__SRC__` must be the installed `__WORKSPACE__`'s checkout. Use the host's shared `__SIM_LOCK__`.

For a separate campus harness, replace the unit's `__PORT__` and the gateway example's `__HARNESS_PORT__` with **8870**, so it can run beside the existing public harness on 8860. For example, the gateway value is:

```json
{"upstream": "http://127.0.0.1:8870"}
```

For the first trial, the coordinator instead points upstream at the existing `http://127.0.0.1:8860` harness. Do not start another harness on that port. The generic launcher retains its legacy 8860 default; the campus unit always passes its explicit port.

Keep `__DATA__/gateway-state` mode **0700**, for the gateway database, secret and audit log. Harness data belongs in a separate `__HARNESS_DATA__`, for example `/home/qifei/ab-campus-data` (`~/ab-campus-data`), also mode 0700: `attention.sqlite3`, `artifacts/`, `runs/`, and the simulator lock queue. The queue lives beside `__SIM_LOCK__`: for example, `~/ab-campus-data/sim.lock.queue.sqlite3` beside `~/ab-campus-data/sim.lock`. Use the same lock path as every other simulator caller on the host, outside gateway-state. Set `disk_path` to that harness data directory, since that is what fills. The prune root is **`~/ab-campus-data/runs`**, never gateway-state. The launcher takes `--secret __DATA__/gateway-state/gateway.secret`; without `--secret`, existing callers still use `DATA/gateway.secret`. The secret must retain owner-only permissions and reaches the harness only through fd 3.

Campus launches must select a key source explicitly. `--key-source stdin` reads only the first stdin line; `credential` reads only `$CREDENTIALS_DIRECTORY/parcc-key` and refuses if `CREDENTIALS_DIRECTORY` is unset; `none` starts without a PARCC key. These modes set `ATTENTION_NO_HOME_LAB_KEY=1`, clear inherited PARCC/LiteLLM keys, and never consult `~/.litellm-key`. The selected key is put only in the child's `PARCC_API_KEY` environment, never in logs, argv or a launcher-created file. With no key, provider defaults report PARCC unavailable with a reason; the harness still starts. `--key-source home` permits the legacy home-file behavior. Omitting the option preserves existing callers, including `--key-stdin` as an alias for stdin.

The campus unit uses `--key-source credential` with `LoadCredential=parcc-key:__KEY_FILE__`. This is optional: to run without PARCC, remove `LoadCredential` and change the source to `none`. When using credentials, replace `__KEY_FILE__` with an existing protected key source managed by the operator; systemd supplies the runtime credential, and `%d/parcc-key` in the hidden-path list names it. Do not copy a key into the harness data directory.

The first trial uses **`--key-source stdin`**, fed from the operator's machine over ssh as today. An illustrative command, run on the operator's machine after replacing paths (not a deployment step performed by S0):

```bash
ssh RUN_HOST 'ATTENTION_PREFLIGHT_HIDDEN_PATHS=/path/to/gateway-state:/path/to/gateway-state/gateway.secret /path/to/python /path/to/src/remote_gateway/deploy/run_host_harness.py /path/to/workspace /home/qifei/ab-campus-data 8870 --secret /path/to/gateway-state/gateway.secret --key-source stdin --sim-lock /path/to/shared/sim.lock --run-slots 3' < /path/on/operator-machine/parcc-key
```

For a manual stdin launch, set `ATTENTION_PREFLIGHT_HIDDEN_PATHS` on the run host to the absolute gateway-state and secret paths, separated by `:`, as above. The credential unit includes those paths and `%d/parcc-key` automatically. Preflight checks these paths with stat only and refuses launch if any is visible inside the agent sandbox; empty entries are ignored and relative paths are refused. Until the dedicated user from C9 exists (requires sudo), the harness itself runs as the same account as the gateway and **can still read gateway-state**. Only the agent sandbox is kept out; this is not account-level separation.

From that checkout, `sh remote_gateway/deploy/campus/pause.sh CONFIG` pauses admission. `off.sh CONFIG MAINTENANCE_FILE [UNIT]` pauses, creates nginx's maintenance file, then stops the gateway; `on.sh` starts it, resumes admission, then removes maintenance. Paths are arguments; `PYTHON` can name the environment's Python. These scripts affect only the gateway unit; existing harness runs continue. The maintenance file and its parent directories must be readable/searchable by nginx, with its containing directory writable only by the operator. Its configured path must match the scripts' argument.

The nginx template explicitly lists the gateway's routes, overwrites `X-Real-IP`, sets HSTS and an 8 KiB request limit, and leaves unknown paths at 404. Logout is `DELETE /api/session`, not a separate `/logout` route. Live pictures use repeated HTTP GETs, so buffering is off and read timeouts exceed the gateway's 120-second upstream timeout. Access logging is disabled; do not add body/cookie logging. `sh remote_gateway/deploy/campus/ufw-rules.sh HOST_IP CIDR [CIDR ...]` only prints 443/tcp allow rules; `--apply` explicitly opts in and requires root. Get actual wired, Wi-Fi and VPN ranges from SEAS IT. No template or script has been installed or run as a service as part of S0.

## Retention

```bash
python -m remote_gateway --config gateway.json prune --runs-root /path/to/public/runs
python -m remote_gateway --config gateway.json prune --runs-root /path/to/public/runs --days 7 --delete
```

For the campus example, pass `--runs-root "$HOME/ab-campus-data/runs"` to these commands.

The default is a dry run: each candidate's absolute path, file size in bytes, age in days, and a total. Review the list and export anything wanted before using `--delete`; the first real deletion needs the owner's review. No timer is enabled. Run cleanup while the gateway is stopped and nobody else is changing the run root.

Only real, direct child directories matching `devexp-[0-9a-f]{24}`, known in this gateway's runs table, with a recorded terminal state older than the retention period qualify. Gateway listings persist an explicit end time when all terminal rows provide one; otherwise they persist the first time all rows of a run are observed ended. This conservative fallback clock starts then, even if the harness ended earlier. A resumed/queued run clears that clock. Unknown, unfinished, queued and never-observed runs stay. Neither creation time nor filesystem timestamps establish completion. Symlink roots/entries, links inside candidates, special files, and paths resolving outside the root are refused. `--delete` removes each candidate directory and its gateway runs/queue/start-failure rows; it does not edit the harness database or remove other data.

## Moving the harness to another host

`upstream` is the only place that says where the harness is. The harness accepts a loopback peer only, so run a tunnel on this
host (for example `ssh -L 8860:127.0.0.1:8860 runhost`) and leave `upstream` pointing at it. Set `host_id` to name the machine:
each run records the host it was created on. Nothing here reads the harness's disk. Evidence and video come through the signed link.

## What it does

- **Login**: an invite code becomes a session cookie (HttpOnly, SameSite=Strict, Secure unless turned off). Writes also need the session's CSRF token.
- **Watching**: anyone may open the page and read every run, signed in or not. Starting a run, stopping it, replying and keys need an invite; only a run's owner may act on it.
- **Queue and time-share**: Start joins a first-come line, one place per person. The gateway starts the next run whenever fewer than `limits.run_slots` runs are open (default 5; a person may have `limits.active_runs` at once, default 2), under its owner. It never holds the robot: the harness (started with `--sim-lock` and `--run-slots`) takes the host's lock for one Try or version at a time, first come first served by execution, so a run that just used the robot waits behind whoever was already waiting. Read-only probes do not take it. Waiting for the robot does not count toward a run's time. Remote runs have no time limit; a run with no new execution, version or help request for 2 hours of active time stops itself with reason `no_progress`. Version, help, probe, quota and disk limits are unchanged. The gateway's `sim_lock` setting is now read only by the crowd-test monitor.
- **New run**: built from named fields, never copied. By default all three roles must be the lab's PARCC or a key this person saved (a preset provider, or a custom endpoint when the harness has `ATTENTION_ALLOW_CUSTOM_PROVIDER_URL=1`; it must be HTTPS on public addresses). Explicit `lab_codex` configuration also offers the configured Lab Codex model for each role; the gateway preserves each role's choice. Limits are clamped here, whatever the page sends.
- **Names**: a person's first run uses their stable anonymous nickname; later runs show `nickname · run N`, ordered by creation in the gateway's known runs.
- **Keys**: pass through to the harness's memory, scoped to the person by the signed owner. The gateway keeps no key, and logs no body.
- **Answers**: lists are rebuilt from owned runs; JSON loses host paths and the harness's private fields. Video and frames stream unchanged, with Range.
- **Replies**: an interactive run waits `human_reply_seconds` (120 by default) for its owner at each help request, then the Advisor answers and the record says who did. A frozen comparison run never waits.
- **Everything else** answers 404. There is one list of what is allowed, in `policy.py`, and it is default-deny.
- **Log**: `audit.jsonl` records who did what to which kind of route, with identifiers taken out. No bodies, cookies, codes or addresses.

## Before anyone else may use this

S0 gateway code now supplies trusted client addresses, address/code/global login budgets, run-number nicknames, disk admission, dry-run retention, and an explicit lab Codex switch with a daily start cap. Campus Secure-cookie/limit examples and service/nginx/ufw templates are written. This is implementation and local test evidence only; it does not establish deployed or formal experimental acceptance.

- **Reaching it from outside**: templates still need host review, certificates, DNS, actual campus CIDRs and explicit S1 approval. No network opening or installation is part of S0.
- **Isolation**: a dedicated system user, account/file permissions and GPU/shared-lock access need host-owner agreement and sudo. Per-user harness instances remain open work before wider use.
- **Keys in memory**: `LimitCORE=0` is in the templates; swap encryption/disablement and system-wide apport/core handling still need approved sudo work and host verification.
- **Endpoint DNS safety**: the separate harness DNS-rebinding task (A3) is outside this gateway change and must pass its own acceptance.
- **Permission and consent**: written SEAS IT/ISC, PI, PARCC and IRB answers, privacy/consent text and review of OpenAI account/data terms remain open. Codex's temporary-use end condition also remains undecided.
- **Retention operations**: review the first real prune list before deletion. No timer or cleanup of any other account's files is authorized.
