from datetime import UTC, datetime, timedelta

import psycopg

from sports_edge_tennis.odds.reconciliation import (
    PinnapiEventEvidence,
    reconcile_pinnapi_event,
)


BASE_START = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
OBSERVED = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _fetchval(conn: psycopg.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _insert_candidate(
    conn: psycopg.Connection,
    *,
    tournament_name: str,
    tour_scope: str,
    competition_tier: str,
) -> int:
    p1 = _fetchval(
        conn,
        "INSERT INTO players (canonical_name) VALUES ('Alex Example') RETURNING player_id",
    )
    p2 = _fetchval(
        conn,
        "INSERT INTO players (canonical_name) VALUES ('Ben Sample') RETURNING player_id",
    )
    tournament_id = _fetchval(
        conn,
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES (%s, %s, %s)
        RETURNING tournament_id
        """,
        (tournament_name, tour_scope, competition_tier),
    )
    edition_id = _fetchval(
        conn,
        """
        INSERT INTO tournament_editions (
            tournament_id, season_year, edition_name, start_date
        )
        VALUES (%s, 2026, %s, %s)
        RETURNING tournament_edition_id
        """,
        (tournament_id, f"{tournament_name} 2026", BASE_START.date()),
    )
    match_id = _fetchval(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    )
    version_id = _fetchval(
        conn,
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at,
            tour_scope, competition_tier, round_code, best_of_sets,
            qualifying_flag, match_status, source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, %s, %s, 'QF', 3, FALSE,
                'SCHEDULED', %s, %s)
        RETURNING match_version_id
        """,
        (
            match_id,
            p1,
            p2,
            BASE_START,
            tour_scope,
            competition_tier,
            OBSERVED,
            OBSERVED,
        ),
    )
    conn.execute(
        """
        INSERT INTO match_source_ids (
            match_id, match_version_id, source_system, source_match_id
        )
        VALUES (%s, %s, 'API_TENNIS', %s)
        """,
        (match_id, version_id, f"api-{match_id}"),
    )
    return match_id


def _evidence(*, league_name: str, start_delta: timedelta) -> PinnapiEventEvidence:
    return PinnapiEventEvidence(
        provider_event_id="pinnapi-fallback-1",
        home_name="Alex Example",
        away_name="Ben Sample",
        scheduled_start=BASE_START + start_delta,
        league_id="provider-league",
        league_name=league_name,
        parent_id=None,
        event_type="prematch",
    )


def test_unique_exact_players_recover_provider_tournament_wording_and_schedule_drift(
    db: psycopg.Connection,
):
    match_id = _insert_candidate(
        db,
        tournament_name="Porto Challenger",
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
    )

    result = reconcile_pinnapi_event(
        db,
        evidence=_evidence(
            league_name="ATP Challenger Porto - Quarterfinals",
            start_delta=timedelta(hours=3),
        ),
        candidate_match_ids=[match_id],
        as_of=datetime(2026, 9, 8, 18, 0, tzinfo=UTC),
    )

    assert result.status == "RESOLVED"
    assert result.match is not None
    assert result.match.match_id == match_id
    assert result.reason == "unique_participant_tournament_fallback"


def test_fallback_does_not_cross_challenger_and_atp_main_scope(
    db: psycopg.Connection,
):
    match_id = _insert_candidate(
        db,
        tournament_name="ATP Vienna",
        tour_scope="ATP_MAIN",
        competition_tier="ATP_TOUR",
    )

    result = reconcile_pinnapi_event(
        db,
        evidence=_evidence(
            league_name="ATP Challenger Vienna - Quarterfinals",
            start_delta=timedelta(hours=2),
        ),
        candidate_match_ids=[match_id],
        as_of=datetime(2026, 9, 8, 18, 0, tzinfo=UTC),
    )

    assert result.status == "UNMATCHED"
    assert result.reason == "tour_scope_conflict"
    assert db.execute(
        "SELECT count(*) FROM match_source_ids WHERE source_system = 'PINNAPI'"
    ).fetchone()[0] == 0
