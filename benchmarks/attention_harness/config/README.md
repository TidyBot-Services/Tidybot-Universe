# config/

`lobby-config.8769.json` is an example demo lobby configuration for the shared Remote page (`--lobby-config`). It lists the
recorded runs the demo replays; the last source, `devexp-d9d6c17c…`, is the turn_on_microwave recording (seed 101,
4 attempts, 4 replay_trim sidecars).

The runtime configuration lives in the user's own `~/.config` (for example `~/.config/attentionbench-8769/lobby-config.json`)
and is not edited from the repository. This file is an example without any machine path: it has **no `state_dir`**, which
the server requires to be an absolute directory outside the repository. Copy the file, add `"state_dir": "<absolute path>"`,
or copy only the `sources` entries you need into the runtime file. Sources are experiment ids in the server's
`daily-experiments` directory.
