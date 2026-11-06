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
 "listen_port": 8861, "cookie_secure": false, "stop_files": []}
```

`cookie_secure` stays true (the default) anywhere a browser reaches this over TLS; false is for plain loopback only.
`stop_files` are paths whose existence refuses new runs, such as a host's no-new-runs flag.

```bash
python -m remote_gateway --config gateway.json secret
```

The harness is a separate instance with its own data, never the operator's: its own store, its own run root, no Memory.
It gets the secret on a file descriptor, not in its environment or arguments.

```bash
python -m benchmarks.attention_harness.init_store --store public/attention.sqlite3 --artifact-root public/artifacts
```

```bash
python -m benchmarks.attention_harness.ui_server --store public/attention.sqlite3 --artifact-root public/artifacts --run-catalog public/catalog.json --dev-v2-root public/runs --dev-v2-per-run-budget --port 8860 --public-gateway-mode --gateway-secret-fd 3 3< gateway-state/gateway.secret
```

```bash
python -m remote_gateway --config gateway.json serve
```

```bash
python -m remote_gateway --config gateway.json invite --days 14
```

That prints an invite id and the code. The code is shown once and stored only as a hash. One code is one person.
`invites`, `revoke-invite`, `revoke-user`, `pause` and `resume` do what they say; `pause` refuses new runs and leaves running ones alone.

## Moving the harness to another host

`upstream` is the only place that says where the harness is. The harness accepts a loopback peer only, so run a tunnel on this
host (for example `ssh -L 8860:127.0.0.1:8860 runhost`) and leave `upstream` pointing at it. Set `host_id` to name the machine:
each run records the host it was created on. Nothing here reads the harness's disk. Evidence and video come through the signed link.

## What it does

- **Login**: an invite code becomes a session cookie (HttpOnly, SameSite=Strict, Secure unless turned off). Writes also need the session's CSRF token.
- **Ownership**: a run belongs to the person who created it. Anyone else gets 404, the same as a run that does not exist, and the harness is not asked.
- **New run**: built from named fields, never copied. All three roles must be the lab's PARCC or a key this person saved, so nothing can reach the lab's Codex. Limits are clamped here, whatever the page sends.
- **Keys**: pass through to the harness's memory, scoped to the person by the signed owner. The gateway keeps no key, and logs no body.
- **Answers**: lists are rebuilt from owned runs; JSON loses host paths and the harness's private fields. Video and frames stream unchanged, with Range.
- **Replies**: an interactive run waits `human_reply_seconds` (120 by default) for its owner at each help request, then the Advisor answers and the record says who did. A frozen comparison run never waits.
- **Everything else** answers 404. There is one list of what is allowed, in `policy.py`, and it is default-deny.
- **Log**: `audit.jsonl` records who did what to which kind of route, with identifiers taken out. No bodies, cookies, codes or addresses.

## Before anyone else may use this

Not done, on purpose. Each needs its own decision.

- **Reaching it from outside**: no tunnel or reverse proxy exists. Opening it to the internet is a separate step that the owner approves in person.
- **Queue**: one simulation runs at a time. A second person's Start is refused until the robot is free; nothing waits in line for them yet.
- **Client address**: behind a tunnel every login looks like loopback, so the login limit would be shared by everyone until a trusted forwarded address is read.
- **Isolation**: one harness holds everyone's keys in one process. Before the internet that should become one instance per person, under a dedicated system user (needs sudo).
- **Keys in memory**: swap and core dumps can write memory to disk. Both need closing on the host (needs sudo).
- **Permission**: PARCC has to allow other people's use through this page, and the school's rules on serving from a campus machine have to be checked.
- **Consent**: collecting strangers' replies needs the consent text and the IRB answer in the design notes.
- **Retention**: nothing deletes old runs yet.
