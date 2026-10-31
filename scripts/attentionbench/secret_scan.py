#!/usr/bin/env python3
"""Scan every commit in a publish range for locally known secrets."""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def git(repo, *args, check=True):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if check and result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or f"git {args[0]} failed")
    return result


def source_paths(extra):
    development = os.environ.get("ATTENTIONBENCH_DEV", str(Path.home() / "桌面/AttentionBench-Workspace/development"))
    defaults = ["~/.litellm-key", "~/.codex/auth.json", "~/.config/attentionbench-8769/*.env",
                str(Path(development) / "memory-m3/keys.env"),
                # Also scanned by the 10-31 audit: Claude, .dsh and Langfuse credentials.
                "~/.claude/.credentials.json", "~/.dsh/.credentials.yaml", "~/langfuse/.env"]
    defaults += [item for item in os.environ.get("ATTENTIONBENCH_EXTRA_SECRET_FILES", "").split(":") if item]
    defaults += extra
    for spec in defaults:
        expanded = str(Path(spec).expanduser())
        matches = glob.glob(expanded) if glob.has_magic(expanded) else [expanded]
        if not matches:
            print(f"absent: {expanded}")
        for match in matches:
            path = Path(match)
            if path.is_file():
                yield path
            else:
                print(f"absent: {path}")


def json_values(value, key=""):
    if isinstance(value, dict):
        for child_key, child in value.items():
            yield from json_values(child, str(child_key))
    elif isinstance(value, list):
        for child in value:
            yield from json_values(child, key)
    elif isinstance(value, str):
        if re.search(r"token|key|secret|password", key, re.I) or value.startswith("sk-") or re.fullmatch(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", value):
            yield value


def extract(path):
    content = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".env" or path.name == ".env":
        for line in content.splitlines():
            match = re.match(r"\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*(.*?)\s*$", line)
            if match:
                yield match.group(1).strip().strip('"\'')
    elif path.suffix == ".json":
        yield from json_values(json.loads(content))
    elif path.suffix in (".yaml", ".yml"):
        for line in content.splitlines():
            match = re.match(r"\s*([A-Za-z0-9_.-]+)\s*:\s*(.+?)\s*$", line)
            if match and re.search(r"token|key|secret|password", match.group(1), re.I):
                yield match.group(2).strip().strip('"\'')
    elif len(content.splitlines()) == 1:
        yield content.strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--range", dest="revision_range", required=True)
    parser.add_argument("--secret-file", action="append", default=[])
    args = parser.parse_args(argv)
    patterns = []
    seen = set()
    try:
        for source in source_paths(args.secret_file):
            for value in extract(source):
                if len(value) >= 12 and "\n" not in value and "\r" not in value and value not in seen:
                    patterns.append((value, str(source)))
                    seen.add(value)
        if not patterns:
            print("ERROR secret scan: zero patterns loaded", file=sys.stderr)
            return 2
        commits = git(args.repo, "rev-list", args.revision_range).stdout.decode().splitlines()
        hits = 0
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix="attentionbench-patterns-", delete=True) as pattern_file:
            os.chmod(pattern_file.name, 0o600)
            for pattern, _ in patterns:
                pattern_file.write(pattern + "\n")
            pattern_file.flush()
            for commit in commits:
                grep = git(args.repo, "grep", "-F", "-l", "-f", pattern_file.name, commit, "--", check=False)
                if grep.returncode not in (0, 1):
                    raise RuntimeError(grep.stderr.decode(errors="replace").strip() or "git grep failed")
                files = [line.decode(errors="replace").split(":", 1)[1] for line in grep.stdout.splitlines()]
                for file in files:
                    content = git(args.repo, "show", f"{commit}:{file}").stdout
                    for index, (pattern, source) in enumerate(patterns, 1):
                        if pattern.encode() in content:
                            print(f"{commit} {file} pattern#{index} {source}")
                            hits += 1
                message = git(args.repo, "log", "-1", "--format=%B", commit).stdout
                for index, (pattern, source) in enumerate(patterns, 1):
                    if pattern.encode() in message:
                        print(f"{commit} <message> pattern#{index} {source}")
                        hits += 1
        print(f"secret scan: {len(commits)} commits, {len(patterns)} patterns, {hits} hits")
        return 1 if hits else 0
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"ERROR secret scan: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
