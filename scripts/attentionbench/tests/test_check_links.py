import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "check_links.py"
spec = importlib.util.spec_from_file_location("check_links", SCRIPT)
check_links = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_links)


def run(tmp_path, capsys, markdown, lookup=None, *options):
    (tmp_path / "README.md").write_text(markdown, encoding="utf-8")
    code = check_links.main(["--repo-root", str(tmp_path), *options, "README.md"], remote_lookup=lookup)
    return code, capsys.readouterr()


def test_dead_relative_link(tmp_path, capsys):
    code, output = run(tmp_path, capsys, "[missing](nowhere.md)")
    assert code == 1
    assert "README.md:1: DEAD nowhere.md (path missing)" in output.out
    assert "checked=1 dead=1 skipped=0" in output.out


def test_dead_anchor(tmp_path, capsys):
    code, output = run(tmp_path, capsys, "# Present\n[missing](#absent)")
    assert code == 1
    assert "anchor missing" in output.out


def test_cjk_and_duplicate_heading_anchors(tmp_path, capsys):
    code, output = run(tmp_path, capsys, "# 中文 标题\n# 中文 标题\n[one](#中文-标题) [two](#中文-标题-1)")
    assert code == 0
    assert "checked=2 dead=0 skipped=0" in output.out


def test_github_branch_with_slash_and_missing_ref(tmp_path, capsys):
    refs = "abc\trefs/heads/attentionbench/baseline-20261015\n"
    lookup = lambda repo: refs
    ok = "[ok](https://github.com/TidyBot-Services/example/tree/attentionbench/baseline-20261015/docs) [root](https://github.com/TidyBot-Services/example/tree/attentionbench/baseline-20261015)"
    bad = "[bad](https://github.com/TidyBot-Services/example/blob/missing/ref/file.md)"
    code, output = run(tmp_path, capsys, ok + "\n" + bad, lookup)
    assert code == 1
    assert "missing ref" in output.out
    assert "checked=3 dead=1 skipped=0" in output.out


def test_archive_ref_with_slash_and_remote_cache(tmp_path, capsys):
    calls = []
    def lookup(repo):
        calls.append(repo)
        return "abc\trefs/heads/attentionbench/baseline-20261015\n"
    archive = "<https://github.com/TidyBot-Services/example/archive/refs/heads/attentionbench/baseline-20261015.zip>"
    code, output = run(tmp_path, capsys, archive + "\n" + archive, lookup)
    assert code == 0
    assert calls == ["example"]
    assert "checked=2 dead=0 skipped=0" in output.out


def test_offline_skips_remote(tmp_path, capsys):
    def forbidden(_):
        raise AssertionError("remote lookup called")
    code, output = run(tmp_path, capsys, "<https://github.com/TidyBot-Services/example/archive/refs/heads/main.zip>", forbidden, "--offline")
    assert code == 0
    assert "skipped (offline)" in output.out
    assert "checked=1 dead=0 skipped=1" in output.out


def test_remote_failure_exits_two(tmp_path, capsys):
    def broken(repo):
        raise check_links.RemoteError(repo, "network unavailable")
    code, output = run(tmp_path, capsys, "[x](https://github.com/TidyBot-Services/example/tree/main/docs)", broken)
    assert code == 2
    assert "ERROR ls-remote example: network unavailable" in output.err


def test_fenced_code_is_skipped(tmp_path, capsys):
    code, output = run(tmp_path, capsys, "```md\n[dead](missing.md)\n```\n")
    assert code == 0
    assert "checked=0" in output.out
