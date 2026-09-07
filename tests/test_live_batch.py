from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import sports_edge_tennis.live.batch as batch


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Connect:
    def __call__(self, *args, **kwargs):
        return _Conn()


def test_batch_runs_bounded_three_day_horizon(monkeypatch, tmp_path, capsys):
    seen = []

    monkeypatch.setattr(batch, "load_secret", lambda name: "key" if name == "API_TENNIS_API_KEY" else None)
    monkeypatch.setattr(batch.psycopg, "connect", _Connect())
    monkeypatch.setattr(batch, "ensure_pinnapi_bookmaker_mapping", lambda conn: None)

    def fake_run(conn, *, slate_date, api_tennis_key, pinnapi_key, artifacts_dir):
        seen.append(slate_date)
        return SimpleNamespace(
            slate_date=slate_date.isoformat(),
            status="LIVE_MVP_SUCCESS_NO_CONSENSUS",
            fixture_count=10,
            target_count=2,
            manager_consensus_count=0,
            pinnapi_requests=0,
        )

    monkeypatch.setattr(batch, "run_live_mvp", fake_run)

    rc = batch.main([
        "--start-date", "2026-09-07",
        "--days", "3",
        "--dsn", "postgresql://example",
        "--artifacts-dir", str(tmp_path),
    ])

    assert rc == 0
    assert seen == [date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9)]
    out = capsys.readouterr().out
    assert "LIVE_BATCH_SUCCESS" in out
    assert "slates=3" in out
    assert "pinnapi_requests=0" in out


def test_batch_rejects_unbounded_horizon(monkeypatch, capsys):
    rc = batch.main(["--days", "8", "--dsn", "postgresql://example"])
    assert rc == 2
    assert "BLOCKED_INVALID_HORIZON" in capsys.readouterr().out


def test_batch_requires_api_tennis_key(monkeypatch, capsys):
    monkeypatch.setattr(batch, "load_secret", lambda _name: None)
    rc = batch.main(["--days", "1", "--dsn", "postgresql://example"])
    assert rc == 2
    assert "BLOCKED_MISSING_API_TENNIS_KEY" in capsys.readouterr().out
