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


def _result(slate_date: date, *, prediction_run_id: int | None, targets: int = 0, consensus: int = 0):
    return SimpleNamespace(
        slate_date=slate_date.isoformat(),
        status="LIVE_MVP_SUCCESS" if targets else "LIVE_MVP_SUCCESS_EMPTY_SLATE",
        fixture_count=10 if targets else 0,
        fixture_rows_seen=10 if targets else 0,
        fixture_rows_ingested=10 if targets else 0,
        fixture_rows_rejected=0,
        fixture_rejection_reasons=(),
        target_count=targets,
        manager_consensus_count=consensus,
        pinnapi_requests=0,
        prediction_run_id=prediction_run_id,
    )


def test_later_api_tennis_failure_does_not_discard_successful_market_run(monkeypatch, tmp_path, capsys):
    market_runs: list[int] = []
    summaries: list[dict[str, int]] = []

    monkeypatch.setattr(batch, "load_secret", lambda name: "api-key" if name == "API_TENNIS_API_KEY" else None)
    monkeypatch.setattr(batch.psycopg, "connect", _Connect())
    monkeypatch.setattr(batch, "_load_manager_reason_counts", lambda conn, run_id: ())
    monkeypatch.setattr(batch, "load_price_scanner_candidate_match_ids", lambda conn, **kwargs: ())
    monkeypatch.setattr(batch, "_load_market_reason_counts", lambda conn, run_id: ())
    monkeypatch.setattr(
        batch,
        "ensure_market_policy_version_v2",
        lambda conn: SimpleNamespace(market_policy_version_id=77),
    )

    def fake_market(conn, **kwargs):
        market_runs.append(kwargs["prediction_run_id"])
        return SimpleNamespace(status="PERSISTED", new_decision_count=1, qualified_bet_count=0)

    monkeypatch.setattr(batch, "evaluate_and_persist_market_slate", fake_market)
    monkeypatch.setattr(batch, "scan_type_b_value_signals", lambda conn, **kwargs: ())

    def fake_run(conn, *, slate_date, **kwargs):
        if slate_date == date(2026, 9, 10):
            return _result(slate_date, prediction_run_id=60, targets=29, consensus=7)
        if slate_date == date(2026, 9, 11):
            return _result(slate_date, prediction_run_id=None)
        raise batch.LivePilotApiTennisFailed("api-tennis response missing result list")

    monkeypatch.setattr(batch, "run_live_mvp", fake_run)

    rc = batch.main(
        [
            "--start-date", "2026-09-10",
            "--days", "3",
            "--dsn", "postgresql://example",
            "--artifacts-dir", str(tmp_path),
        ],
        summary_sink=summaries.append,
    )

    assert rc == 0
    assert market_runs == [60]
    out = capsys.readouterr().out
    assert "SLATE_UNAVAILABLE date=2026-09-12 result_code=LIVE_PILOT_API_TENNIS_FAILED" in out
    assert "MARKET prediction_run_id=60 status=PERSISTED" in out
    assert "LIVE_BATCH_SUCCESS" in out
    assert "slates=2" in out
    assert "failed_slates=1" in out
    assert summaries == [
        {
            "slates": 2,
            "failed_slates": 1,
            "fixtures": 10,
            "targets": 29,
            "consensus": 7,
            "price_candidates": 0,
            "pinnapi_requests": 0,
            "offered_requests": 0,
            "offered_quotes": 0,
            "oddspapi_events": 0,
            "oddspapi_exact": 0,
            "oddspapi_ambiguous": 0,
            "oddspapi_conflict": 0,
            "bets": 0,
            "type_b_value_signals": 0,
            "close_finalized": 0,
            "clv_calculated": 0,
        }
    ]


def test_all_api_tennis_slates_failed_still_fails_closed(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(batch, "load_secret", lambda name: "api-key" if name == "API_TENNIS_API_KEY" else None)
    monkeypatch.setattr(batch.psycopg, "connect", _Connect())
    monkeypatch.setattr(
        batch,
        "run_live_mvp",
        lambda conn, slate_date, **kwargs: (_ for _ in ()).throw(
            batch.LivePilotApiTennisFailed("api-tennis response missing result list")
        ),
    )

    rc = batch.main([
        "--start-date", "2026-09-10",
        "--days", "3",
        "--dsn", "postgresql://example",
        "--artifacts-dir", str(tmp_path),
    ])

    assert rc == 2
    out = capsys.readouterr().out
    assert out.count("SLATE_UNAVAILABLE") == 3
    assert "LIVE_BATCH_FAILED_NO_SUCCESSFUL_SLATES" in out
    assert "LIVE_BATCH_SUCCESS" not in out
