"""API-Tennis fixtures + OddsPapi odds: identity, orientation and batch wiring.

Production fixtures come from API-Tennis, which carries no Betradar id, so
OddsPapi events must link to canonical matches by deterministic participant
names in exactly one orientation plus start time, failing closed otherwise.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import psycopg
import pytest

import sports_edge_tennis.live.batch as batch
from sports_edge_tennis.live.oddspapi import parse_oddspapi_tennis_fixture
from sports_edge_tennis.live.oddspapi_odds import ingest_oddspapi_tennis_odds
from sports_edge_tennis.live.oddspapi_reconciliation import (
    OddspapiCanonicalCandidate,
    OddspapiEventResolution,
    reconcile_oddspapi_event,
)
from tests.abstention_helpers import fetchval, insert_player
from tests.test_live_batch_target_providers import _Connect, _live_result, _NoopLedger

START = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
AS_OF = START - timedelta(hours=3)


def _fixture(
    fixture_id="id1200000000000077",
    p1="Novak Djokovic",
    p2="Daniil Medvedev",
    p1_id=9001,
    p2_id=9002,
    start=START,
    tournament_id=303,
):
    return parse_oddspapi_tennis_fixture(
        {
            "fixtureId": fixture_id,
            "participant1Id": p1_id,
            "participant2Id": p2_id,
            "participant1Name": p1,
            "participant2Name": p2,
            "sportId": 12,
            "tournamentId": tournament_id,
            "startTime": start.isoformat().replace("+00:00", "Z"),
        }
    )


def _canonical_match(
    conn: psycopg.Connection,
    *,
    p1_name="N. Djokovic",
    p2_name="D. Medvedev",
    start=START,
    players: tuple[int, int] | None = None,
) -> tuple[int, int, int]:
    """API-Tennis-style canonical match (abbreviated names, no Sportradar id)."""
    p1, p2 = players or (insert_player(conn, p1_name), insert_player(conn, p2_name))
    tournament_id = fetchval(
        conn,
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) "
        "VALUES ('Shanghai', 'ATP_MAIN', 'MASTERS_1000') RETURNING tournament_id",
    )
    edition_id = fetchval(
        conn,
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name) "
        "VALUES (%s, 2026, 'Shanghai 2026') RETURNING tournament_edition_id",
        (tournament_id,),
    )
    match_id = fetchval(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    )
    conn.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope, competition_tier,
            surface, round_code, best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'MASTERS_1000', 'HARD', 'R16', 3, FALSE,
                'SCHEDULED', %s, %s)
        """,
        (match_id, p1, p2, start, AS_OF - timedelta(hours=1), AS_OF - timedelta(hours=1)),
    )
    conn.execute(
        "INSERT INTO match_source_ids (match_id, source_system, source_match_id) "
        "VALUES (%s, 'API_TENNIS', %s)",
        (match_id, f"api-{match_id}"),
    )
    return match_id, p1, p2


def _oddspapi_links(conn, match_id):
    return conn.execute(
        "SELECT source_match_id FROM match_source_ids "
        "WHERE source_system = 'ODDSPAPI' AND match_id = %s",
        (match_id,),
    ).fetchall()


def _player_binding(conn, source_player_id):
    row = conn.execute(
        "SELECT player_id FROM player_source_ids "
        "WHERE source_system = 'ODDSPAPI' AND source_player_id = %s",
        (str(source_player_id),),
    ).fetchone()
    return int(row[0]) if row else None


# --- canonical identity integration (PostgreSQL) ---------------------------


def test_direct_name_time_link_without_sportradar(db: psycopg.Connection):
    match_id, p1, p2 = _canonical_match(db)
    result = reconcile_oddspapi_event(
        db,
        envelope=_fixture(),
        candidate_match_ids=(match_id,),
        as_of=AS_OF,
        persist=True,
    )
    assert (result.status, result.reason, result.match_id) == ("EXACT", "NAME_TIME", match_id)
    assert result.candidate.provider_orientation == "DIRECT"
    assert _oddspapi_links(db, match_id) == [("id1200000000000077",)]
    assert _player_binding(db, 9001) == p1
    assert _player_binding(db, 9002) == p2


def test_reversed_provider_orientation_binds_players_crosswise(db: psycopg.Connection):
    match_id, p1, p2 = _canonical_match(db)
    result = reconcile_oddspapi_event(
        db,
        envelope=_fixture(p1="Daniil Medvedev", p2="Novak Djokovic"),
        candidate_match_ids=(match_id,),
        as_of=AS_OF,
        persist=True,
    )
    assert result.status == "EXACT"
    assert result.candidate.provider_orientation == "REVERSED"
    # OddsPapi participant1 (Medvedev) is canonical player 2, and vice versa.
    assert _player_binding(db, 9001) == p2
    assert _player_binding(db, 9002) == p1


def test_start_time_outside_window_is_unmatched(db: psycopg.Connection):
    match_id, _, _ = _canonical_match(db)
    result = reconcile_oddspapi_event(
        db,
        envelope=_fixture(start=START + timedelta(minutes=61)),
        candidate_match_ids=(match_id,),
        as_of=AS_OF,
        max_start_delta_minutes=batch.ODDSPAPI_DISCOVERY_MAX_START_DELTA_MINUTES,
        persist=True,
    )
    assert result.status == "UNMATCHED"
    assert _oddspapi_links(db, match_id) == []


def test_wrong_given_name_initial_never_links(db: psycopg.Connection):
    match_id, _, _ = _canonical_match(db, p1_name="M. Djokovic")
    result = reconcile_oddspapi_event(
        db,
        envelope=_fixture(),
        candidate_match_ids=(match_id,),
        as_of=AS_OF,
        persist=False,
    )
    assert result.status == "UNMATCHED"


def test_two_candidate_matches_are_ambiguous_and_write_nothing(db: psycopg.Connection):
    first, p1, p2 = _canonical_match(db)
    second, _, _ = _canonical_match(db, start=START + timedelta(minutes=30), players=(p1, p2))
    result = reconcile_oddspapi_event(
        db,
        envelope=_fixture(),
        candidate_match_ids=(first, second),
        as_of=AS_OF,
        persist=True,
    )
    assert result.status == "AMBIGUOUS"
    assert _oddspapi_links(db, first) == [] and _oddspapi_links(db, second) == []


def test_second_fixture_for_linked_match_is_conflict(db: psycopg.Connection):
    match_id, _, _ = _canonical_match(db)
    reconcile_oddspapi_event(
        db, envelope=_fixture(), candidate_match_ids=(match_id,), as_of=AS_OF, persist=True
    )
    duplicate = reconcile_oddspapi_event(
        db,
        envelope=_fixture(fixture_id="id1200000000000099", p1_id=9101, p2_id=9102),
        candidate_match_ids=(match_id,),
        as_of=AS_OF,
        persist=True,
    )
    assert (duplicate.status, duplicate.reason) == ("CONFLICT", "match_mapped_to_other_fixture")
    assert _oddspapi_links(db, match_id) == [("id1200000000000077",)]


def test_player_bridge_conflict_rolls_back_and_does_not_abort_ingest(db: psycopg.Connection):
    match_id, p1, _ = _canonical_match(db)
    other = insert_player(db, "Someone Else")
    # OddsPapi participant 9001 is already bound to a different canonical player.
    db.execute(
        "INSERT INTO player_source_ids (player_id, source_system, source_player_id) "
        "VALUES (%s, 'ODDSPAPI', '9001')",
        (other,),
    )
    payload = {
        "fixtureId": "id1200000000000077",
        "participant1Id": 9001,
        "participant2Id": 9002,
        "participant1Name": "Novak Djokovic",
        "participant2Name": "Daniil Medvedev",
        "sportId": 12,
        "tournamentId": 303,
        "startTime": START.isoformat().replace("+00:00", "Z"),
        "bookmakerOdds": {},
    }
    outcome = ingest_oddspapi_tennis_odds(
        db,
        payload_rows=[payload],
        received_at=AS_OF,
        candidate_match_ids=(match_id,),
    )
    assert outcome.conflict_events == 1
    assert outcome.normalized_quotes == 0
    # The event mapping written before the bridge failed was rolled back.
    assert _oddspapi_links(db, match_id) == []
    assert _player_binding(db, 9002) is None


# --- discovery identity resolution (unit) ----------------------------------


def _candidate(match_id, orientation="DIRECT"):
    return OddspapiCanonicalCandidate(
        match_id=match_id,
        match_version_id=1,
        canonical_player_1_id=11,
        canonical_player_2_id=22,
        canonical_player_1_name="A",
        canonical_player_2_name="B",
        provider_orientation=orientation,
        start_delta_minutes=0,
    )


def _row(fixture_id, tournament_id=303):
    return {
        "fixtureId": fixture_id,
        "participant1Id": 1,
        "participant2Id": 2,
        "participant1Name": "A",
        "participant2Name": "B",
        "sportId": 12,
        "tournamentId": tournament_id,
        "startTime": "2026-10-01T09:00:00Z",
    }


def test_cold_resolution_caches_orientation_and_rejects_double_claims(monkeypatch):
    resolutions = {
        "f-reversed": OddspapiEventResolution("EXACT", 1, _candidate(1, "REVERSED"), 1, "NAME_TIME"),
        "f-dup-a": OddspapiEventResolution("EXACT", 2, _candidate(2), 1, "NAME_TIME"),
        "f-dup-b": OddspapiEventResolution("EXACT", 2, _candidate(2), 1, "NAME_TIME"),
        "f-ambiguous": OddspapiEventResolution("AMBIGUOUS", None, None, 2, "multiple"),
        "f-out-of-scope": OddspapiEventResolution("EXACT", 99, _candidate(99), 1, "NAME_TIME"),
    }
    seen_kwargs = []

    def fake_reconcile(conn, **kwargs):
        seen_kwargs.append(kwargs)
        return resolutions[kwargs["envelope"].fixture_id]

    cached = []
    monkeypatch.setattr(batch, "reconcile_oddspapi_event", fake_reconcile)
    monkeypatch.setattr(batch, "upsert_cached_identity", lambda conn, identity: cached.append(identity))

    tournaments, cached_count, rejected = batch._resolve_cold_oddspapi_identities(
        None,
        discovery_rows=[_row(fixture_id) for fixture_id in resolutions],
        cold_context=[
            {"match_id": 1, "sportradar_event_id": None, "betradar_id": None, "scheduled_start": START},
            {"match_id": 2, "sportradar_event_id": None, "betradar_id": None, "scheduled_start": START},
        ],
        now=AS_OF,
    )

    assert tournaments == [303]
    assert cached_count == 1
    assert rejected == 2  # one ambiguous fixture + one doubly-claimed match
    assert [(c.canonical_match_id, c.player_orientation, c.sportradar_event_id) for c in cached] == [
        (1, "REVERSED", None)
    ]
    assert all(kwargs["persist"] is False for kwargs in seen_kwargs)
    assert all(kwargs["candidate_match_ids"] == (1, 2) for kwargs in seen_kwargs)


# --- batch wiring -----------------------------------------------------------


def _install_api_tennis_oddspapi_batch(monkeypatch, *, consensus):
    authority = SimpleNamespace(
        data_mode=batch.LEGACY_DATA_MODE,
        odds_mode=batch.TARGET_ODDS_MODE,
    )
    monkeypatch.setattr(batch, "load_provider_authority", lambda env: authority)
    monkeypatch.setattr(batch, "assert_cutover_ready", lambda conn, env: authority)
    monkeypatch.setattr(batch.psycopg, "connect", _Connect())
    secrets = {
        "ODDSPAPI_API_KEY": "odds-key",
        "API_TENNIS_API_KEY": "api-tennis-key",
        "PINNAPI_KEY": "legacy-pinnapi",
    }
    monkeypatch.setattr(batch, "load_secret", lambda name: secrets.get(name))
    monkeypatch.setattr(batch, "OddspapiLedgerBinding", _NoopLedger)

    calls: dict[str, list] = {"live": [], "price": [], "discovery": [], "board": [], "ingest": [], "cached": []}

    def fake_live(**kwargs):
        calls["live"].append(kwargs)
        return _live_result()

    monkeypatch.setattr(batch, "run_live_mvp", fake_live)
    monkeypatch.setattr(batch, "_load_manager_reason_counts", lambda *a, **k: ())
    monkeypatch.setattr(
        batch,
        "load_price_scanner_candidate_match_ids",
        lambda *a, **k: calls["price"].append(k) or (10,),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Pinnapi / API-Tennis odds must not be called")

    for name in ("fetch_api_tennis_odds", "fetch_pinnapi_board", "normalize_pinnapi_board_for_matches", "ensure_pinnapi_bookmaker_mapping"):
        monkeypatch.setattr(batch, name, forbidden)

    received = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)
    monkeypatch.setattr(
        batch,
        "fetch_oddspapi_tennis_fixtures",
        lambda key, **kw: calls["discovery"].append((key, kw)) or (received, [_row("f-1")]),
    )
    monkeypatch.setattr(
        batch,
        "_load_manager_consensus_context",
        lambda conn, run_ids: [
            {"match_id": 10, "sportradar_event_id": None, "betradar_id": None, "scheduled_start": START}
        ]
        if consensus
        else [],
    )
    monkeypatch.setattr(batch, "partition_cached_identities", lambda conn, candidates, now: ({}, [10]))
    monkeypatch.setattr(batch, "upsert_cached_identity", lambda conn, identity: calls["cached"].append(identity))
    monkeypatch.setattr(
        batch,
        "reconcile_oddspapi_event",
        lambda conn, **kw: OddspapiEventResolution("EXACT", 10, _candidate(10, "REVERSED"), 1, "NAME_TIME"),
    )
    monkeypatch.setattr(
        batch,
        "fetch_oddspapi_tennis_odds_for_books",
        lambda key, **kw: calls["board"].append((key, kw)) or (received, [_row("f-1")], 4),
    )
    monkeypatch.setattr(
        batch,
        "ingest_oddspapi_tennis_odds",
        lambda conn, **kw: calls["ingest"].append(kw)
        or SimpleNamespace(
            candidate_matches=1, provider_events_seen=1, exact_events=1, existing_events=0,
            unmatched_events=0, ambiguous_events=0, conflict_events=0, normalized_quotes=4,
            unknown_bookmaker_groups=0, unknown_participant_groups=0,
        ),
    )
    monkeypatch.setattr(
        batch,
        "ensure_market_policy_version_v2",
        lambda conn: SimpleNamespace(market_policy_version_id=77, odds_max_staleness_minutes=15),
    )
    monkeypatch.setattr(
        batch,
        "evaluate_and_persist_market_slate",
        lambda *a, **k: SimpleNamespace(status="PERSISTED", new_decision_count=1, qualified_bet_count=0),
    )
    monkeypatch.setattr(batch, "_load_market_reason_counts", lambda *a, **k: ())
    monkeypatch.setattr(batch, "scan_type_b_value_signals", lambda *a, **k: ())
    monkeypatch.setattr(
        batch,
        "persist_type_b_scan",
        lambda *a, **k: SimpleNamespace(scan_run_id=1, inserted_signal_count=0, persisted_signal_count=0),
    )
    monkeypatch.setattr(batch, "consecutive_zero_signal_days", lambda *a, **k: 0)
    monkeypatch.setattr(batch, "activation_from_env", lambda: None)
    return calls


def _run(tmp_path):
    return batch.main(
        [
            "--start-date", "2026-10-14", "--days", "1",
            "--dsn", "postgresql://example", "--artifacts-dir", str(tmp_path),
        ]
    )


def test_batch_api_tennis_oddspapi_never_calls_pinnapi_and_discovers_without_betradar(
    monkeypatch, tmp_path, capsys
):
    calls = _install_api_tennis_oddspapi_batch(monkeypatch, consensus=True)
    assert _run(tmp_path) == 0

    live = calls["live"][0]
    assert live["pinnapi_key"] is None
    assert live["api_tennis_key"] == "api-tennis-key"
    assert calls["price"][0]["source_systems"] == ("API_TENNIS",)
    assert calls["discovery"][0][0] == "odds-key"
    assert calls["board"][0][1]["tournament_ids"] == (303,)
    # Discovery (1) + 4 books x 1 chunk stays within the 5-request cycle cap.
    assert calls["board"][0][1]["fallback_budget"] == 0
    assert [(c.canonical_match_id, c.player_orientation) for c in calls["cached"]] == [(10, "REVERSED")]
    out = capsys.readouterr().out
    assert "odds_provider=ODDSPAPI_TENNIS" in out
    assert "ODDSPAPI_IDENTITY_CACHE warm=0 cold=1 cold_discovered=1 cold_rejected=0" in out
    assert "odds-key" not in out and "api-tennis-key" not in out


def test_batch_api_tennis_oddspapi_zero_consensus_makes_zero_requests(monkeypatch, tmp_path, capsys):
    calls = _install_api_tennis_oddspapi_batch(monkeypatch, consensus=False)
    assert _run(tmp_path) == 0
    assert calls["discovery"] == [] and calls["board"] == []
    assert "ODDSPAPI_QUALIFICATION_SKIPPED reason=ZERO_CONSENSUS requests=0" in capsys.readouterr().out


@pytest.mark.parametrize("kind,label", [("HTTP_403", "FORBIDDEN"), ("HTTP_429", "RATE_LIMIT")])
def test_batch_oddspapi_transport_failure_is_classified_and_fails_closed(
    monkeypatch, tmp_path, capsys, kind, label
):
    from sports_edge_tennis.live.transport import TransportError

    calls = _install_api_tennis_oddspapi_batch(monkeypatch, consensus=True)

    def failing_discovery(key, **kw):
        raise TransportError(kind, "provider unavailable")

    monkeypatch.setattr(batch, "fetch_oddspapi_tennis_fixtures", failing_discovery)
    assert _run(tmp_path) == 0
    out = capsys.readouterr().out
    assert f"OFFERED_ODDS_UNAVAILABLE provider=ODDSPAPI_TENNIS kind={kind} class={label}" in out
    assert calls["board"] == [] and calls["ingest"] == []
