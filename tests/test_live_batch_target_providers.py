from __future__ import annotations

from datetime import UTC, date, datetime
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


def _live_result():
    return SimpleNamespace(
        slate_date="2026-09-19",
        status="LIVE_MVP_SUCCESS",
        fixture_count=1,
        fixture_rows_seen=1,
        fixture_rows_ingested=1,
        fixture_rows_rejected=0,
        fixture_rejection_reasons=(),
        target_count=1,
        manager_consensus_count=1,
        pinnapi_requests=0,
        prediction_run_id=60,
    )


def test_target_mode_uses_only_sportradar_and_goalserve(monkeypatch, tmp_path):
    authority = SimpleNamespace(
        data_mode=batch.TARGET_DATA_MODE,
        odds_mode=batch.TARGET_ODDS_MODE,
    )
    monkeypatch.setattr(batch, "load_provider_authority", lambda env: authority)
    monkeypatch.setattr(batch, "assert_cutover_ready", lambda conn, env: authority)
    monkeypatch.setattr(batch.psycopg, "connect", _Connect())

    secrets = {
        "SPORTRADAR_TENNIS_API_KEY": "sr-key",
        "GOALSERVE_TENNIS_FEED_URL": "https://secret.invalid/feed",
        # Deliberately present: target mode must still not use them.
        "API_TENNIS_API_KEY": "legacy-api-tennis",
        "PINNAPI_KEY": "legacy-pinnapi",
    }
    monkeypatch.setattr(batch, "load_secret", lambda name: secrets.get(name))

    live_calls = []

    def fake_live(**kwargs):
        live_calls.append(kwargs)
        return _live_result()

    monkeypatch.setattr(batch, "run_live_mvp", fake_live)
    monkeypatch.setattr(batch, "_load_manager_reason_counts", lambda *a, **k: ())
    monkeypatch.setattr(
        batch,
        "load_price_scanner_candidate_match_ids",
        lambda *a, **k: (10,),
    )

    def legacy_call_forbidden(*args, **kwargs):
        raise AssertionError("legacy provider call executed in target mode")

    monkeypatch.setattr(batch, "fetch_api_tennis_odds", legacy_call_forbidden)
    monkeypatch.setattr(batch, "fetch_pinnapi_board", legacy_call_forbidden)
    monkeypatch.setattr(batch, "normalize_pinnapi_board_for_matches", legacy_call_forbidden)

    goalserve_received = datetime(2026, 9, 19, 12, 30, tzinfo=UTC)
    goalserve_fetches = []
    monkeypatch.setattr(
        batch,
        "fetch_goalserve_tennis_json",
        lambda url: (
            goalserve_fetches.append(url) or goalserve_received,
            {"scores": {}},
        ),
    )
    goalserve_ingests = []

    def fake_goalserve_ingest(conn, **kwargs):
        goalserve_ingests.append(kwargs)
        return SimpleNamespace(
            candidate_matches=1,
            provider_events_seen=1,
            exact_events=1,
            existing_events=0,
            unmatched_events=0,
            ambiguous_events=0,
            normalized_quotes=4,
            unknown_bookmaker_groups=0,
            unknown_participant_groups=0,
        )

    monkeypatch.setattr(
        batch,
        "ingest_goalserve_tennis_odds",
        fake_goalserve_ingest,
    )
    monkeypatch.setattr(
        batch,
        "ensure_market_policy_version_v1",
        lambda conn: SimpleNamespace(
            market_policy_version_id=77,
            odds_max_staleness_minutes=15,
        ),
    )
    monkeypatch.setattr(
        batch,
        "evaluate_and_persist_market_slate",
        lambda *a, **k: SimpleNamespace(
            status="PERSISTED",
            new_decision_count=1,
            qualified_bet_count=0,
        ),
    )
    monkeypatch.setattr(batch, "_load_market_reason_counts", lambda *a, **k: ())
    monkeypatch.setattr(batch, "scan_type_b_value_signals", lambda *a, **k: ())
    monkeypatch.setattr(
        batch,
        "persist_type_b_scan",
        lambda *a, **k: SimpleNamespace(
            scan_run_id=1,
            inserted_signal_count=0,
            persisted_signal_count=0,
        ),
    )
    monkeypatch.setattr(batch, "consecutive_zero_signal_days", lambda *a, **k: 0)
    monkeypatch.setattr(batch, "activation_from_env", lambda: None)

    rc = batch.main(
        [
            "--start-date",
            "2026-09-19",
            "--days",
            "1",
            "--dsn",
            "postgresql://example",
            "--artifacts-dir",
            str(tmp_path),
        ]
    )

    assert rc == 0
    assert len(live_calls) == 1
    assert live_calls[0]["api_tennis_key"] == "sr-key"
    assert live_calls[0]["fetch_fixtures"] is batch.fetch_sportradar_fixture_rows
    assert live_calls[0]["ingest_fixtures"] is batch.ingest_sportradar_fixture_rows
    assert live_calls[0]["pinnapi_key"] is None
    assert goalserve_fetches == ["https://secret.invalid/feed"]
    assert goalserve_ingests[0]["candidate_match_ids"] == (10,)
    assert goalserve_ingests[0]["received_at"] == goalserve_received
