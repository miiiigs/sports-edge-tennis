from __future__ import annotations

from datetime import UTC, datetime
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


def _oddspapi_row():
    return {
        "fixtureId": "id1200000000000001",
        "participant1Id": 101,
        "participant2Id": 202,
        "participant1Name": "Carlos Alcaraz",
        "participant2Name": "Jannik Sinner",
        "sportId": 12,
        "tournamentId": 303,
        "startTime": "2026-09-19T14:00:00Z",
        "hasOdds": True,
        "externalProviders": {"betradarId": 12345},
        "bookmakerOdds": {},
    }


def test_target_mode_uses_only_sportradar_and_oddspapi(monkeypatch, tmp_path):
    authority = SimpleNamespace(
        data_mode=batch.TARGET_DATA_MODE,
        odds_mode=batch.TARGET_ODDS_MODE,
    )
    monkeypatch.setattr(batch, "load_provider_authority", lambda env: authority)
    monkeypatch.setattr(batch, "assert_cutover_ready", lambda conn, env: authority)
    monkeypatch.setattr(batch.psycopg, "connect", _Connect())

    secrets = {
        "SPORTRADAR_TENNIS_API_KEY": "sr-key",
        "ODDSPAPI_API_KEY": "odds-key",
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

    price_calls = []

    def fake_price_slate(*args, **kwargs):
        price_calls.append(kwargs)
        return (10,)

    monkeypatch.setattr(
        batch,
        "load_price_scanner_candidate_match_ids",
        fake_price_slate,
    )

    def legacy_call_forbidden(*args, **kwargs):
        raise AssertionError("legacy provider call executed in target mode")

    monkeypatch.setattr(batch, "fetch_api_tennis_odds", legacy_call_forbidden)
    monkeypatch.setattr(batch, "fetch_pinnapi_board", legacy_call_forbidden)
    monkeypatch.setattr(
        batch,
        "normalize_pinnapi_board_for_matches",
        legacy_call_forbidden,
    )

    odds_received = datetime(2026, 9, 19, 12, 30, tzinfo=UTC)
    account_calls = []
    monkeypatch.setattr(
        batch,
        "fetch_oddspapi_account",
        lambda key: account_calls.append(key)
        or SimpleNamespace(request_limit=5000, request_count=3),
    )
    discovery_calls = []

    def fake_discovery(key, **kwargs):
        discovery_calls.append((key, kwargs))
        kwargs["budget"].consume_attempt()
        return odds_received, [_oddspapi_row()]

    monkeypatch.setattr(
        batch,
        "fetch_oddspapi_tennis_fixtures",
        fake_discovery,
    )

    board_calls = []

    def fake_board(key, **kwargs):
        board_calls.append((key, kwargs))
        assert kwargs["budget"] is discovery_calls[0][1]["budget"]
        assert kwargs["budget"].session_used == 1
        kwargs["budget"].consume_attempt()
        return odds_received, [_oddspapi_row()]

    monkeypatch.setattr(
        batch,
        "fetch_oddspapi_tennis_odds_by_tournaments",
        fake_board,
    )

    ingest_calls = []

    def fake_oddspapi_ingest(conn, **kwargs):
        ingest_calls.append(kwargs)
        return SimpleNamespace(
            candidate_matches=1,
            provider_events_seen=1,
            exact_events=1,
            existing_events=0,
            unmatched_events=0,
            ambiguous_events=0,
            conflict_events=0,
            normalized_quotes=6,
            unknown_bookmaker_groups=0,
            unknown_participant_groups=0,
        )

    monkeypatch.setattr(
        batch,
        "ingest_oddspapi_tennis_odds",
        fake_oddspapi_ingest,
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
    assert price_calls[0]["source_systems"] == ("SPORTRADAR",)
    assert account_calls == ["odds-key"]
    assert discovery_calls[0][0] == "odds-key"
    assert discovery_calls[0][1]["bookmakers"] == ("pinnacle+30",)
    assert discovery_calls[0][1]["budget"].monthly_used == 3
    assert discovery_calls[0][1]["budget"].session_used == 2
    assert board_calls[0][0] == "odds-key"
    assert board_calls[0][1]["tournament_ids"] == [303]
    assert board_calls[0][1]["bookmakers"] == (
        "pinnacle+30",
        "1xbet",
        "unibet",
        "bet365",
    )
    assert ingest_calls[0]["candidate_match_ids"] == (10,)
    assert ingest_calls[0]["received_at"] == odds_received


def test_legacy_mode_never_touches_oddspapi_budget_or_account(monkeypatch, tmp_path):
    authority = SimpleNamespace(
        data_mode=batch.LEGACY_DATA_MODE,
        odds_mode=batch.LEGACY_ODDS_MODE,
    )
    monkeypatch.setattr(batch, "load_provider_authority", lambda env: authority)
    monkeypatch.setattr(batch, "assert_cutover_ready", lambda conn, env: authority)
    monkeypatch.setattr(batch.psycopg, "connect", _Connect())
    monkeypatch.setattr(
        batch,
        "load_secret",
        lambda name: {
            "API_TENNIS_API_KEY": "legacy-api-tennis",
            "PINNAPI_KEY": "legacy-pinnapi",
            "ODDSPAPI_API_KEY": "odds-key",
        }.get(name),
    )
    monkeypatch.setattr(batch, "ensure_pinnapi_bookmaker_mapping", lambda conn: None)
    monkeypatch.setattr(batch, "run_live_mvp", lambda **kwargs: _live_result())
    monkeypatch.setattr(batch, "_load_manager_reason_counts", lambda *a, **k: ())
    monkeypatch.setattr(
        batch,
        "load_price_scanner_candidate_match_ids",
        lambda *a, **k: (),
    )
    monkeypatch.setattr(
        batch,
        "fetch_oddspapi_account",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("OddsPapi account called in legacy mode")
        ),
    )
    monkeypatch.setattr(
        batch,
        "fetch_oddspapi_tennis_fixtures",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("OddsPapi fixtures called in legacy mode")
        ),
    )
    monkeypatch.setattr(
        batch,
        "fetch_oddspapi_tennis_odds_by_tournaments",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("OddsPapi odds called in legacy mode")
        ),
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


def test_target_mode_budget_blocks_second_billable_call(monkeypatch, tmp_path):
    authority = SimpleNamespace(
        data_mode=batch.TARGET_DATA_MODE,
        odds_mode=batch.TARGET_ODDS_MODE,
    )
    monkeypatch.setattr(batch, "load_provider_authority", lambda env: authority)
    monkeypatch.setattr(batch, "assert_cutover_ready", lambda conn, env: authority)
    monkeypatch.setattr(batch.psycopg, "connect", _Connect())
    monkeypatch.setattr(
        batch,
        "load_secret",
        lambda name: {
            "SPORTRADAR_TENNIS_API_KEY": "sr-key",
            "ODDSPAPI_API_KEY": "odds-key",
        }.get(name),
    )
    monkeypatch.setattr(batch, "run_live_mvp", lambda **kwargs: _live_result())
    monkeypatch.setattr(batch, "_load_manager_reason_counts", lambda *a, **k: ())
    monkeypatch.setattr(
        batch,
        "load_price_scanner_candidate_match_ids",
        lambda *a, **k: (10,),
    )
    monkeypatch.setattr(
        batch,
        "fetch_oddspapi_account",
        lambda key: SimpleNamespace(request_limit=5000, request_count=4499),
    )
    odds_received = datetime(2026, 9, 19, 12, 30, tzinfo=UTC)
    board_calls = []

    def fake_discovery(key, **kwargs):
        kwargs["budget"].consume_attempt()
        return odds_received, [_oddspapi_row()]

    def fake_board(key, **kwargs):
        kwargs["budget"].consume_attempt()
        board_calls.append((key, kwargs))
        return odds_received, [_oddspapi_row()]

    monkeypatch.setattr(batch, "fetch_oddspapi_tennis_fixtures", fake_discovery)
    monkeypatch.setattr(batch, "fetch_oddspapi_tennis_odds_by_tournaments", fake_board)
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
    assert board_calls == []
