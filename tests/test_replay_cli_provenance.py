from __future__ import annotations

from pathlib import Path

from sports_edge_tennis.replay import cli


def test_git_sha_prefers_explicit_replay_sha(monkeypatch):
    monkeypatch.setenv("SPORTS_EDGE_REPLAY_GIT_SHA", "explicit-sha")
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "railway-sha")

    assert cli._git_sha() == "explicit-sha"


def test_git_sha_uses_railway_commit_sha(monkeypatch):
    monkeypatch.delenv("SPORTS_EDGE_REPLAY_GIT_SHA", raising=False)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "railway-sha")

    assert cli._git_sha() == "railway-sha"


def test_git_sha_resolves_repository_independent_of_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv("SPORTS_EDGE_REPLAY_GIT_SHA", raising=False)
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    calls = []

    def fake_check_output(args, **kwargs):
        calls.append((args, kwargs))
        return "repo-sha\n"

    monkeypatch.setattr(cli.subprocess, "check_output", fake_check_output)
    monkeypatch.chdir(tmp_path)

    assert cli._git_sha() == "repo-sha"
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:2] == ["git", "-C"]
    assert Path(args[2]) == Path(cli.__file__).resolve().parents[3]
    assert args[3:] == ["rev-parse", "HEAD"]
    assert kwargs["text"] is True
