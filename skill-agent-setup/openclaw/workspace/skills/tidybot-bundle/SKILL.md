---
name: tidybot-bundle
description: Bundle a Tidybot skill and its dependencies into a single executable Python script for robot submission. Use when (1) submitting a multi-dependency skill to the robot, (2) preparing code for the /code/execute API, (3) resolving deps.txt dependency chains into one file.
---

# Tidybot Bundle

Resolves skill dependencies from `deps.txt`, topologically sorts them, and inlines all code into one self-contained Python script ready for robot execution.

## Usage

```bash
# Output to stdout
python scripts/tidybot-bundle.py <skill-name>

# Save to file
python scripts/tidybot-bundle.py tb-pick-and-place -o bundled.py

# With parameters (replaces __main__ block with custom call)
python scripts/tidybot-bundle.py tb-pick-and-place \
  --call 'pick_and_place(pick_target="ball", place_target="trash can")' \
  -o bundled.py

# Custom skills directory
python scripts/tidybot-bundle.py my-skill --skills-dir /path/to/skills
```

## --call Flag

Use `--call` (or `-c`) to inject a custom entry point. This strips the skill's `if __name__` block and appends your function call instead. This way the agent only needs to know the function signature from SKILL.md — no need to read the source code.

## How It Works

1. Reads `deps.txt` recursively to build dependency graph
2. Topological sort — dependencies before dependents
3. Extracts code: removes `if __name__` blocks from deps, keeps main skill's
4. Deduplicates imports and function definitions
5. Outputs single bundled script

## Skill Directory Convention

```
skill-name/
├── scripts/
│   ├── main.py      # Skill code
│   └── deps.txt     # One dependency per line
└── SKILL.md
```

The bundler looks for `<skills-dir>/<skill-name>/scripts/main.py` or `<skills-dir>/<skill-name>/main.py`.

For full reference, see `references/usage.md`.
