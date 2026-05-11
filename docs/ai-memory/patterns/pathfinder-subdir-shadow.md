# Pattern — Python PathFinder Shadows Editable Install via Repo-Root Subdir

**Discovered:** 2026-05-09 (debugging Counter-To-Cab sim launch failures)

## TL;DR

If a pip-editable package's repo root contains a subdir with the same name as another top-level pip-editable package, Python's PathFinder discovers the subdir as the top-level — silently shadowing the canonical install. `@register_env` / module-level side effects in the canonical never run.

## How we hit it

We had two editable installs:

- **`robocasa_tasks`** → `/Tidybot-Universe/sims/robocasa_tasks/` (canonical)
- **`maniskill_tidyverse`** → `/maniskill-tidyverse/` (the repo root IS the package, via `package_dir={"maniskill_tidyverse": "."}`)

After a cleanup commit, the `maniskill-tidyverse/robocasa_tasks/` subdirectory had been removed. To keep 36 downstream imports of `from maniskill_tidyverse.robocasa_tasks import robocasa_utils` working, we added back a "shim" `maniskill-tidyverse/robocasa_tasks/__init__.py` that re-exported the canonical.

**Result on fresh import**:
- `import robocasa_tasks` (top-level) resolved to the SHIM at `/maniskill-tidyverse/robocasa_tasks/__init__.py`, not the canonical at `/Tidybot-Universe/sims/robocasa_tasks/__init__.py`
- The canonical's `__init__.py` (which does `from . import single_stage` triggering all `@register_env` decorators) was never imported
- `gym.make('RoboCasa-Pn-P-Counter-To-Cab-v0')` → `NameNotFound`

## Why it happens

Python's import system tries finders in `sys.meta_path` order. The default `PathFinder` comes early (position 3). When PathFinder walks editable installs' configured paths, it sees `/maniskill-tidyverse/robocasa_tasks/` as a discoverable package directory — and returns it for top-level `robocasa_tasks` lookup, **before** the dedicated `__editable___robocasa_tasks_0_1_0_finder` (lower in meta_path) gets a turn.

This is not a bug in Python — it's how `package_dir = "."` interacts with editable install path discovery. Once `maniskill_tidyverse`'s repo root is on the lookup hot path, any subdir in it that matches a top-level package name shadows the real install.

## Diagnose

If `import X` returns a path you don't expect:

```python
import importlib.util
from importlib.machinery import PathFinder
spec = PathFinder.find_spec('X')
print('PathFinder result:', spec.origin)

import sys
for i, finder in enumerate(sys.meta_path):
    try:
        s = finder.find_spec('X', None, None)
        if s: print(f'[{i}] {type(finder).__name__}: {s.origin}')
    except: pass
```

If PathFinder (low index) and the per-package `_EditableFinder` (higher index) both return specs, **PathFinder wins** and you're being shadowed.

## Fix patterns

1. **Best — rewrite imports**: change the downstream code to import the canonical package directly (`from robocasa_tasks import ...` instead of `from maniskill_tidyverse.robocasa_tasks import ...`). Eliminates the need for a shim entirely.

2. **OK — different subdir name**: if you must keep a shim, name the subdir something that doesn't collide with any top-level package (e.g. `_robocasa_shim/`).

3. **Bad — lazy `__getattr__` shim** (PEP 562 module-level): tested 2026-05-09 — still shadows because the subdir exists at import-resolution time. Won't help.

4. **Bad — `sys.path` manipulation**: brittle, easy to forget, only works for processes that import in a controlled order.

## Symptoms to watch for

- An editable package "exists" (`pip show` works, `pip list` shows it) but `import` resolves to the wrong place
- Side effects in `__init__.py` (env registrations, monkeypatches, plugin loading) silently don't fire
- `find_spec` returns a path inside an unexpected repo
- `python -c "import X; print(X.__file__)"` shows the surprise path

## Related

- `decisions/0005-stdout-via-files.md` — different bug, same root cause class: "contract assumed between modules but not enforced"
- 2026-05-09 fix commits:
  - `maniskill-tidyverse` `fbbac3a` (revert shim)
  - `maniskill-robocasa-tasks` `a0debd7` (rewrite 36 imports)
  - `maniskill_sim` `747f867` (sim's own import update)
