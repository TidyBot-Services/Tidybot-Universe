#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
manifest="$script_dir/publish-manifest.json"
push=false

fail() {
  printf 'ERROR %s: %s: %s. Fix: %s\n' "$1" "$2" "$3" "$4" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --push) push=true; shift ;;
    --manifest)
      (($# >= 2)) || fail arguments publish '--manifest needs a file' 'provide a manifest path'
      manifest=$2; shift 2 ;;
    *) fail arguments publish "unknown option $1" 'use [--push] [--manifest FILE]' ;;
  esac
done

entries_file=$(mktemp)
trap 'rm -f "$entries_file"' EXIT
if ! python3 - "$manifest" > "$entries_file" <<'PY'
import json
import os
import sys
from pathlib import Path

try:
    development = os.environ.get("ATTENTIONBENCH_DEV", str(Path.home() / "桌面/AttentionBench-Workspace/development"))
    entries = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("manifest must be a JSON list")
    for entry in entries:
        if not isinstance(entry, dict) or any(not isinstance(entry.get(k), str) or not entry[k] for k in ("name", "path", "remote", "branch")):
            raise ValueError("each entry needs nonempty name, path, remote, branch strings")
        fields = (entry["name"], entry["path"].replace("$ATTENTIONBENCH_DEV", development), entry["remote"], entry["branch"])
        if any("\0" in field for field in fields):
            raise ValueError("manifest fields cannot contain NUL")
        for field in fields:
            sys.stdout.buffer.write(field.encode() + b"\0")
except (OSError, ValueError, json.JSONDecodeError) as exc:
    print(f"manifest error: {exc}", file=sys.stderr)
    sys.exit(1)
PY
then
  fail manifest publish 'cannot load manifest' 'correct the JSON manifest and path'
fi
mapfile -d '' -t entries < "$entries_file"
summary_rows=()
for ((index=0; index<${#entries[@]}; index+=4)); do
  name=${entries[index]}
  path=${entries[index+1]}
  remote=${entries[index+2]}
  branch=${entries[index+3]}
  if ! local_sha=$(git -C "$path" rev-parse --verify "refs/heads/$branch" 2>&1); then
    fail 'verify branch' "$name" "$local_sha" "create local branch $branch in $path"
  fi

  if remote_output=$(git -C "$path" ls-remote --exit-code "$remote" "refs/heads/$branch" 2>&1); then
    remote_sha=${remote_output%%[[:space:]]*}
  else
    code=$?
    if ((code == 2)); then
      remote_sha='-'
    else
      fail 'check remote' "$name" "$remote_output" "check remote $remote and its connectivity"
    fi
  fi

  if [[ $remote_sha != '-' ]]; then
    if ! fetch_output=$(git -C "$path" fetch "$remote" "refs/heads/$branch:refs/remotes/$remote/$branch" 2>&1); then
      fail fetch "$name" "$fetch_output" "check remote $remote and branch $branch"
    fi
    remote_ref="refs/remotes/$remote/$branch"
    if ! git -C "$path" merge-base --is-ancestor "$remote_ref" "refs/heads/$branch"; then
      fail 'fast-forward' "$name" 'not a fast-forward; never force push' 'merge or rebase the remote branch into the local branch'
    fi
    range="$remote_ref..refs/heads/$branch"
  else
    range="refs/heads/$branch"
  fi

  if ! python3 "$script_dir/secret_scan.py" --repo "$path" --range "$range"; then
    fail 'secret scan' "$name" 'scan failed or found a secret' 'remove secrets from the commits in the publish range and provide valid secret sources'
  fi
  if [[ $name == Tidybot-Universe ]]; then
    if ! python3 "$script_dir/check_links.py" --repo-root "$path"; then
      fail 'link check' "$name" 'dead link or remote check failure' 'correct links and retry'
    fi
  fi

  if [[ $push == true ]]; then
    if ! push_output=$(git -C "$path" push "$remote" "$branch:$branch" 2>&1); then
      fail push "$name" "$push_output" 'resolve the remote rejection and retry without force push'
    fi
    if ! verify_output=$(git -C "$path" ls-remote --exit-code "$remote" "refs/heads/$branch" 2>&1); then
      fail 'verify push' "$name" "$verify_output" 'check remote connectivity and branch state'
    fi
    verified_sha=${verify_output%%[[:space:]]*}
    [[ $verified_sha == "$local_sha" ]] || fail 'verify push' "$name" "remote SHA $verified_sha differs from local $local_sha" 'inspect the remote branch before retrying'
    remote_sha=$verified_sha
    status=pushed
  else
    printf '%s: would push %s to %s/%s\n' "$name" "$range" "$remote" "$branch"
    git -C "$path" log --oneline -10 "$range"
    status='dry run'
  fi
  summary_rows+=("$name" "$local_sha" "$remote_sha" "$status")
done
printf '%-28s %-40s %-40s %s\n' name local_sha remote_sha status
for ((index=0; index<${#summary_rows[@]}; index+=4)); do
  printf '%-28s %-40s %-40s %s\n' "${summary_rows[index]}" "${summary_rows[index+1]}" "${summary_rows[index+2]}" "${summary_rows[index+3]}"
done
