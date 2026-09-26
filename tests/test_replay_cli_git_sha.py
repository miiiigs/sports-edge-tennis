"""Manifest git SHA must come from the executing source, not the process cwd."""

from __future__ import annotations

from pathlib import Path

from sports_edge_tennis.replay import cli


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv(cli.GIT_SHA_ENV, "deadbeefcafe")
    assert cli._git_sha() == "deadbeefcafe"


def test_resolves_from_source_repo_root_not_cwd(monkeypatch):
    monkeypatch.delenv(cli.GIT_SHA_ENV, raising=False)
    captured = {}

    def fake_check_output(args, **kwargs):
        captured["args"] = args
        return "abc123\n"

    monkeypatch.setattr(cli.subprocess, "check_output", fake_check_output)
    monkeypatch.chdir(Path.home())  # arbitrary cwd must not matter

    assert cli._git_sha() == "abc123"
    args = captured["args"]
    assert args[0] == "git" and args[1] == "-C"
    source_root = Path(cli.__file__).resolve().parents[3]
    assert Path(args[2]) == source_root
    assert str(source_root) != str(Path.cwd())


def test_unknown_when_no_repo(monkeypatch):
    monkeypatch.delenv(cli.GIT_SHA_ENV, raising=False)
    monkeypatch.setattr(cli, "_source_repo_root", lambda: None)
    assert cli._git_sha() == "UNKNOWN"
