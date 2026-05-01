#!/usr/bin/env python3
"""Generate a unified graph.json containing ONLY the 32 real registered RoboCasa
single-stage task_envs. Dependencies reflect semantic transfer — when solving
task B can reuse the solution / strategy from task A.

NO synthetic primitives. Every node is a real `@register_env(...)` task.

Output: graphs/unified-single-stage/graph.json
"""

import argparse
import json
from pathlib import Path

DEFAULT_OUT = Path(__file__).resolve().parent / "graphs" / "unified-single-stage"

# ---------------------------------------------------------------------------
# Dependency graph over real task_envs.
#
# Reading guide: each line is `task → [tasks it builds on]`.
# Empty list = leaf (foundational, dev figures out from scratch).
#
# Heuristics behind the deps:
#   - Single-X comes before Double-X (single is the simpler case to learn first)
#   - Specific Open/Close come before generic Manipulate
#   - Press-Button comes before Turn-On/Off-Microwave (turning on/off IS pressing
#     a specific button, but the press-button task isolates the button-press
#     primitive without the on/off semantic)
#   - Manipulate-Stove-Knob comes before Turn-On/Off-Stove (knob rotation skill
#     reused with specific target angle)
#   - Manipulate-Sink-Faucet comes before Turn-On/Off-Sink-Faucet, Turn-Sink-Spout
#   - Open-Single-Door comes before pnp tasks that involve a closed cabinet
#     (Pn-P-*-Cab-* require the cabinet door to be opened first)
#   - Pn-P-Counter-To-Sink and Pn-P-Sink-To-Counter are foundational pnp
#     (no enclosure to open) and seed the more complex pnp variants
# ---------------------------------------------------------------------------

DEPS: dict[str, list[str]] = {
    # ---- Pure leaves (foundational) ----
    "RoboCasa-Navigate-Kitchen-v0":          [],
    "RoboCasa-Open-Drawer-v0":               [],
    "RoboCasa-Close-Drawer-v0":              [],
    "RoboCasa-Open-Single-Door-v0":          [],
    "RoboCasa-Close-Single-Door-v0":         [],
    "RoboCasa-Microwave-Press-Button-v0":    [],
    "RoboCasa-Coffee-Press-Button-v0":       [],
    "RoboCasa-Manipulate-Sink-Faucet-v0":    [],
    "RoboCasa-Manipulate-Stove-Knob-v0":     [],

    # ---- Door variants ----
    # Generic Open-Door / Close-Door / Manipulate-Door learn from the specific
    # single-door cases; double-door extends single-door with two-handle coordination.
    "RoboCasa-Open-Double-Door-v0":          ["RoboCasa-Open-Single-Door-v0"],
    "RoboCasa-Close-Double-Door-v0":         ["RoboCasa-Close-Single-Door-v0"],
    "RoboCasa-Open-Door-v0":                 ["RoboCasa-Open-Single-Door-v0",
                                              "RoboCasa-Open-Double-Door-v0"],
    "RoboCasa-Close-Door-v0":                ["RoboCasa-Close-Single-Door-v0",
                                              "RoboCasa-Close-Double-Door-v0"],
    "RoboCasa-Manipulate-Door-v0":           ["RoboCasa-Open-Single-Door-v0",
                                              "RoboCasa-Close-Single-Door-v0"],

    # ---- Microwave controls ----
    "RoboCasa-Turn-On-Microwave-v0":         ["RoboCasa-Microwave-Press-Button-v0"],
    "RoboCasa-Turn-Off-Microwave-v0":        ["RoboCasa-Microwave-Press-Button-v0"],

    # ---- Stove controls ----
    "RoboCasa-Turn-On-Stove-v0":             ["RoboCasa-Manipulate-Stove-Knob-v0"],
    "RoboCasa-Turn-Off-Stove-v0":            ["RoboCasa-Manipulate-Stove-Knob-v0"],

    # ---- Sink controls ----
    "RoboCasa-Turn-On-Sink-Faucet-v0":       ["RoboCasa-Manipulate-Sink-Faucet-v0"],
    "RoboCasa-Turn-Off-Sink-Faucet-v0":      ["RoboCasa-Manipulate-Sink-Faucet-v0"],
    "RoboCasa-Turn-Sink-Spout-v0":           ["RoboCasa-Manipulate-Sink-Faucet-v0"],

    # ---- Pure pick-and-place (no enclosure to open) ----
    # Counter-To-Sink is the canonical "pnp into open container" — set as a
    # foundation for the others. Sink-To-Counter is its mirror.
    "RoboCasa-Pn-P-Counter-To-Sink-v0":      [],
    "RoboCasa-Pn-P-Sink-To-Counter-v0":      ["RoboCasa-Pn-P-Counter-To-Sink-v0"],
    "RoboCasa-Pn-P-Counter-To-Stove-v0":     ["RoboCasa-Pn-P-Counter-To-Sink-v0"],
    "RoboCasa-Pn-P-Stove-To-Counter-v0":     ["RoboCasa-Pn-P-Sink-To-Counter-v0"],

    # ---- Coffee-related ----
    "RoboCasa-Coffee-Setup-Mug-v0":          ["RoboCasa-Pn-P-Counter-To-Sink-v0"],
    "RoboCasa-Coffee-Serve-Mug-v0":          ["RoboCasa-Pn-P-Sink-To-Counter-v0"],
    "RoboCasa-Pn-P-Coffee-v0":               ["RoboCasa-Coffee-Setup-Mug-v0",
                                              "RoboCasa-Coffee-Serve-Mug-v0"],

    # ---- pnp through closed fixture ----
    # Cabinet variants: need to open cabinet door, do pnp, close.
    "RoboCasa-Pn-P-Counter-To-Cab-v0":       ["RoboCasa-Open-Single-Door-v0",
                                              "RoboCasa-Pn-P-Counter-To-Sink-v0",
                                              "RoboCasa-Close-Single-Door-v0"],
    "RoboCasa-Pn-P-Cab-To-Counter-v0":       ["RoboCasa-Open-Single-Door-v0",
                                              "RoboCasa-Pn-P-Sink-To-Counter-v0",
                                              "RoboCasa-Close-Single-Door-v0"],
    # Microwave variants: no Microwave-open task exists, so dev figures out the
    # open via Manipulate-Door (transferable closure-opening skill) + does pnp.
    "RoboCasa-Pn-P-Counter-To-Microwave-v0": ["RoboCasa-Manipulate-Door-v0",
                                              "RoboCasa-Pn-P-Counter-To-Sink-v0"],
    "RoboCasa-Pn-P-Microwave-To-Counter-v0": ["RoboCasa-Manipulate-Door-v0",
                                              "RoboCasa-Pn-P-Sink-To-Counter-v0"],
}


def slugify(task_env: str) -> str:
    s = task_env
    if s.startswith("RoboCasa-"):
        s = s[len("RoboCasa-"):]
    if s.endswith("-v0"):
        s = s[:-len("-v0")]
    s = s.replace("Pn-P", "pnp")
    return s.lower()


def build_graph() -> dict:
    entries = []
    for env, deps in sorted(DEPS.items()):
        slug = slugify(env)
        entries.append({
            "name": slug,
            "task_env": env,
            "description": (
                f"Solve {env} end-to-end. The robot must complete the scenario "
                f"defined by this sim's _check_success() method. When dependencies "
                f"are satisfied, prefer reusing those skills' implementations as a "
                f"starting point."
            ),
            "dependencies": [slugify(d) for d in deps],
        })

    return {
        "task_source": "/home/truares/文档/maniskill-tidyverse/robocasa_tasks/single_stage/",
        "comment": (
            "Unified graph for all 32 RoboCasa single-stage tasks. Every node is "
            "a real registered task_env. Dependency edges express semantic "
            "transfer — task B builds on the solution for A. Each node carries "
            "its own task_env; the orchestrator must start that task's sim and "
            "use its _check_success() for the mechanical test."
        ),
        "entries": entries,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    out_path: Path = args.out / "graph.json"
    if out_path.exists() and not args.force:
        print(f"refusing to overwrite {out_path} (use --force)")
        return 1

    graph = build_graph()
    args.out.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(graph, indent=2))

    # Stats
    leaves = [e for e in graph["entries"] if not e["dependencies"]]
    others = [e for e in graph["entries"] if e["dependencies"]]
    by_layer: dict[int, list[str]] = {}

    name_to_deps = {e["name"]: e["dependencies"] for e in graph["entries"]}
    layer_of: dict[str, int] = {}

    def compute_layer(n: str) -> int:
        if n in layer_of:
            return layer_of[n]
        deps = name_to_deps[n]
        if not deps:
            layer_of[n] = 0
            return 0
        layer_of[n] = max(compute_layer(d) for d in deps) + 1
        return layer_of[n]

    for e in graph["entries"]:
        L = compute_layer(e["name"])
        by_layer.setdefault(L, []).append(e["name"])

    # Validate dep references
    names = {e["name"] for e in graph["entries"]}
    bad = [(e["name"], d) for e in graph["entries"] for d in e["dependencies"] if d not in names]

    print(f"wrote {out_path}")
    print(f"  total real-task nodes: {len(graph['entries'])}")
    for L in sorted(by_layer):
        print(f"  layer {L}: {len(by_layer[L])} nodes")
    if bad:
        print("\nBROKEN DEPS:")
        for n, d in bad:
            print(f"  {n} -> {d!r}")
        return 1
    print("  all deps resolve ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
