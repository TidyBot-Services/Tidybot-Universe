import json
import os
import subprocess
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]


def git(*args, cwd=None):
    return subprocess.run(["git", *map(str, args)], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def setup_repo(tmp_path):
    bare = tmp_path / "origin.git"
    repo = tmp_path / "work"
    git("init", "--bare", bare)
    git("init", "-b", "main", repo)
    git("config", "user.email", "test@example.com", cwd=repo)
    git("config", "user.name", "Test", cwd=repo)
    git("remote", "add", "origin", bare, cwd=repo)
    (repo / "file.txt").write_text("first\n")
    git("add", "file.txt", cwd=repo)
    git("commit", "-m", "first", cwd=repo)
    git("push", "-u", "origin", "main", cwd=repo)
    git("--git-dir", bare, "symbolic-ref", "HEAD", "refs/heads/main")
    return repo, bare


def commit(repo, text, message="change"):
    (repo / "file.txt").write_text(text)
    git("add", "file.txt", cwd=repo)
    git("commit", "-m", message, cwd=repo)


def scan(repo, secret_file, revision_range, env):
    return subprocess.run([sys.executable, str(SCRIPTS / "secret_scan.py"), "--repo", str(repo),
                           "--range", revision_range, "--secret-file", str(secret_file)],
                          capture_output=True, text=True, env=env)


def clean_env(tmp_path):
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "empty-home")
    env["ATTENTIONBENCH_DEV"] = str(tmp_path / "empty-development")
    env.pop("ATTENTIONBENCH_EXTRA_SECRET_FILES", None)
    return env


def manifest(tmp_path, repo, name="test-repo"):
    file = tmp_path / "manifest.json"
    file.write_text(json.dumps([{"name": name, "path": str(repo), "remote": "origin", "branch": "main"}]))
    return file


def publish(file, env, push=False):
    args = ["bash", str(SCRIPTS / "publish.sh"), "--manifest", str(file)]
    if push:
        args.append("--push")
    return subprocess.run(args, capture_output=True, text=True, env=env)


def test_secret_in_old_commit_in_range(tmp_path):
    repo, _ = setup_repo(tmp_path)
    secret = "sk-test-secret-123456789"
    source = tmp_path / "secret.txt"
    source.write_text(secret)
    commit(repo, secret, "secret entered")
    commit(repo, "clean current tree\n", "removed")
    result = scan(repo, source, "origin/main..main", clean_env(tmp_path))
    assert result.returncode == 1
    assert "pattern#1" in result.stdout
    assert secret not in result.stdout + result.stderr


def test_secret_only_before_range(tmp_path):
    repo, _ = setup_repo(tmp_path)
    secret = "sk-test-secret-123456789"
    source = tmp_path / "secret.txt"
    source.write_text(secret)
    commit(repo, secret)
    git("push", "origin", "main", cwd=repo)
    git("fetch", "origin", "main", cwd=repo)
    commit(repo, "clean later\n")
    result = scan(repo, source, "origin/main..main", clean_env(tmp_path))
    assert result.returncode == 0
    assert "1 commits, 1 patterns, 0 hits" in result.stdout


def test_zero_patterns_fails_closed(tmp_path):
    repo, _ = setup_repo(tmp_path)
    source = tmp_path / "empty.txt"
    source.write_text("")
    result = scan(repo, source, "main", clean_env(tmp_path))
    assert result.returncode == 2
    assert "zero patterns" in result.stderr


def test_non_fast_forward_refused(tmp_path):
    repo, bare = setup_repo(tmp_path)
    source = tmp_path / "secret.txt"
    source.write_text("safe-test-pattern-123456")
    commit(repo, "local divergence\n")
    other = tmp_path / "other"
    git("clone", bare, other)
    git("config", "user.email", "test@example.com", cwd=other)
    git("config", "user.name", "Test", cwd=other)
    commit(other, "remote divergence\n")
    git("push", "origin", "main", cwd=other)
    env = clean_env(tmp_path)
    env["ATTENTIONBENCH_EXTRA_SECRET_FILES"] = str(source)
    result = publish(manifest(tmp_path, repo), env, push=True)
    assert result.returncode == 1
    assert "not a fast-forward; never force push" in result.stderr
    assert git("--git-dir", bare, "rev-parse", "refs/heads/main") == git("rev-parse", "HEAD", cwd=other)


def test_push_and_verify_local_bare(tmp_path):
    repo, bare = setup_repo(tmp_path)
    source = tmp_path / "secret.txt"
    source.write_text("safe-test-pattern-123456")
    commit(repo, "ready to publish\n")
    env = clean_env(tmp_path)
    env["ATTENTIONBENCH_EXTRA_SECRET_FILES"] = str(source)
    result = publish(manifest(tmp_path, repo), env, push=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pushed" in result.stdout
    assert git("--git-dir", bare, "rev-parse", "refs/heads/main") == git("rev-parse", "HEAD", cwd=repo)
