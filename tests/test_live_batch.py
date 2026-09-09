from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
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

    def fake_run(conn, *, slate_date, api_tennis_key, pinnapi_key, ingest_fixtures, artifacts_dir, **kwargs):
        assert ingest_fixtures is batch.ingest_api_tennis_source_a_fixtures_with_upcoming_status
        seen.append(slate_date)
        return SimpleNamespace(
            slate_date=slate_date.isoformat(),
            status="LIVE_MVP_SUCCESS_NO_CONSENSUS",
            fixture_count=10,
            target_count=2,
            manager_consensus_count=0,
            pinnapi_requests=0,
            prediction_run_id=None,
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
    assert "offered_requests=0" in out
    assert "bets=0" in out
    assert "type_b_value_signals=0" in out


def test_batch_caps_pinnapi_to_one_board_request_per_cycle(monkeypatch, tmp_path):
    seen_keys = []

    monkeypatch.setattr(
        batch,
        "load_secret",
        lambda name: "api-key" if name == "API_TENNIS_API_KEY" else "pinnapi-key",
    )
    monkeypatch.setattr(batch.psycopg, "connect", _Connect())
    monkeypatch.setattr(batch, "ensure_pinnapi_bookmaker_mapping", lambda conn: None)

    def fake_run(conn, *, slate_date, api_tennis_key, pinnapi_key, ingest_fixtures, artifacts_dir, **kwargs):
        seen_keys.append(pinnapi_key)
        board_calls = 1 if slate_date == date(2026, 9, 8) and pinnapi_key else 0
        return SimpleNamespace(
            slate_date=slate_date.isoformat(),
            status="LIVE_MVP_SUCCESS" if board_calls else "LIVE_MVP_SUCCESS_NO_CONSENSUS",
            fixture_count=10,
            target_count=2,
            manager_consensus_count=1 if board_calls else 0,
            pinnapi_requests=board_calls,
            prediction_run_id=None,
        )

    monkeypatch.setattr(batch, "run_live_mvp", fake_run)

    rc = batch.main([
        "--start-date", "2026-09-07",
        "--days", "3",
        "--dsn", "postgresql://example",
        "--artifacts-dir", str(tmp_path),
    ])

    assert rc == 0
    assert seen_keys == ["pinnapi-key", "pinnapi-key", None]


def test_batch_fetches_full_slate_odds_and_evaluates_type_a_and_type_b(monkeypatch, tmp_path, capsys):
    received_at = datetime(2026, 9, 8, 1, 0, tzinfo=timezone.utc)
    market_runs = []

    monkeypatch.setattr(
        batch,
        "load_secret",
        lambda name: "api-key" if name == "API_TENNIS_API_KEY" else "pinnapi-key",
    )
    monkeypatch.setattr(batch.psycopg, "connect", _Connect())
    monkeypatch.setattr(batch, "ensure_pinnapi_bookmaker_mapping", lambda conn: None)
    monkeypatch.setattr(batch, "_load_market_candidate_match_ids", lambda conn, run_ids: (101, 202))
    monkeypatch.setattr(
        batch,
        "_load_manager_reason_counts",
        lambda conn, run_id: (("CONSENSUS", "NONE", 1),),
    )
    monkeypatch.setattr(
        batch,
        "_load_market_reason_counts",
        lambda conn, run_id: (("TYPE_A_MODEL_EDGE", "NO_BET", "EDGE_BELOW_THRESHOLD", 1),),
    )
    monkeypatch.setattr(
        batch,
        "fetch_pinnapi_board",
        lambda key: (received_at, {"events": []}),
    )
    monkeypatch.setattr(
        batch,
        "normalize_pinnapi_board_for_matches",
        lambda conn, **kwargs: SimpleNamespace(
            candidate_matches=2,
            board_events=2,
            resolved_events=2,
            normalized_events=2,
            rejected_events=0,
        ),
    )
    monkeypatch.setattr(
        batch,
        "fetch_api_tennis_odds",
        lambda api_key, start_date, stop_date: (received_at, {"event": {}}),
    )
    monkeypatch.setattr(
        batch,
        "ingest_api_tennis_offered_odds",
        lambda conn, **kwargs: SimpleNamespace(
            candidate_matches=2,
            events_with_odds=2,
            normalized_quotes=8,
            missing_events=0,
            bookmaker_count=2,
        ),
    )
    policy = SimpleNamespace(market_policy_version_id=77)
    monkeypatch.setattr(batch, "ensure_market_policy_version_v1", lambda conn: policy)

    def fake_market(conn, **kwargs):
        market_runs.append(kwargs["prediction_run_id"])
        return SimpleNamespace(status="PERSISTED", new_decision_count=2, qualified_bet_count=1)

    monkeypatch.setattr(batch, "evaluate_and_persist_market_slate", fake_market)
    monkeypatch.setattr(
        batch,
        "scan_type_b_value_signals",
        lambda conn, **kwargs: (
            SimpleNamespace(
                match_id=202,
                selection_side=2,
                bookmaker_code="SOFT_A",
                offered_price=Decimal("2.20"),
                sharp_fair_odds=Decimal("2.00"),
                edge_pct=Decimal("0.10"),
                threshold=Decimal("0.05"),
                min_acceptable_odds=Decimal("2.10"),
            ),
        ),
    )

    counter = {"value": 0}

    def fake_run(conn, *, slate_date, **kwargs):
        counter["value"] += 1
        return SimpleNamespace(
            slate_date=slate_date.isoformat(),
            status="LIVE_MVP_SUCCESS",
            fixture_count=10,
            target_count=2,
            manager_consensus_count=1,
            pinnapi_requests=1 if counter["value"] == 1 else 0,
            prediction_run_id=counter["value"],
        )

    monkeypatch.setattr(batch, "run_live_mvp", fake_run)

    rc = batch.main([
        "--start-date", "2026-09-08",
        "--days", "3",
        "--dsn", "postgresql://example",
        "--artifacts-dir", str(tmp_path),
    ])

    assert rc == 0
    assert market_runs == [1, 2, 3]
    out = capsys.readouterr().out
    assert "SHARP_SLATE candidates=2" in out
    assert "MANAGER_REASONS" in out
    assert "CONSENSUS:NONE=1" in out
    assert "MARKET_REASONS" in out
    assert "TYPE_A_MODEL_EDGE:NO_BET:EDGE_BELOW_THRESHOLD=1" in out
    assert "offered_requests=1" in out
    assert "offered_quotes=8" in out
    assert "TYPE_B_VALUE match_id=202 side=2" in out
    assert "TYPE_B_VALUE_SUMMARY candidates=2 signals=1" in out
    assert "bets=3" in out
    assert "type_b_value_signals=1" in out


def test_type_b_scans_even_when_manager_has_zero_consensus(monkeypatch, tmp_path, capsys):
    received_at = datetime(2026, 9, 9, 1, 0, tzinfo=timezone.utc)
    external_board_calls = []

    monkeypatch.setattr(
        batch,
        "load_secret",
        lambda name: "api-key" if name == "API_TENNIS_API_KEY" else "pinnapi-key",
    )
    monkeypatch.setattr(batch.psycopg, "connect", _Connect())
    monkeypatch.setattr(batch, "ensure_pinnapi_bookmaker_mapping", lambda conn: None)
    monkeypatch.setattr(batch, "_load_market_candidate_match_ids", lambda conn, run_ids: (101,))
    monkeypatch.setattr(batch, "_load_manager_reason_counts", lambda conn, run_id: (("NO_BET", "DIRECTIONAL_DISAGREEMENT", 1),))
    monkeypatch.setattr(batch, "_load_market_reason_counts", lambda conn, run_id: ())

    def fetch_board(key):
        external_board_calls.append(key)
        return received_at, {"events": []}

    monkeypatch.setattr(batch, "fetch_pinnapi_board", fetch_board)
    monkeypatch.setattr(
        batch,
        "normalize_pinnapi_board_for_matches",
        lambda conn, **kwargs: SimpleNamespace(
            candidate_matches=1,
            board_events=1,
            resolved_events=1,
            normalized_events=1,
            rejected_events=0,
        ),
    )
    monkeypatch.setattr(batch, "fetch_api_tennis_odds", lambda *args: (received_at, {}))
    monkeypatch.setattr(
        batch,
        "ingest_api_tennis_offered_odds",
        lambda conn, **kwargs: SimpleNamespace(
            candidate_matches=1,
            events_with_odds=1,
            normalized_quotes=4,
            missing_events=0,
            bookmaker_count=2,
        ),
    )
    policy = SimpleNamespace(market_policy_version_id=77)
    monkeypatch.setattr(batch, "ensure_market_policy_version_v1", lambda conn: policy)
    monkeypatch.setattr(
        batch,
        "evaluate_and_persist_market_slate",
        lambda conn, **kwargs: SimpleNamespace(status="PERSISTED", new_decision_count=1, qualified_bet_count=0),
    )
    monkeypatch.setattr(
        batch,
        "scan_type_b_value_signals",
        lambda conn, **kwargs: (
            SimpleNamespace(
                match_id=101,
                selection_side=1,
                bookmaker_code="SOFT_A",
                offered_price=Decimal("2.20"),
                sharp_fair_odds=Decimal("2.00"),
                edge_pct=Decimal("0.10"),
                threshold=Decimal("0.05"),
                min_acceptable_odds=Decimal("2.10"),
            ),
        ),
    )
    monkeypatch.setattr(
        batch,
        "run_live_mvp",
        lambda conn, slate_date, **kwargs: SimpleNamespace(
            slate_date=slate_date.isoformat(),
            status="LIVE_MVP_SUCCESS_NO_CONSENSUS",
            fixture_count=10,
            target_count=1,
            manager_consensus_count=0,
            pinnapi_requests=0,
            prediction_run_id=1,
        ),
    )

    rc = batch.main([
        "--start-date", "2026-09-09",
        "--days", "1",
        "--dsn", "postgresql://example",
        "--artifacts-dir", str(tmp_path),
    ])

    assert rc == 0
    assert external_board_calls == ["pinnapi-key"]
    out = capsys.readouterr().out
    assert "consensus=0" in out
    assert "SHARP_SLATE candidates=1" in out
    assert "TYPE_B_VALUE_SUMMARY candidates=1 signals=1" in out


def test_batch_rejects_unbounded_horizon(monkeypatch, capsys):
    rc = batch.main(["--days", "8", "--dsn", "postgresql://example"])
    assert rc == 2
    assert "BLOCKED_INVALID_HORIZON" in capsys.readouterr().out


def test_batch_requires_api_tennis_key(monkeypatch, capsys):
    monkeypatch.setattr(batch, "load_secret", lambda _name: None)
    rc = batch.main(["--days", "1", "--dsn", "postgresql://example"])
    assert rc == 2
    assert "BLOCKED_MISSING_API_TENNIS_KEY" in capsys.readouterr().out
