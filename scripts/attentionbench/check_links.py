#!/usr/bin/env python3
"""Check local Markdown links and AttentionBench GitHub references."""

import argparse
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen


DEFAULT_FILES = ("README.md", "GETTING_STARTED.md", "docs/attentionbench_project_guide.md")
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\((<?[^\s)]+>?)\)")
AUTOLINK = re.compile(r"<https?://[^>]+>")
GITHUB_LINK = re.compile(r"^https://github\.com/TidyBot-Services/([^/]+)/(tree|blob)/(.+)$")
GITHUB_ARCHIVE = re.compile(r"^https://github\.com/TidyBot-Services/([^/]+)/archive/refs/heads/(.+)\.zip$")


class RemoteError(Exception):
    def __init__(self, repo, reason):
        self.repo = repo
        self.reason = reason
        super().__init__(reason)


def ls_remote(repo):
    result = subprocess.run(
        ["git", "ls-remote", f"https://github.com/TidyBot-Services/{repo}.git"],
        text=True, capture_output=True, check=False,
    )
    if result.returncode:
        raise RemoteError(repo, result.stderr.strip() or f"exit {result.returncode}")
    return result.stdout


def heading_slug(heading):
    heading = re.sub(r"\s+#+\s*$", "", heading.strip()).lower()
    return "".join(
        char if char in "-_" or char.isspace() or unicodedata.category(char)[0] in "LN" else ""
        for char in heading
    ).replace(" ", "-")


def headings(path):
    counts = {}
    slugs = set()
    in_fence = False
    fence_char = ""
    fence_len = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        fence = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence, fence_char, fence_len = True, marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_len:
                in_fence = False
            continue
        if in_fence:
            continue
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        if match:
            base = heading_slug(match.group(1))
            count = counts.get(base, 0)
            slugs.add(f"{base}-{count}" if count else base)
            counts[base] = count + 1
    return slugs


def links(path):
    in_fence = False
    fence_char = ""
    fence_len = 0
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fence = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence, fence_char, fence_len = True, marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_len:
                in_fence = False
            continue
        if in_fence:
            continue
        found = [(m.start(), m.group(1).strip("<>")) for m in MARKDOWN_LINK.finditer(line)]
        found += [(m.start(), m.group()[1:-1]) for m in AUTOLINK.finditer(line)]
        for _, target in sorted(found):
            yield number, target


def remote_refs(repo, cache, remote_lookup):
    if repo not in cache:
        try:
            output = remote_lookup(repo)
        except RemoteError:
            raise
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RemoteError(repo, getattr(exc, "stderr", None) or str(exc)) from exc
        refs = set()
        for line in output.splitlines():
            parts = line.split()
            if len(parts) == 2:
                for prefix in ("refs/heads/", "refs/tags/"):
                    if parts[1].startswith(prefix):
                        refs.add(parts[1][len(prefix):].removesuffix("^{}"))
        cache[repo] = refs
    return cache[repo]


def external_ok(target):
    for method in ("HEAD", "GET"):
        try:
            with urlopen(Request(target, method=method), timeout=15) as response:
                if 200 <= response.status < 400:
                    return True
                if method == "HEAD":
                    continue
                return False
        except (HTTPError, URLError, TimeoutError, ValueError):
            if method == "GET":
                return False
    return False


def main(argv=None, remote_lookup=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--external", action="store_true")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()
    cache = {}
    remote_lookup = remote_lookup or ls_remote
    checked = dead = skipped = 0
    branch = None
    result = subprocess.run(["git", "-C", str(root), "branch", "--show-current"],
                            text=True, capture_output=True, check=False)
    if result.returncode == 0:
        branch = result.stdout.strip()
    for name in args.files or DEFAULT_FILES:
        source = root / name
        display = name
        if not source.is_file():
            checked += 1
            dead += 1
            print(f"{display}:1: DEAD {name} (markdown file missing)")
            continue
        for number, target in links(source):
            checked += 1
            parsed = urlsplit(target)
            reason = None
            github = GITHUB_LINK.match(target)
            archive = GITHUB_ARCHIVE.match(target)
            if github or archive:
                if args.offline:
                    skipped += 1
                    print(f"{display}:{number}: skipped (offline) {target}")
                    continue
                repo = (github or archive).group(1)
                try:
                    refs = remote_refs(repo, cache, remote_lookup)
                except RemoteError as exc:
                    print(f"ERROR ls-remote {exc.repo}: {exc.reason}", file=sys.stderr)
                    return 2
                remainder = github.group(3) if github else archive.group(2)
                remainder = unquote(remainder.split("?", 1)[0].split("#", 1)[0])
                ref = max((r for r in refs if remainder == r or remainder.startswith(r + "/")),
                          key=len, default=None)
                if ref is None:
                    reason = "missing ref"
                elif github:
                    relative = remainder[len(ref):].lstrip("/")
                    if not relative and github.group(2) == "blob":
                        reason = "missing path"
                    elif ref == branch and not (root / unquote(relative)).exists():
                        reason = "local path missing"
            elif parsed.scheme in ("http", "https"):
                if not args.external:
                    skipped += 1
                    print(f"{display}:{number}: not checked {target}")
                    continue
                if not external_ok(target):
                    reason = "HTTP check failed"
            elif parsed.scheme or target.startswith("//"):
                skipped += 1
                print(f"{display}:{number}: not checked {target}")
                continue
            else:
                path = source if not parsed.path else (source.parent / unquote(parsed.path))
                if not path.exists():
                    reason = "path missing"
                elif parsed.fragment and path.is_file() and path.suffix.lower() in (".md", ".markdown"):
                    if unquote(parsed.fragment).lower() not in headings(path):
                        reason = "anchor missing"
            if reason:
                dead += 1
                print(f"{display}:{number}: DEAD {target} ({reason})")
    print(f"checked={checked} dead={dead} skipped={skipped}")
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main())
