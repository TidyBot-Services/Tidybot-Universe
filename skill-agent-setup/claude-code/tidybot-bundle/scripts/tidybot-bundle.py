#!/usr/bin/env python3
"""
tidybot-bundle: Bundle a skill and its dependencies into a single executable script.

Usage:
    tidybot-bundle <skill-name> [--skills-dir DIR] [--output FILE]

Resolves dependencies from deps.txt, topologically sorts them,
and inlines all code into one self-contained Python script.
"""

import argparse
import os
import sys
from pathlib import Path
from collections import deque


DEFAULT_SKILLS_DIR = Path.home() / ".openclaw/workspace/skills"


def find_skill_dir(skill_name: str, skills_dir: Path) -> Path | None:
    """Find a skill directory by name. Supports both old (main.py at root) and new (scripts/main.py) layouts."""
    candidates = [
        skills_dir / skill_name,
        skills_dir / f"{skill_name}-repo",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            if (candidate / "scripts" / "main.py").exists():
                return candidate
            if (candidate / "main.py").exists():
                return candidate
    return None


def _get_main_py(skill_dir: Path) -> Path:
    """Return path to main.py, preferring scripts/ subfolder."""
    scripts_main = skill_dir / "scripts" / "main.py"
    if scripts_main.exists():
        return scripts_main
    return skill_dir / "main.py"


def read_deps(skill_dir: Path) -> list[str]:
    """Read dependencies from deps.txt (checks scripts/ first, then root)."""
    deps_file = skill_dir / "scripts" / "deps.txt"
    if not deps_file.exists():
        deps_file = skill_dir / "deps.txt"
    if not deps_file.exists():
        return []
    
    deps = []
    for line in deps_file.read_text().strip().split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            deps.append(line)
    return deps


def resolve_dependencies(skill_name: str, skills_dir: Path) -> list[str]:
    """
    Resolve all dependencies in topological order (dependencies first).
    Uses BFS + reverse for correct ordering.
    """
    visited = set()
    order = []
    queue = deque([skill_name])
    
    # BFS to collect all deps
    all_deps = {}  # skill -> [deps]
    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        
        skill_dir = find_skill_dir(current, skills_dir)
        if skill_dir is None:
            print(f"WARNING: Skill '{current}' not found, skipping", file=sys.stderr)
            continue
        
        deps = read_deps(skill_dir)
        all_deps[current] = deps
        
        for dep in deps:
            if dep not in visited:
                queue.append(dep)
    
    # Topological sort using DFS
    visited.clear()
    
    def dfs(name):
        if name in visited:
            return
        visited.add(name)
        for dep in all_deps.get(name, []):
            dfs(dep)
        order.append(name)
    
    dfs(skill_name)
    return order  # Dependencies come before dependents


def extract_code(skill_dir: Path, skill_name: str, is_dependency: bool) -> str:
    """
    Extract code from a skill's main.py.
    For dependencies, skip if __name__ == "__main__" blocks.
    """
    main_py = _get_main_py(skill_dir)
    if not main_py.exists():
        return f"# ERROR: {skill_name}/main.py not found\n"
    
    code = main_py.read_text()
    
    if is_dependency:
        # Remove if __name__ == "__main__" block from dependencies
        lines = code.split("\n")
        filtered = []
        skip_main = False
        main_indent = 0
        
        for line in lines:
            # Detect start of main block
            stripped = line.strip()
            if stripped.startswith("if __name__") and "__main__" in stripped:
                skip_main = True
                main_indent = len(line) - len(line.lstrip())
                continue
            
            # If we're skipping, check if we've dedented past the main block
            if skip_main:
                if line.strip() == "":
                    continue  # Skip blank lines in main block
                current_indent = len(line) - len(line.lstrip())
                if current_indent <= main_indent and line.strip():
                    skip_main = False  # Dedented, stop skipping
                else:
                    continue  # Still in main block, skip
            
            filtered.append(line)
        
        code = "\n".join(filtered)
    
    return code


def deduplicate_bundle(code_sections: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """
    Deduplicate imports and function definitions across bundled sections.
    
    Each section is (label, name, code). Returns sections with duplicates removed
    (first occurrence wins).
    """
    import re
    
    seen_imports = set()      # "import x" or "from x import y" lines
    seen_functions = set()    # function names
    
    result = []
    for label, name, code in code_sections:
        lines = code.split("\n")
        filtered = []
        skip_func = False
        func_indent = 0
        
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            
            # Handle imports
            if stripped.startswith(("import ", "from ")) and not stripped.startswith("from ."):
                if stripped in seen_imports:
                    i += 1
                    continue
                seen_imports.add(stripped)
                filtered.append(line)
                i += 1
                continue
            
            # Handle function definitions
            func_match = re.match(r'^(def \w+)\(', stripped)
            if func_match:
                func_sig = func_match.group(1)
                if func_sig in seen_functions:
                    # Skip entire function body
                    base_indent = len(line) - len(line.lstrip())
                    i += 1
                    while i < len(lines):
                        next_line = lines[i]
                        if next_line.strip() == "":
                            i += 1
                            continue
                        next_indent = len(next_line) - len(next_line.lstrip())
                        if next_indent <= base_indent:
                            break
                        i += 1
                    continue
                seen_functions.add(func_sig)
            
            filtered.append(line)
            i += 1
        
        result.append((label, name, "\n".join(filtered)))
    
    return result


def bundle(skill_name: str, skills_dir: Path, call: str | None = None) -> str:
    """Bundle a skill and all dependencies into one script.
    
    Args:
        skill_name: Name of the skill to bundle
        skills_dir: Path to skills directory
        call: Optional function call to append (replaces __main__ block).
              e.g. 'pick_and_place(pick_target="ball", place_target="trash can")'
    """
    order = resolve_dependencies(skill_name, skills_dir)
    
    if not order:
        return f"# ERROR: Could not resolve skill '{skill_name}'\n"
    
    # Collect all code sections
    sections = []
    for i, name in enumerate(order):
        skill_dir = find_skill_dir(name, skills_dir)
        if skill_dir is None:
            sections.append(("ERROR", name, f"# ERROR: Skill '{name}' not found"))
            continue
        
        # When --call is used, treat the main skill like a dependency too
        # (strip its __main__ block since we'll append our own call)
        is_dep = (i < len(order) - 1) or (call is not None)
        label = "DEPENDENCY" if (i < len(order) - 1) else "MAIN"
        code = extract_code(skill_dir, name, is_dependency=is_dep)
        sections.append((label, name, code))
    
    # Deduplicate imports and functions
    sections = deduplicate_bundle(sections)
    
    parts = [
        '"""',
        f"Bundled skill: {skill_name}",
        f"Dependencies: {', '.join(order[:-1]) if len(order) > 1 else 'none'}",
        f"Generated by tidybot-bundle",
        '"""',
        "",
    ]
    
    for label, name, code in sections:
        parts.append("")
        parts.append("# " + "=" * 76)
        parts.append(f"# {label}: {name}")
        parts.append("# " + "=" * 76)
        parts.append("")
        parts.append(code)
    
    # Append custom call if provided
    if call:
        parts.append("")
        parts.append("# " + "=" * 76)
        parts.append("# Entry point (via --call)")
        parts.append("# " + "=" * 76)
        parts.append("")
        parts.append(call)
    
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(
        description="Bundle a TidyBot skill and its dependencies"
    )
    parser.add_argument("skill", help="Skill name to bundle")
    parser.add_argument(
        "--skills-dir", "-d",
        type=Path,
        default=DEFAULT_SKILLS_DIR,
        help=f"Skills directory (default: {DEFAULT_SKILLS_DIR})"
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        help="Output file (default: stdout)"
    )
    parser.add_argument(
        "--call", "-c",
        type=str,
        help="Function call to append as entry point (replaces __main__ block). "
             'e.g. \'pick_and_place(pick_target="ball", place_target="trash")\''
    )
    
    args = parser.parse_args()
    
    if not args.skills_dir.is_dir():
        print(f"ERROR: Skills directory not found: {args.skills_dir}", file=sys.stderr)
        sys.exit(1)
    
    result = bundle(args.skill, args.skills_dir, call=args.call)
    
    if args.output:
        args.output.write_text(result)
        print(f"Bundled to {args.output}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()
