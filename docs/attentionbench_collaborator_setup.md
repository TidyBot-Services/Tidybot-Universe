# AttentionBench collaborator workspace

## Prerequisites

You need Ubuntu 22.04 or 24.04 on x86_64, an NVIDIA GPU with its driver, Git, network access, and about **80 GB free**. A first install peaks near 75 GB and ends near 53 GB, because archives and download caches are removed as soon as nothing needs them.

`setup.sh` does the rest:

- It installs pinned copies of `uv`, Conda and the Codex CLI in `WS/tools`. Your own copies are neither used nor changed.
- It lists the Ubuntu packages this host still lacks (`build-essential`, `bubblewrap`, `ffmpeg`, `libegl1`, `libgl1`, `libglib2.0-0`, `libgomp1`) and any AppArmor change, and asks **once** before running them with `sudo`. If you decline, or stdin is not a terminal, it prints the commands and changes nothing. The install still goes on; a missing AppArmor change then stops `doctor` at the end, and rerunning `setup.sh` after the change is quick.
- It ignores your shell's `PYTHONPATH`, `LD_LIBRARY_PATH`, active Conda or virtual environment, `PIP_*`, `UV_*`, `CC` and `CUDA_HOME`. Proxy, certificate and package-index settings pass through, and so does `CUDA_VISIBLE_DEVICES`.

No model account is needed to install or to verify the workspace. Only a real run needs one, see [Model routes](#model-routes).

An NVIDIA driver older than 570.124.06 gives a warning, and the planner proof on your GPU decides. x86_64 is required because the locked environments are built for it. If `bwrap` cannot create user namespaces and you have no administrator, agent runs cannot start.

## Supported systems

| Item | Ubuntu 22.04 | Ubuntu 24.04 |
|---|---|---|
| `setup.sh` and `doctor` on the host | Verified | Not verified yet |
| `bwrap` sandbox | Works by default | Needs the one-time AppArmor change below |
| Planner chain and GPU build proof | Verified (RTX 5070 Ti, sm_120, driver 580.178.04) | Not verified yet |
| `run.sh` agent run | Verified with Codex | Not verified yet |
| Own-key and PARCC routes through `run.sh` | Not verified yet | Not verified yet |
| Docker appendix | Verified on a 22.04 host | Not verified; use the AppArmor 4 profile named there |

The UI, simulator and cuRobo environments bring their own Python, compiler and CUDA toolchain, so the system Python (3.10 on 22.04, 3.12 on 24.04) only starts `setup.sh`. `uv` must be allowed to download Python 3.10 and 3.11; `setup.sh` checks this.

### Ubuntu 24.04: user namespaces

Ubuntu 23.10 and later set `kernel.apparmor_restrict_unprivileged_userns=1`, so `bwrap` cannot create the sandbox. `setup.sh` detects this from the setting and a failed probe, whatever the error text says. It prints the AppArmor profile from `config/attentionbench/apparmor/attentionbench-bwrap-userns` and its commands, and asks together with any missing packages. The machine-wide restriction stays on, and any local user can then use `/usr/bin/bwrap` to create user namespaces. To undo it, run `sudo apparmor_parser -R /etc/apparmor.d/attentionbench-bwrap-userns`, then `sudo rm /etc/apparmor.d/attentionbench-bwrap-userns`.

If you prefer to open the whole machine instead:

1. **Temporary, until reboot:** `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`.
2. **Permanent:** `echo 'kernel.apparmor_restrict_unprivileged_userns=0' | sudo tee /etc/sysctl.d/60-attentionbench-userns.conf && sudo sysctl --system`.

### System updates and the planner

`setup.sh` builds a planner lock on your machine. It records the SHA256 of your `libcuda` and `libc.so.6`, plus a host fingerprint (OS, kernel, glibc, driver, GPU, libraries) that is also written to `data/run/host-fingerprint.json`.

glibc must be 2.35 or newer, which Ubuntu 22.04 and 24.04 are. A driver older than 570.124.06 gives a warning; the planner proof decides whether it works. Driver 580.178 is the only one tested so far. Other Ubuntu releases give a warning.

If a driver or `libc6` update changes those files later, often through `unattended-upgrades`, development runs keep working:

- `doctor` shows a `host-libraries` warning that names the changed files.
- The Run page shows `host libs changed since lock built`, and each run's receipt records the host fingerprint and the change.
- Summaries mark runs from different hosts as `host differs`. A Memory A/B validation whose two arms ran on different hosts stops at `needs_review`.

Two optional commands follow a change. Stop the services first for the second one.

```bash
/absolute/WS/repos/attentionbench/scripts/attentionbench/setup.sh --workspace /absolute/WS --reprove
/absolute/WS/repos/attentionbench/scripts/attentionbench/setup.sh --workspace /absolute/WS --rebuild-planner
```

`--reprove` reruns the planner proof on the current host and writes `data/run/host-reproof-<UTC>.json`; it changes nothing else. `--rebuild-planner` makes a fresh lock with the same checks as a first install. The old `deployments/` directory is renamed to `deployments.superseded-<UTC>` and is not deleted; keep it while you still need runs that used it.

The lock no longer guarantees that two development runs used the same driver or glibc, so planner output can differ between them. Compare the host fingerprints before drawing conclusions. The formal tier still refuses any host library change.

### Results from different machines

Each machine's lock is trusted only on that machine, and every run from it is development tier (`formal_eligible=false`). Results from different machines were produced with different host libraries, so they cannot be compared as formal results.

## Three commands

Choose a workspace path and replace `/absolute/WS` below:

```bash
mkdir -p /absolute/WS/repos && git clone -b attentionbench/baseline-20261015 https://github.com/TidyBot-Services/Tidybot-Universe.git /absolute/WS/repos/attentionbench
/absolute/WS/repos/attentionbench/scripts/attentionbench/setup.sh --workspace /absolute/WS
/absolute/WS/repos/attentionbench/scripts/attentionbench/run.sh --workspace /absolute/WS
```

Keep the path under 90 bytes, without spaces, `:` or `#`; `setup.sh` checks this at once. A symlink in the path is resolved. A clone somewhere else moves itself into the workspace.

`setup.sh` prepares the host, downloads and checks everything, runs `doctor`, then runs `verify.sh`, and tells you which model routes are ready. If it stops or the network drops, run it again: it continues where it stopped. After `git pull`, run `setup.sh --update`.

`run.sh` starts the services, submits one development-tier request, waits up to 45 minutes (`--minutes N`; in a clean container the first version took 37 minutes in one run and did not appear within 30 in another), cancels the run if it is still going, writes `data/run/<experiment_id>-summary.json`, and stops services and agent workers. Use `--lock FILE` to serialize a complete run including shutdown. `--no-stop` leaves services running after the run. `stop.sh --workspace /absolute/WS` cancels running agent workers before stopping UI and Memory.

### Verify without a model

`verify.sh --workspace /absolute/WS` needs no account. It proves four things and writes `data/run/verify-<UTC>.json`:

| Check | What it proves |
|---|---|
| Scene and renderer | The registered scene matches and a camera frame has content |
| Planner | The planner proof passes on this GPU |
| Sandbox | The agent sandbox starts and denies what it must deny |
| Recording | H.264 encoding and `ffprobe` work |

`verify.sh --model NAME` also asks that route's provider what each agent needs.

### Model routes

`run.sh --model NAME` chooses who answers for the Dev, Eval and Advisor agents. Without `--model` it takes the first ready route in the table's order, and it stops with instructions if none is ready. A key is read from the environment or key file, sent once to the local UI's memory, and never printed or written to a file.

| Route | You provide | Default models |
|---|---|---|
| `codex` | `~/.codex` login: `WS/tools/codex/bin/codex login` | `gpt-6.1-sol` |
| `parcc` | `PARCC_API_KEY`, or the key in `~/.litellm-key` (University of Pennsylvania) | Dev and Eval `parcc/GLM`, which reads text only; Advisor `parcc/Qwen` |
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-flash` |
| `openai` | `OPENAI_API_KEY` and `--dev-model NAME` | The named model |
| `custom` | `--base-url URL --api-key-env NAME --dev-model NAME` for any OpenAI-compatible HTTPS endpoint | The named model |

`--eval-model` and `--advisor-model` set those agents separately. The same choices are in the UI under Models & keys. The Codex CLI runs the agents on every route; only the `codex` route needs a login, and only that route uses Codex quota. Agent runs on it read the weekly quota from Codex's own session records and accept a reading at most 10 minutes old, so `run.sh` makes one short Codex call (`codex exec --skip-git-repo-check "Reply with ok."`) when no recent reading exists.

Dev needs tool calls and streaming, Eval needs both too, and the Advisor needs plain chat. Eval and Advisor should read images. **A model that cannot read images still runs, with a warning:** `run.sh` prints `Eval did not see images`, the summary lists it under `warnings`, and the run page says so. That agent is sent text in place of each image, so its judgement rests on text alone. If a route's default Eval or Advisor model is down, `run.sh` falls back to Dev's model and says so. Own-key runs have a time limit but no spending limit, so check your provider's billing.

Memory validation runs still need the `codex` route.

### Manual path

Run `scripts/attentionbench/bootstrap.sh --workspace DIR`, `scripts/attentionbench/doctor.sh --workspace DIR`, and `scripts/attentionbench/start.sh --workspace DIR` from the checkout. Use `scripts/attentionbench/stop.sh --workspace DIR` to stop. `setup.sh --dry-run --workspace DIR` checks the host, prints what it would ask and the bootstrap plan, and changes nothing. To reuse a complete existing official RoboCasa asset tree instead of downloading, add `--assets-from /absolute/path/to/robocasa/models/assets`; it is read and SHA256 checked. The planner chain assembles its parent project from this checkout, its tracked OpenArm model, the pinned official cuRobo checkout, and `repos/robocasa_tasks` at `b18bbf1585c42e370ae45ababdddad700cc2c71d`. `--planner-project-from` remains an override for an existing complete project tree; `--planner-native-assets-from` reuses an existing native asset tree. Missing or differing inputs stop the chain with the affected step and entries.

Bootstrap creates `tools/`, `cache/`, `repos/`, `envs/`, `sim/`, `deployments/`, and `data/`; installs the pinned tools; checks out exact commits; makes a Python 3.10 UI environment, a Python 3.11 simulator environment, and `envs/curobo-env` from the committed Conda and pip specifications; downloads the seven official RoboCasa archives listed in `config/attentionbench/asset-sources.json` and writes only files whose SHA256 matches the manifest; writes relative `paths.json` values; initializes the UI and Memory stores; and creates private random Memory keys. The planner step downloads the public ManiSkill RoboCasa dataset and Robotiq description, applies `config/attentionbench/native-assets/plate_17-overlay.tar.gz` (the one local change in the r12 tree), checks the official source and all 70,254 native files, writes an r12-schema official lock, derives d05 and d06, runs two fixed isolated local plans, writes a protected 0444 local trust registry, and materializes a dev-local snapshot. Planner stamps are under `data/installer/stamps/`. The native tree is about 8.2 GB, the author's cuRobo environment about 15 GB, and the r12 project about 70 MB. About 15 GB of archives are downloaded; asset transfer, hashing and Conda installation can each take tens of minutes or longer. Long steps print a line every minute. A rerun repeats only the unfinished step. When a checkout changes a step's inputs, `setup.sh --update` rebuilds it; old deployments move to `deployments.superseded-<UTC>`. A long agent run can use 20 GB or more, a run needs 40 GB free to start, and it pauses below 15 GB.

`start.sh` launches Memory on loopback port 8878 and the UI on loopback port 8769, waits for both health checks, and prints the UI address. If a port is busy it uses the next free one and remembers it in `data/run/ports.json`, which `doctor`, `run.sh` and `stop.sh` read. Use `--ui-port` and `--memory-port` to choose ports yourself. On a machine with several GPUs it uses the one with the most free memory; set `CUDA_VISIBLE_DEVICES` to choose. Several GPUs have not been tested. PID files and logs are under `data/run/`. `stop.sh` cancels live workers with matching process identities, waits, then stops UI and Memory. If a worker cannot stop, the services stay up and the command reports its PID and log.

## Troubleshooting

Each bootstrap error names its step, command failure or mismatch, and a repair. Rerun `setup.sh` after an interrupted download or a full disk. If asset verification still reports files, the official archive no longer matches the manifest, so send the list back. If `doctor` reports no GPU, check the NVIDIA driver and `torch.cuda.is_available()` in `envs/curobo-env/bin/python`. For isolation failures, install bubblewrap and enable unprivileged user namespaces. If a run stops at a model check, the message names the role and the provider's answer. If the `verify.sh` renderer check fails on a server, install the driver's OpenGL libraries (`libnvidia-gl-*`) as well as `libegl1`. For a missing `paths.json` target, create or provide the required local data; several legacy/test evidence entries are intentionally not generated by a fresh installer.

Run `python3 scripts/attentionbench/build_planner_chain.py --workspace DIR --dry-run` to inspect only the planner steps. `--report-official-diff` builds the environment, prints every source or prefix file whose bytes differ from the committed manifests, and reports whether each actual file contains the workspace path. It also reports dependency and distribution differences. This read-only report is for proposing a path exception list to the user; it never edits `allowed-path-files.json`. The official-content check still stops the chain before trust registration on any unapproved difference. The author's editable cuRobo installation is confirmed by its `direct_url.json`; its exact build isolation flags and any kernel compilation flags are absent from the available lock and runbook. The separate native ManiSkill scene path requires an 8.48 GB `scene_datasets` tree and its own lock.

The build proof runs as a standalone script under the newly built cuRobo Python with `-B -I`. It imports official cuRobo from `repos/curobo` and the d06 planner adapter from the d06 project, executes the committed two-plan request set, and records request and result hashes. It does not use `V2Service` or call `identity.load_lock`; it is **evidence that the locally built chain plans, not a trust decision**. Registry creation follows the proof and remains subject to the approved official-content rules.

The parent project inventory is not part of the official content comparison. The original r12 native task tree's `.git/config` identifies `https://github.com/TidyBot-Services/maniskill-robocasa-tasks.git`, its HEAD is `b18bbf1`, and all 122 runtime source files in the inventory match that public commit byte for byte in the available local checkout. A local assembly comparison against r12 found zero differences in the 179 `config/openarm-model` files, 22 `config/openarm_description` files, 122 native task source files, and 24 Franka meshes. The model inputs come from tracked `openarm_phase0_recheck/model/compat_v6`; the robot YAML is stored as a root-substituted template. Most Franka meshes come from the pinned official cuRobo source. Three project-specific meshes are now carried in this checkout. Every excluded author-only or generated inventory entry and its reason is listed in `scripts/attentionbench/r12-project-omissions.json`: 256 Git administrative files, five generated egg-info files, four acceptance-run files, and the author's CUPTI probe source and compiled library. The parent snapshot omits that probe; since P4d the d05 layer compiles it locally in the pinned environment. The 1,237 copied project files form the parent snapshot; d05 still checks its full locked inventory before deriving.

**Official-environment comparison:** L2 passed with five fixed path map-back rules documented in [`curobo_v2/official/AUTHOR-PATHS.md`](../curobo_v2/official/AUTHOR-PATHS.md). A different machine may show new differences; send the `--report-official-diff` output back for review and do not relax the check. Offline unit tests do not attest the planner chain.

**Scene fingerprint:** `verify.sh` runs `scripts/attentionbench/scene_fingerprint_check.py`, which must print `matches_registered: True`.

## Docker appendix

Docker is optional. Inside Docker the agent's `bwrap` sandbox needs user namespaces and its own `/proc`, which the defaults forbid, so setup cannot fix this from inside. The setup verified on 2026-11-01 (Ubuntu 22.04 host, kernel 6.8, Docker 28.3.1, NVIDIA Container Toolkit 1.19.0) keeps seccomp and AppArmor on and only adds what `bwrap` needs:

1. Load the AppArmor profile once (needs root): `sudo apparmor_parser -r -W config/attentionbench/docker/attentionbench-bwrap.apparmor`. It is Docker's default profile plus `mount`, `umount`, `pivot_root` and user namespaces.
2. Generate a GPU CDI spec without the `disable-device-node-modification` hook (needs root to install): `nvidia-ctk cdi generate --vendor attentionbench.local --class gpu --disable-hook disable-device-node-modification --output attentionbench-gpu.yaml`, then `sudo mkdir -p /etc/cdi && sudo cp attentionbench-gpu.yaml /etc/cdi/`. With `--gpus all`, or with the default CDI spec, NVIDIA mounts files over `/proc/driver/nvidia`; the kernel then refuses a new `/proc` inside a user namespace (`bwrap: Can't mount proc on /newroot/proc: Operation not permitted`).
3. Start the container with `--device attentionbench.local/gpu=all --security-opt seccomp=config/attentionbench/docker/bwrap-seccomp.json --security-opt apparmor=attentionbench-bwrap --security-opt systempaths=unconfined`. The seccomp profile is Docker's default plus `unshare`, `clone`/`clone3` with namespace flags, `mount`, `umount2`, `pivot_root` and `setns`; `systempaths=unconfined` only removes Docker's masking of `/proc` paths. `--privileged` is not needed.

On a 24.04 host, load `config/attentionbench/docker/attentionbench-bwrap-2404.apparmor` in step 1 instead. It adds the AppArmor 4 `userns` rule that the host restriction requires, and it has not been verified yet. The CDI steps are expected to be the same.

`doctor` checks this with a `bwrap` namespace probe (`isolation`).
