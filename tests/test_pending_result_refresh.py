"""Tests for the bounded same-day pending Model Pick result refresh.

The lane is result-ingestion infrastructure. It must never create wagers, change
model/Manager/Market/Veto/staking behavior, widen the shared 15-minute identity
tolerance, or implement its own canonical identity policy — identity is delegated to
the global repository ingestion path.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

import sports_edge_tennis.live.pending_result_refresh as prr
from sports_edge_tennis.agents.contracts import AGENT_PROBABILITY_REPRESENTATIONS
from sports_edge_tennis.data.repository import HistoricalRepository
from sports_edge_tennis.live.credentials import MissingCredentialError
from sports_edge_tennis.live.orchestrator import ensure_agent_config_versions_v1
from sports_edge_tennis.live.transport import TransportError
from sports_edge_tennis.manager.persistence import ensure_manager_policy_version_v1

A1 = "AGENT_1_SURFACE_ELO"
A2 = "AGENT_2_HIERARCHICAL_MARKOV"
A3 = "AGENT_3_INTRANSITIVITY"

NOW = datetime.now(UTC)
MATCH_START = NOW - timedelta(hours=8)
VERSION_OBSERVED = NOW - timedelta(hours=9)
RUN_AS_OF = NOW - timedelta(hours=8, minutes=30)
# Slightly in the past so the database clock is always >= the observation time.
RECEIVED_AT = NOW - timedelta(seconds=5)
MATCH_DAY = MATCH_START.date()
TODAY = NOW.date()


def _fv(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def _seed_run(conn) -> HistoricalRepository:
    run_id = _fv(
        conn,
        "INSERT INTO ingest_runs (source_system, source_dataset, started_at, status) "
        "VALUES ('TEST', 'pending_result_refresh', %s, 'RUNNING') RETURNING ingest_run_id",
        (VERSION_OBSERVED,),
    )
    return HistoricalRepository(conn, run_id)


def _player(conn, name: str) -> int:
    return _fv(
        conn,
        "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id",
        (name,),
    )


def _tournament(conn, name: str = "Test Open") -> int:
    return _fv(
        conn,
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) "
        "VALUES (%s, 'ATP_MAIN', 'ATP_250') RETURNING tournament_id",
        (name,),
    )


def _edition(conn, tournament_id: int, season: int = MATCH_START.year) -> int:
    return _fv(
        conn,
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name, start_date) "
        "VALUES (%s, %s, %s, %s) RETURNING tournament_edition_id",
        (tournament_id, season, f"Edition {season}", MATCH_DAY.isoformat()),
    )


def _version(
    conn,
    match_id: int,
    p1: int,
    p2: int,
    start: datetime,
    *,
    status: str = "SCHEDULED",
    observed: datetime = VERSION_OBSERVED,
) -> int:
    return _fv(
        conn,
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope,
            competition_tier, surface, round_code, best_of_sets, qualifying_flag,
            match_status, source_observed_at, as_of
        ) VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3, FALSE, %s, %s, %s)
        RETURNING match_version_id
        """,
        (match_id, p1, p2, start, status, observed, observed),
    )


def _source(conn, match_id: int, system: str, source_id: str) -> None:
    conn.execute(
        "INSERT INTO match_source_ids (match_id, source_system, source_match_id) "
        "VALUES (%s, %s, %s)",
        (match_id, system, source_id),
    )


def _api_player_link(conn, player_id: int, source_player_id: str) -> None:
    conn.execute(
        "INSERT INTO player_source_ids (player_id, source_system, source_player_id) "
        "VALUES (%s, 'API_TENNIS', %s)",
        (player_id, source_player_id),
    )


def _api_tournament_link(conn, tournament_id: int, source_tournament_id: str) -> None:
    conn.execute(
        "INSERT INTO tournament_source_ids (tournament_id, source_system, source_tournament_id) "
        "VALUES (%s, 'API_TENNIS', %s)",
        (tournament_id, source_tournament_id),
    )


def _result(conn, match_id: int, *, outcome: str = "COMPLETED", winner_side: int | None = 1) -> int:
    return _fv(
        conn,
        """
        INSERT INTO match_result_observations (
            match_id, outcome_status, winner_side, source_observed_at, as_of
        ) VALUES (%s, %s, %s, %s, %s) RETURNING match_result_observation_id
        """,
        (match_id, outcome, winner_side, VERSION_OBSERVED, VERSION_OBSERVED),
    )


def _seed_target(
    conn,
    *,
    api_event: str | None = None,
    status: str = "SCHEDULED",
    with_result: bool = False,
    start: datetime = MATCH_START,
    observed: datetime = VERSION_OBSERVED,
    source_system: str = "SPORTRADAR",
    source_match_id: str = "sr-1",
):
    """Canonical picked match plus API-Tennis identity maps and optional event link."""
    repository = _seed_run(conn)
    p1 = _player(conn, "Player A")
    p2 = _player(conn, "Player B")
    tournament_id = _tournament(conn)
    edition_id = _edition(conn, tournament_id)
    match_id = _fv(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    )
    _version(conn, match_id, p1, p2, start, status=status, observed=observed)
    _source(conn, match_id, source_system, source_match_id)
    _api_player_link(conn, p1, "api-a")
    _api_player_link(conn, p2, "api-b")
    _api_tournament_link(conn, tournament_id, "tk-1")
    if api_event is not None:
        _source(conn, match_id, "API_TENNIS", api_event)
    if with_result:
        _result(conn, match_id)
    return {
        "repository": repository,
        "match_id": match_id,
        "player_1_id": p1,
        "player_2_id": p2,
        "tournament_id": tournament_id,
        "edition_id": edition_id,
    }


def seed_production_model_pick(
    conn,
    match_id: int,
    *,
    side: int = 1,
    probability: str = "0.620",
    as_of: datetime = RUN_AS_OF,
    wide_spread: bool = False,
) -> int:
    configs = _seed_agent_manager_configs(conn)
    run_id = _fv(
        conn,
        "INSERT INTO prediction_runs (run_status, as_of, started_at, prediction_contract_version) "
        "VALUES ('RUNNING', %s, %s, 'predictive_agent_result_v1') RETURNING prediction_run_id",
        (as_of, as_of),
    )
    with conn.transaction():
        conn.execute("SET CONSTRAINTS fk_predictions_prediction_run_finalization DEFERRED")
        conn.execute(
            "INSERT INTO prediction_run_targets (prediction_run_id, match_id) VALUES (%s, %s)",
            (run_id, match_id),
        )
        if wide_spread:
            a1_side, a1_prob = side, "0.700"
            a2_side, a2_prob = side, "0.550"
            manager_status, reason = "NO_BET", "AGENT3_ABSTENTION_SPREAD_EXCEEDED"
        else:
            a1_side, a1_prob = side, probability
            a2_side, a2_prob = side, probability
            manager_status, reason = "CONSENSUS", "NONE"
        for code, agent_side, agent_prob in ((A1, a1_side, a1_prob), (A2, a2_side, a2_prob)):
            cfg = configs[code]
            conn.execute(
                """
                INSERT INTO predictions (
                    match_id, prediction_run_id, agent_config_version_id, agent_code,
                    prediction_status, selection_side, win_probability,
                    probability_representation, model_version, feature_version, as_of,
                    prediction_locked_at
                ) VALUES (%s, %s, %s, %s, 'PREDICTED', %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    match_id, run_id, cfg["id"], code, agent_side, Decimal(agent_prob),
                    AGENT_PROBABILITY_REPRESENTATIONS[code], cfg["model"], cfg["feature"],
                    as_of, as_of,
                ),
            )
        cfg = configs[A3]
        conn.execute(
            """
            INSERT INTO predictions (
                match_id, prediction_run_id, agent_config_version_id, agent_code,
                prediction_status, model_version, feature_version, as_of, prediction_locked_at
            ) VALUES (%s, %s, %s, %s, 'ABSTAINED', %s, %s, %s, %s)
            """,
            (match_id, run_id, cfg["id"], A3, cfg["model"], cfg["feature"], as_of, as_of),
        )
        conn.execute(
            "UPDATE prediction_runs SET run_status='COMPLETED', prediction_finalized_at=%s, "
            "ended_at=%s WHERE prediction_run_id=%s",
            (as_of, as_of, run_id),
        )

    policy_id = _fv(
        conn,
        "SELECT manager_policy_version_id FROM manager_policy_versions "
        "ORDER BY manager_policy_version_id LIMIT 1",
    )
    conn.execute(
        """
        INSERT INTO manager_consensus_results (
            prediction_run_id, match_id, manager_policy_version_id, manager_status,
            selected_side, consensus_probability, active_agent_count,
            abstaining_agent_count, probability_spread, reason_code, as_of
        ) VALUES (%s, %s, %s, %s, %s, %s, 2, 1, %s, %s, %s)
        """,
        (
            run_id, match_id, policy_id, manager_status,
            side if manager_status == "CONSENSUS" else None,
            Decimal(probability) if manager_status == "CONSENSUS" else None,
            Decimal("0.02"), reason, as_of,
        ),
    )
    return run_id


def _seed_agent_manager_configs(conn) -> dict[str, dict]:
    ensure_agent_config_versions_v1(conn)
    ensure_manager_policy_version_v1(conn)
    rows = conn.execute(
        "SELECT agent_code::text, agent_config_version_id, model_version, feature_version "
        "FROM agent_config_versions"
    ).fetchall()
    return {r[0]: {"id": r[1], "model": r[2], "feature": r[3]} for r in rows}


def _fixture(**overrides) -> dict:
    payload = {
        "event_key": "at-1",
        "tournament_key": "tk-1",
        "tournament_name": "Test Open",
        "event_type_type": "Atp Singles",
        "competition_tier": "ATP_250",
        "event_date": MATCH_START.date().isoformat(),
        "event_time": MATCH_START.strftime("%H:%M"),
        "event_status": "Finished",
        "event_first_player": "Player A",
        "event_second_player": "Player B",
        "first_player_key": "api-a",
        "second_player_key": "api-b",
        "event_surface": "Hard",
        "event_round": "R32",
        "event_winner": "First Player",
        "event_final_result": "2 - 0",
    }
    payload.update(overrides)
    return payload


def _fetch_at(fixtures: list[dict]):
    def fetch(_api_key: str, _start, _end):
        return RECEIVED_AT, fixtures

    return fetch


def _refresh(conn, fixtures, *, fetcher=None, dry_run=False, **kwargs):
    return prr.refresh_pending_results(
        conn,
        api_key="secret",
        as_of=NOW,
        fetch_window=fetcher or _fetch_at(fixtures),
        dry_run=dry_run,
        **kwargs,
    )


def _write_counts(conn) -> dict[str, int]:
    tables = (
        "matches",
        "match_versions",
        "match_source_ids",
        "match_result_observations",
        "cross_provider_match_reconciliations",
        "decision_settlements",
    )
    return {t: int(conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]) for t in tables}


# --------------------------------------------------------------- population


def test_same_day_pending_pick_is_selected(db: psycopg.Connection):
    target = _seed_target(db)
    seed_production_model_pick(db, target["match_id"])
    targets = prr.load_pending_model_pick_targets(
        db, as_of=NOW, window_start=MATCH_DAY, window_end=MATCH_DAY, max_targets=10
    )
    assert [t.match_id for t in targets] == [target["match_id"]]
    assert targets[0].pick_source == prr.PICK_SOURCE_MANAGER
    assert targets[0].selected_side == 1


def test_previous_day_pending_pick_is_selected(db: psycopg.Connection):
    previous_start = datetime.combine(TODAY - timedelta(days=1), time(18, 0), tzinfo=UTC)
    target = _seed_target(
        db, start=previous_start, observed=previous_start - timedelta(hours=1)
    )
    seed_production_model_pick(
        db, target["match_id"], as_of=previous_start - timedelta(minutes=30)
    )
    targets = prr.load_pending_model_pick_targets(
        db,
        as_of=NOW,
        window_start=TODAY - timedelta(days=1),
        window_end=TODAY,
        max_targets=10,
    )
    assert [t.match_id for t in targets] == [target["match_id"]]


def test_not_started_match_excluded(db: psycopg.Connection):
    target = _seed_target(
        db, start=NOW + timedelta(hours=2), observed=NOW - timedelta(hours=1)
    )
    seed_production_model_pick(db, target["match_id"])
    targets = prr.load_pending_model_pick_targets(
        db, as_of=NOW, window_start=MATCH_DAY, window_end=TODAY, max_targets=10
    )
    assert targets == ()


def test_already_settled_match_excluded(db: psycopg.Connection):
    target = _seed_target(db, with_result=True)
    seed_production_model_pick(db, target["match_id"])
    targets = prr.load_pending_model_pick_targets(
        db, as_of=NOW, window_start=MATCH_DAY, window_end=MATCH_DAY, max_targets=10
    )
    assert targets == ()


def test_outside_window_excluded(db: psycopg.Connection):
    target = _seed_target(
        db,
        start=MATCH_START - timedelta(days=5),
        observed=VERSION_OBSERVED - timedelta(days=5),
    )
    seed_production_model_pick(db, target["match_id"])
    targets = prr.load_pending_model_pick_targets(
        db, as_of=NOW, window_start=MATCH_DAY, window_end=TODAY, max_targets=10
    )
    assert targets == ()


def test_duplicate_prediction_rows_dedupe_to_one_match(db: psycopg.Connection):
    target = _seed_target(db)
    seed_production_model_pick(db, target["match_id"], as_of=RUN_AS_OF)
    seed_production_model_pick(db, target["match_id"], as_of=RUN_AS_OF + timedelta(minutes=1))
    targets = prr.load_pending_model_pick_targets(
        db, as_of=NOW, window_start=MATCH_DAY, window_end=MATCH_DAY, max_targets=10
    )
    assert len(targets) == 1


def test_wide_spread_tracking_pick_is_included_and_labelled(db: psycopg.Connection):
    target = _seed_target(db)
    seed_production_model_pick(db, target["match_id"], wide_spread=True)
    targets = prr.load_pending_model_pick_targets(
        db, as_of=NOW, window_start=MATCH_DAY, window_end=MATCH_DAY, max_targets=10
    )
    assert [t.match_id for t in targets] == [target["match_id"]]
    assert targets[0].pick_source == prr.PICK_SOURCE_WIDE_SPREAD


# ----------------------------------------------------------- same-canonical


def test_same_match_terminal_result_persists(db: psycopg.Connection):
    target = _seed_target(db, api_event="at-1")
    seed_production_model_pick(db, target["match_id"])
    summary = _refresh(db, [_fixture()])
    assert summary.same_match == 1
    assert summary.persisted == 1
    row = db.execute(
        "SELECT outcome_status::text, winner_side FROM match_result_observations "
        "WHERE match_id = %s",
        (target["match_id"],),
    ).fetchone()
    assert row == ("COMPLETED", 1)


def test_retired_persists_as_retired(db: psycopg.Connection):
    target = _seed_target(db, api_event="at-1")
    seed_production_model_pick(db, target["match_id"])
    _refresh(db, [_fixture(event_status="Retired", event_winner="First Player", event_final_result="0 - 1")])
    row = db.execute(
        "SELECT outcome_status::text, retired_side FROM match_result_observations"
    ).fetchone()
    assert row == ("RETIRED", 2)


def test_walkover_persists_as_walkover(db: psycopg.Connection):
    target = _seed_target(db, api_event="at-1")
    seed_production_model_pick(db, target["match_id"])
    _refresh(db, [_fixture(event_status="Walkover", event_winner="First Player")])
    assert db.execute(
        "SELECT outcome_status::text FROM match_result_observations"
    ).fetchone()[0] == "WALKOVER"


def test_walkover_without_provider_winner_stays_pending(db: psycopg.Connection):
    target = _seed_target(db, api_event="at-1")
    seed_production_model_pick(db, target["match_id"])
    summary = _refresh(
        db,
        [_fixture(event_status="Walkover", event_winner=None, event_final_result="-")],
    )
    assert summary.persisted == 0
    assert summary.unsafe == 1
    assert (
        db.execute(
            "SELECT count(*) FROM match_result_observations WHERE match_id = %s",
            (target["match_id"],),
        ).fetchone()[0]
        == 0
    )


def test_cancelled_persists_as_cancelled(db: psycopg.Connection):
    target = _seed_target(db, api_event="at-1")
    seed_production_model_pick(db, target["match_id"])
    _refresh(db, [_fixture(event_status="Cancelled", event_winner=None, event_final_result="-")])
    row = db.execute(
        "SELECT outcome_status::text, winner_side FROM match_result_observations"
    ).fetchone()
    assert row == ("CANCELLED", None)


def test_nonterminal_fixture_remains_pending(db: psycopg.Connection):
    target = _seed_target(db, api_event="at-1")
    seed_production_model_pick(db, target["match_id"])
    summary = _refresh(
        db, [_fixture(event_status="Not Started", event_winner=None, event_final_result="-")]
    )
    assert summary.terminal_found == 0
    assert summary.unresolved == 1
    assert db.execute("SELECT count(*) FROM match_result_observations").fetchone()[0] == 0


def test_second_run_is_idempotent(db: psycopg.Connection):
    target = _seed_target(db, api_event="at-1")
    seed_production_model_pick(db, target["match_id"])
    first = _refresh(db, [_fixture()])
    second = _refresh(db, [_fixture()])
    assert first.persisted == 1
    assert second.checked == 0  # now settled, excluded from the pending population
    assert db.execute("SELECT count(*) FROM match_result_observations").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM matches").fetchone()[0] == 1


# ------------------------------------------------- global canonical reuse


def test_unseen_event_reuses_canonical_via_global_path(db: psycopg.Connection):
    target = _seed_target(db)  # SPORTRADAR only, no API event link
    seed_production_model_pick(db, target["match_id"])
    summary = _refresh(db, [_fixture(event_key="at-new")])
    assert summary.reused == 1
    assert summary.same_match == 0
    assert db.execute("SELECT count(*) FROM matches").fetchone()[0] == 1
    assert db.execute(
        "SELECT match_id FROM match_source_ids WHERE source_system='API_TENNIS' "
        "AND source_match_id='at-new'"
    ).fetchone()[0] == target["match_id"]
    assert db.execute(
        "SELECT match_id FROM match_result_observations"
    ).fetchone()[0] == target["match_id"]


def test_reversed_unseen_event_reuses_canonical(db: psycopg.Connection):
    target = _seed_target(db)
    seed_production_model_pick(db, target["match_id"])
    summary = _refresh(
        db,
        [_fixture(event_key="at-rev", first_player_key="api-b", second_player_key="api-a")],
    )
    assert summary.reused == 1
    assert db.execute("SELECT count(*) FROM matches").fetchone()[0] == 1


def test_time_mismatch_does_not_reuse(db: psycopg.Connection):
    target = _seed_target(db)
    seed_production_model_pick(db, target["match_id"])
    shifted = (MATCH_START + timedelta(minutes=16)).strftime("%H:%M")
    summary = _refresh(db, [_fixture(event_key="at-late", event_time=shifted)])
    assert summary.reused == 0
    assert summary.same_match == 0
    assert summary.unresolved + summary.unsafe == 1
    assert db.execute("SELECT count(*) FROM match_result_observations").fetchone()[0] == 0


def test_no_reconciliation_rows_created(db: psycopg.Connection):
    target = _seed_target(db)
    seed_production_model_pick(db, target["match_id"])
    _refresh(db, [_fixture(event_key="at-new")])
    assert db.execute(
        "SELECT count(*) FROM cross_provider_match_reconciliations"
    ).fetchone()[0] == 0


# ------------------------------------------------------------- dry-run


def test_dry_run_same_match_writes_nothing(db: psycopg.Connection):
    target = _seed_target(db, api_event="at-1")
    seed_production_model_pick(db, target["match_id"])
    before = _write_counts(db)
    summary = _refresh(db, [_fixture()], dry_run=True)
    assert summary.provider_requests == 1
    assert summary.same_match == 1
    assert summary.persisted == 0
    assert _write_counts(db) == before


def test_dry_run_predicts_reuse_without_writes(db: psycopg.Connection):
    target = _seed_target(db)
    seed_production_model_pick(db, target["match_id"])
    before = _write_counts(db)
    summary = _refresh(db, [_fixture(event_key="at-new")], dry_run=True)
    assert summary.reused == 1
    assert _write_counts(db) == before


def test_dry_run_expected_w_l_void(db: psycopg.Connection):
    target = _seed_target(db, api_event="at-1")
    seed_production_model_pick(db, target["match_id"], side=1)
    lost = _refresh(
        db,
        [_fixture(event_key="at-1", event_winner="Second Player", event_final_result="1 - 2")],
        dry_run=True,
    )
    assert lost.expected_lost == 1
    void = _refresh(
        db,
        [_fixture(event_key="at-1", event_status="Retired", event_winner="First Player")],
        dry_run=True,
    )
    assert void.expected_void == 1


def test_dry_run_reports_target_breakdown(db: psycopg.Connection):
    manager = _seed_target(db, api_event="m-1")

    # A second target that is a wide-spread-only pick.
    p1 = _player(db, "Player C")
    p2 = _player(db, "Player D")
    tid = _tournament(db, "Other Open")
    eid = _edition(db, tid)
    wide = _fv(db, "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id", (eid,))
    _version(db, wide, p1, p2, MATCH_START, status="SCHEDULED")
    _source(db, wide, "SPORTRADAR", "sr-2")
    _api_player_link(db, p1, "api-c")
    _api_player_link(db, p2, "api-d")
    _api_tournament_link(db, tid, "tk-2")

    seed_production_model_pick(db, manager["match_id"])
    seed_production_model_pick(db, wide, wide_spread=True)

    summary = _refresh(db, [_fixture(event_key="none")], dry_run=True)
    assert summary.checked == 2
    assert summary.manager_targets == 1
    assert summary.wide_spread_targets == 1


# ------------------------------------------------------------- guards


def test_provider_failure_fails_closed(db: psycopg.Connection):
    target = _seed_target(db, api_event="at-1")
    seed_production_model_pick(db, target["match_id"])

    def boom(_api_key, _start, _end):
        raise TransportError("TIMEOUT", "down")

    summary = _refresh(db, [], fetcher=boom)
    assert summary.provider_errors == 1
    assert summary.provider_requests == 1
    assert db.execute("SELECT count(*) FROM match_result_observations").fetchone()[0] == 0


def test_no_targets_makes_no_provider_request(db: psycopg.Connection):
    calls = []

    def fetch(_api_key, _start, _end):
        calls.append(1)
        return RECEIVED_AT, []

    summary = _refresh(db, [], fetcher=fetch)
    assert calls == []
    assert summary.checked == 0
    assert summary.provider_requests == 0


def test_missing_api_key_raises(db: psycopg.Connection):
    target = _seed_target(db)
    seed_production_model_pick(db, target["match_id"])
    with pytest.raises(MissingCredentialError):
        prr.refresh_pending_results(db, api_key=None, as_of=NOW, fetch_window=_fetch_at([]))


def test_window_and_target_limits_guard():
    assert prr.window_bounds(NOW, 1) == (TODAY, TODAY)
    assert prr.window_bounds(NOW, 2) == (TODAY - timedelta(days=1), TODAY)
    assert prr.window_bounds(NOW, 3) == (TODAY - timedelta(days=2), TODAY)
    with pytest.raises(ValueError):
        prr.window_bounds(NOW, 4)
    with pytest.raises(ValueError):
        prr.load_pending_model_pick_targets(
            None, as_of=NOW, window_start=MATCH_DAY, window_end=MATCH_DAY,
            max_targets=prr.MAX_TARGETS_LIMIT + 1,
        )


def test_manila_previous_day_can_start_two_utc_dates_earlier():
    # At 06:00 UTC Oct 13, a match beginning at 22:30 UTC Oct 11
    # is shown on the dashboard as Oct 12 in Asia/Manila.
    as_of = datetime(2026, 10, 13, 6, 0, tzinfo=UTC)
    match_start = datetime(2026, 10, 11, 22, 30, tzinfo=UTC)
    start, end = prr.window_bounds(as_of)
    assert (start, end) == (datetime(2026, 10, 11).date(), datetime(2026, 10, 13).date())
    assert start <= match_start.date() <= end
    assert (match_start + timedelta(hours=8)).date() == datetime(2026, 10, 12).date()


# ------------------------------------------------- no behavior change


def test_refresh_performs_zero_betting_actions(db: psycopg.Connection):
    target = _seed_target(db, api_event="at-1")
    seed_production_model_pick(db, target["match_id"])
    tracked = ("decisions", "decision_settlements", "predictions", "manager_consensus_results", "prediction_runs")

    def counts() -> dict[str, int]:
        return {t: int(db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]) for t in tracked}

    before = counts()
    summary = _refresh(db, [_fixture()])
    assert summary.same_match == 1
    assert counts() == before


def test_module_delegates_identity_and_has_no_local_policy():
    source = Path(prr.__file__).read_text(encoding="utf-8")
    assert "resolve_cross_provider_canonical_reuse" in source
    assert "build_reconciliation_plan" not in source
    assert "apply_candidate_links" not in source
    assert "apply_cross_provider_reconciliation" not in source
    assert "MAX_START_DELTA_MINUTES =" not in source


# ------------------------------------------------- production integration


class _ConnCtx:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_pending_cycle_is_fail_isolated(monkeypatch, capsys):
    from sports_edge_tennis.live import production_batch

    monkeypatch.setattr(
        prr,
        "refresh_pending_results",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(production_batch.psycopg, "connect", lambda *a, **k: _ConnCtx())
    monkeypatch.setenv("API_TENNIS_API_KEY", "secret")
    production_batch._pending_result_refresh_cycle("postgresql://example")
    out = capsys.readouterr().out
    assert "PENDING_RESULT_REFRESH_FAILED error_type=RuntimeError" in out


def test_pending_cycle_is_reachable_and_wired_before_settlement():
    import inspect

    from sports_edge_tennis.live import production_batch

    source = inspect.getsource(production_batch._run_batch)
    assert "_pending_result_refresh_cycle(dsn)" in source
    # The degraded path also calls settlement, so compare against the
    # successful-batch settlement call after the pending-result refresh.
    assert source.index("_pending_result_refresh_cycle(dsn)") < source.rindex(
        "_settlement_cycle(dsn, settled_at=settled_at)"
    )


def test_main_blocks_without_database(monkeypatch, capsys):
    monkeypatch.setattr(prr, "_db_dsn", lambda: None)
    monkeypatch.setattr(prr, "load_secret", lambda name: "key")
    assert prr.main([]) == 2
    assert "reason=NO_DATABASE" in capsys.readouterr().out


def test_main_blocks_without_api_key(monkeypatch, capsys):
    monkeypatch.setattr(prr, "load_secret", lambda name: None)
    assert prr.main(["--dsn", "postgresql://x/y"]) == 2
    assert "reason=MISSING_API_TENNIS_KEY" in capsys.readouterr().out


def test_main_skips_when_already_running(monkeypatch, capsys):
    monkeypatch.setattr(prr, "load_secret", lambda name: "key")
    monkeypatch.setattr(prr, "acquire_production_batch_lock", lambda dsn: None)
    assert prr.main(["--dsn", "postgresql://x/y"]) == 0
    assert "reason=ALREADY_RUNNING" in capsys.readouterr().out


def test_main_logs_dry_run_summary(monkeypatch, capsys):
    monkeypatch.setattr(prr, "load_secret", lambda name: "key")
    monkeypatch.setattr(prr, "acquire_production_batch_lock", lambda dsn: object())
    monkeypatch.setattr(prr, "release_production_batch_lock", lambda info: None)
    monkeypatch.setattr(prr.psycopg, "connect", lambda *a, **k: _ConnCtx())
    monkeypatch.setattr(
        prr, "refresh_pending_results", lambda *a, **k: prr.PendingResultRefreshSummary()
    )
    assert prr.main(["--dsn", "postgresql://x/y", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "PENDING_RESULT_REFRESH_DRY_RUN writes=0" in out
    assert "PENDING_RESULT_REFRESH checked=0" in out


# ------------------------------------------------- settlement-eligible Type A


def _seed_type_a_bet(
    conn,
    match_id: int,
    *,
    side: int = 1,
    created_at: datetime = RUN_AS_OF,
    market_type: str = "MATCH_WINNER",
) -> int:
    """A settlement-eligible Type A BET on a match that is *not* a Model Pick."""
    run_id = _fv(
        conn,
        "INSERT INTO prediction_runs (run_status, as_of, started_at, prediction_contract_version) "
        "VALUES ('RUNNING', %s, %s, 'predictive_agent_result_v1') RETURNING prediction_run_id",
        (created_at, created_at),
    )
    return _fv(
        conn,
        """
        INSERT INTO decisions (
            match_id, prediction_run_id, signal_type, decision_status, reason_code,
            market_type, selection_type, selection_side, consensus_probability,
            consensus_fair_odds, market_decimal_price, sharp_reference_price,
            price_source_system, price_bookmaker_code, devig_method, devig_fair_odds,
            edge_pct, min_acceptable_odds, active_agent_count, abstaining_agent_count,
            model_version, feature_version, as_of, prediction_locked_at, created_at
        ) VALUES (
            %s, %s, 'TYPE_A_MODEL_EDGE', 'BET', 'NONE',
            %s, %s, %s, 0.60, 1.666667, 1.90, 1.85,
            'OFFERED', 'BOOKIE', 'MULTIPLICATIVE', 1.85, 0.05, 1.70,
            2, 1, 'test-model', 'test-feature', %s, %s, %s
        ) RETURNING decision_id
        """,
        (
            match_id, run_id, market_type, "PLAYER_1" if side == 1 else "PLAYER_2", side,
            created_at, created_at, created_at,
        ),
    )


def test_pending_type_a_bet_without_model_pick_is_selected(db: psycopg.Connection):
    # Settlement reports pending_results on Type A BET decisions; this lane must
    # cover that cohort directly rather than rely on every BET match also being
    # a Model Pick (true for all 56 production BET matches so far, not enforced).
    target = _seed_target(db)
    _seed_type_a_bet(db, target["match_id"], side=2)

    targets = prr.load_pending_model_pick_targets(
        db, as_of=NOW, window_start=MATCH_DAY, window_end=MATCH_DAY, max_targets=10
    )

    assert [t.match_id for t in targets] == [target["match_id"]]
    assert targets[0].pick_source == prr.PICK_SOURCE_TYPE_A_BET
    assert targets[0].selected_side == 2


def test_repeated_type_a_bets_on_one_match_dedupe_to_one_target(db: psycopg.Connection):
    target = _seed_target(db)
    for hours in range(3):
        _seed_type_a_bet(db, target["match_id"], created_at=RUN_AS_OF + timedelta(hours=hours))
    seed_production_model_pick(db, target["match_id"])

    targets = prr.load_pending_model_pick_targets(
        db, as_of=NOW, window_start=MATCH_DAY, window_end=MATCH_DAY, max_targets=10
    )

    assert [t.match_id for t in targets] == [target["match_id"]]


def test_type_a_bet_terminal_result_persists_without_settling(db: psycopg.Connection):
    target = _seed_target(db, api_event="at-1")
    _seed_type_a_bet(db, target["match_id"], side=1)
    before = _write_counts(db)

    summary = _refresh(db, [_fixture()])

    assert summary.checked == 1
    assert summary.type_a_bet_targets == 1
    assert summary.terminal_found == 1
    assert summary.same_match == 1
    assert summary.expected_won == 1
    after = _write_counts(db)
    assert after["match_result_observations"] == before["match_result_observations"] + 1
    # Result ingestion only: settlement stays the settlement service's job.
    assert after["decision_settlements"] == before["decision_settlements"]
    assert _fv(db, "SELECT count(*) FROM decisions") == 1


def test_type_a_bet_outside_window_excluded(db: psycopg.Connection):
    old_start = MATCH_START - timedelta(days=5)
    target = _seed_target(db, start=old_start, observed=old_start - timedelta(hours=1))
    _seed_type_a_bet(db, target["match_id"], created_at=old_start - timedelta(hours=1))

    targets = prr.load_pending_model_pick_targets(
        db, as_of=NOW, window_start=MATCH_DAY, window_end=MATCH_DAY, max_targets=10
    )

    assert targets == ()


def test_type_a_bet_on_model_pick_match_is_flagged_not_hidden(db: psycopg.Connection):
    # DISTINCT ON keeps one row per match labelled by its earliest pick; the BET
    # must still be visible so type_a_bet_targets reflects real BET coverage.
    target = _seed_target(db, api_event="at-1")
    seed_production_model_pick(db, target["match_id"])
    _seed_type_a_bet(db, target["match_id"], created_at=RUN_AS_OF + timedelta(hours=1))

    targets = prr.load_pending_model_pick_targets(
        db, as_of=NOW, window_start=MATCH_DAY, window_end=MATCH_DAY, max_targets=10
    )
    assert [t.match_id for t in targets] == [target["match_id"]]
    assert targets[0].pick_source == prr.PICK_SOURCE_MANAGER
    assert targets[0].has_type_a_bet is True

    summary = _refresh(db, [_fixture()])
    assert summary.checked == 1
    assert summary.manager_targets == 1
    assert summary.type_a_bet_targets == 1


def test_model_pick_without_bet_is_not_flagged(db: psycopg.Connection):
    target = _seed_target(db)
    seed_production_model_pick(db, target["match_id"])

    targets = prr.load_pending_model_pick_targets(
        db, as_of=NOW, window_start=MATCH_DAY, window_end=MATCH_DAY, max_targets=10
    )
    assert [t.has_type_a_bet for t in targets] == [False]


def test_non_match_winner_bet_is_not_a_target(db: psycopg.Connection):
    # Only the settlement-eligible cohort counts; other markets are never targets.
    target = _seed_target(db)
    _seed_type_a_bet(db, target["match_id"], market_type="GAME_HANDICAP")

    targets = prr.load_pending_model_pick_targets(
        db, as_of=NOW, window_start=MATCH_DAY, window_end=MATCH_DAY, max_targets=10
    )
    assert targets == ()
