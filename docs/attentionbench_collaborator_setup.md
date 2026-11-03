# AttentionBench collaborator workspace

## Prerequisites

Use Linux x86_64 with an NVIDIA GPU and a working driver, Git, Python 3.10+ with development headers (`python3-dev` on Ubuntu), a C compiler (`build-essential`), `uv`, Conda, `bwrap`, enabled unprivileged user namespaces, and a Codex account with access to `gpt-6.1-sol`. Reserve **about 70 GB free** for the first install: the workspace takes about 51 GB, and uv, pip and Conda keep about 15 GB of download caches outside it. `setup.sh` checks free space before anything else. After setup you can reclaim the caches with `uv cache clean; pip cache purge; conda clean -a`. Install `codex-cli 0.165.0` and sign in with `codex login`. `setup.sh` checks these prerequisites without installing host packages. Agent runs read the weekly quota from Codex's own session records and accept only a reading at most 10 minutes old; otherwise they stop with `quota_unreadable` (state `needs_review`). So `setup.sh` and, before each run, `run.sh` make one short Codex call (`codex exec --skip-git-repo-check "Reply with ok."`, using your own quota) whenever no recent reading exists. `setup.sh --no-codex-warmup` skips it during setup only.

## Supported systems

| Item | Ubuntu 22.04 | Ubuntu 24.04 |
|---|---|---|
| `setup.sh` and `doctor` on the host | Verified | Not verified yet |
| `bwrap` sandbox | Works by default | Needs a user namespace fix, see below |
| Planner chain and GPU build proof | Verified (RTX 5070 Ti, sm_120, driver 580.178.04) | Not verified yet |
| `run.sh` agent run | Verified | Not verified yet |
| Docker appendix | Verified on a 22.04 host | Not verified; use the AppArmor 4 profile named there |

The UI, simulator and cuRobo environments bring their own Python, compiler and CUDA toolchain, so the system Python (3.10 on 22.04, 3.12 on 24.04) does not matter beyond running `setup.sh`. `uv` must be allowed to download Python 3.10 and 3.11; `setup.sh` checks this.

### Ubuntu 24.04: user namespaces

Ubuntu 23.10 and later set `kernel.apparmor_restrict_unprivileged_userns=1`, so `bwrap` stops with `setting up uid map: Permission denied`. `setup.sh` reports this and never changes the setting on its own. Choose one fix:

1. **Recommended: allow only `bwrap`.** Run `setup.sh --workspace /absolute/WS --fix-userns`. It prints the AppArmor profile from `config/attentionbench/apparmor/attentionbench-bwrap-userns` and every command, asks for confirmation, then uses `sudo`. The machine-wide restriction stays on. Any local user can then use `bwrap` to create user namespaces. To undo it, run `sudo apparmor_parser -R /etc/apparmor.d/attentionbench-bwrap-userns`, then `sudo rm /etc/apparmor.d/attentionbench-bwrap-userns`.
2. **Temporary, whole machine, until reboot:** `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`.
3. **Permanent, whole machine:** `echo 'kernel.apparmor_restrict_unprivileged_userns=0' | sudo tee /etc/sysctl.d/60-attentionbench-userns.conf && sudo sysctl --system`.

### System updates and the planner

`setup.sh` builds a planner lock on your machine that records the SHA256 of your `libcuda` and `libc.so.6`, and writes the host details to `data/run/host-fingerprint.json`. If a driver or `libc6` update changes those files, often through `unattended-upgrades`, the planner refuses to start. `doctor` reports this as `host-libraries` and names the changed files. Stop the services, then rebuild:

```bash
/absolute/WS/repos/attentionbench/scripts/attentionbench/setup.sh --workspace /absolute/WS --rebuild-planner
```

The rebuild runs the same checks as a first install. The old `deployments/` directory is renamed to `deployments.superseded-<UTC>` and is not deleted; keep it while you still need runs that used it. To avoid rebuilds you can hold those packages with `sudo apt-mark hold`, which also holds back their security updates.

### Results from different machines

Each machine's lock is trusted only on that machine, and every run from it is development tier (`formal_eligible=false`). Results from different machines were produced with different host libraries, so they cannot be compared as formal results.

## Three commands

Choose an absolute workspace path and replace `/absolute/WS` below. Clone this repository **into the workspace** (a real directory, not a symlink):

```bash
mkdir -p /absolute/WS/repos && git clone -b attentionbench/baseline-20261015 https://github.com/TidyBot-Services/Tidybot-Universe.git /absolute/WS/repos/attentionbench
/absolute/WS/repos/attentionbench/scripts/attentionbench/setup.sh --workspace /absolute/WS
/absolute/WS/repos/attentionbench/scripts/attentionbench/run.sh --workspace /absolute/WS
```

`setup.sh` checks the host, warms up Codex quota if needed, bootstraps the workspace, and requires `doctor` to pass. `run.sh` starts the services, submits the locked L2 development-tier request, waits up to 45 minutes (`--minutes N`; in a clean container the first version took 37 minutes in one run and did not appear within 30 in another), cancels the run if it is still going, writes `data/run/<experiment_id>-summary.json`, and stops services and agent workers. Use `--lock FILE` to serialize a complete run including shutdown. `--no-stop` leaves services running after the run. `stop.sh --workspace /absolute/WS` cancels running agent workers before stopping UI and Memory.

### Manual path

Run `scripts/attentionbench/bootstrap.sh --workspace DIR`, `scripts/attentionbench/doctor.sh --workspace DIR`, and `scripts/attentionbench/start.sh --workspace DIR` from the checkout. Use `scripts/attentionbench/stop.sh --workspace DIR` to stop. `setup.sh --dry-run --workspace DIR` checks the host (GPU and Codex are warnings) and prints the bootstrap plan without creating `DIR`. To use a complete existing official RoboCasa asset tree, add `--assets-from /absolute/path/to/robocasa/models/assets`; it is read and SHA256 checked. The planner chain assembles its parent project from this checkout, its tracked OpenArm model, the pinned official cuRobo checkout, and `repos/robocasa_tasks` at `b18bbf1585c42e370ae45ababdddad700cc2c71d`. The native asset tree defaults to `DIR/data/robocasa365/assets`. `--planner-project-from` remains an override for an existing complete project tree; `--planner-native-assets-from` overrides the native asset source. Missing or differing inputs stop the chain with the affected step and entries.

Bootstrap creates `repos/`, `envs/`, `sim/`, `deployments/`, and `data/`; checks out exact commits; makes a Python 3.10 UI environment, a Python 3.11 simulator environment, and `envs/curobo-env` from the committed Conda and pip specifications; downloads and verifies the official locked assets; writes relative `paths.json` values; initializes the UI and Memory stores; and creates private random Memory keys. The planner step checks the official source and 70,254 native files, writes an r12-schema official lock, derives d05 and d06, runs two fixed isolated local plans, writes a protected 0444 local trust registry, and materializes a dev-local snapshot. Planner stamps are under `data/installer/stamps/`. The native tree is about 8.2 GB, the author's cuRobo environment about 15 GB, and the r12 project about 70 MB. Allow about 70 GB free for the first install (see Prerequisites); asset transfer, hashing and Conda installation can each take tens of minutes or longer. A changed planner stamp requires repair of its inputs or removal of that step's outputs and stamp before retrying.

`start.sh` launches Memory on loopback port 8878 and the UI on loopback port 8769, waits for both health checks, and prints the UI address. PID files and logs are under `data/run/`. Use `--ui-port` and `--memory-port` to choose other ports. `stop.sh` cancels live workers with matching process identities, waits, then stops UI and Memory. If a worker cannot stop, the services stay up and the command reports its PID and log.

## Troubleshooting

Each bootstrap error names its step, command failure or mismatch, and a repair. If asset verification fails, the listed files must be replaced from the official source; bootstrap never changes mismatching files. If `doctor` reports no GPU, check the NVIDIA driver and `torch.cuda.is_available()` in `envs/curobo/bin/python`. For isolation failures, install bubblewrap and enable unprivileged user namespaces. For login or version failures, run `codex login` or install the lock's CLI version. For a busy port, stop the owning process or choose another port at start. For a missing `paths.json` target, create or provide the required local data; several legacy/test evidence entries are intentionally not generated by a fresh installer.

Run `scripts/attentionbench/build_planner_chain.py --workspace DIR --dry-run` to inspect only the planner steps. `--report-official-diff` builds the environment, prints every source or prefix file whose bytes differ from the committed manifests, and reports whether each actual file contains the workspace path. It also reports dependency and distribution differences. This read-only report is for proposing a path exception list to the user; it never edits `allowed-path-files.json`. The official-content check still stops the chain before trust registration on any unapproved difference. The author's editable cuRobo installation is confirmed by its `direct_url.json`; its exact build isolation flags and any kernel compilation flags are absent from the available lock and runbook. The separate native ManiSkill scene path requires an 8.48 GB `scene_datasets` tree and its own lock.

The build proof runs as a standalone script under the newly built cuRobo Python with `-B -I`. It imports official cuRobo from `repos/curobo` and the d06 planner adapter from the d06 project, executes the committed two-plan request set, and records request and result hashes. It does not use `V2Service` or call `identity.load_lock`; it is **evidence that the locally built chain plans, not a trust decision**. Registry creation follows the proof and remains subject to the approved official-content rules.

The parent project inventory is not part of the official content comparison. The original r12 native task tree's `.git/config` identifies `https://github.com/TidyBot-Services/maniskill-robocasa-tasks.git`, its HEAD is `b18bbf1`, and all 122 runtime source files in the inventory match that public commit byte for byte in the available local checkout. A local assembly comparison against r12 found zero differences in the 179 `config/openarm-model` files, 22 `config/openarm_description` files, 122 native task source files, and 24 Franka meshes. The model inputs come from tracked `openarm_phase0_recheck/model/compat_v6`; the robot YAML is stored as a root-substituted template. Most Franka meshes come from the pinned official cuRobo source. Three project-specific meshes are now carried in this checkout. Every excluded author-only or generated inventory entry and its reason is listed in `scripts/attentionbench/r12-project-omissions.json`: 256 Git administrative files, five generated egg-info files, four acceptance-run files, and the author's CUPTI probe source and compiled library. The parent snapshot omits that probe; since P4d the d05 layer compiles it locally in the pinned environment. The 1,237 copied project files form the parent snapshot; d05 still checks its full locked inventory before deriving.

**Official-environment comparison:** L2 passed with five fixed path map-back rules documented in [`curobo_v2/official/AUTHOR-PATHS.md`](../curobo_v2/official/AUTHOR-PATHS.md). A different machine may show new differences; send the `--report-official-diff` output back for review and do not relax the check. Offline unit tests do not attest the planner chain.

## Docker appendix

Docker is optional. Inside Docker the agent's `bwrap` sandbox needs user namespaces and its own `/proc`, which the defaults forbid. The setup verified on 2026-11-01 (Ubuntu 22.04 host, kernel 6.8, Docker 28.3.1, NVIDIA Container Toolkit 1.19.0) keeps seccomp and AppArmor on and only adds what `bwrap` needs:

1. Load the AppArmor profile once (needs root): `sudo apparmor_parser -r -W config/attentionbench/docker/attentionbench-bwrap.apparmor`. It is Docker's default profile plus `mount`, `umount`, `pivot_root` and user namespaces.
2. Generate a GPU CDI spec without the `disable-device-node-modification` hook (needs root to install): `nvidia-ctk cdi generate --vendor attentionbench.local --class gpu --disable-hook disable-device-node-modification --output attentionbench-gpu.yaml`, then `sudo mkdir -p /etc/cdi && sudo cp attentionbench-gpu.yaml /etc/cdi/`. With `--gpus all`, or with the default CDI spec, NVIDIA mounts files over `/proc/driver/nvidia`; the kernel then refuses a new `/proc` inside a user namespace (`bwrap: Can't mount proc on /newroot/proc: Operation not permitted`).
3. Start the container with `--device attentionbench.local/gpu=all --security-opt seccomp=config/attentionbench/docker/bwrap-seccomp.json --security-opt apparmor=attentionbench-bwrap --security-opt systempaths=unconfined`. The seccomp profile is Docker's default plus `unshare`, `clone`/`clone3` with namespace flags, `mount`, `umount2`, `pivot_root` and `setns`; `systempaths=unconfined` only removes Docker's masking of `/proc` paths. `--privileged` is not needed.

On a 24.04 host, load `config/attentionbench/docker/attentionbench-bwrap-2404.apparmor` in step 1 instead. It adds the AppArmor 4 `userns` rule that the host restriction requires, and it has not been verified yet. The CDI steps are expected to be the same.

`doctor` checks this with a `bwrap` namespace probe (`isolation`).
