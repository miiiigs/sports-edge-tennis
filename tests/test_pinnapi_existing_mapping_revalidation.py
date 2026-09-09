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
    player_1_name: str,
    player_2_name: str,
    tournament_name: str,
    tour_scope: str,
    competition_tier: str,
) -> int:
    p1 = _fetchval(
        conn,
        "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id",
        (player_1_name,),
    )
    p2 = _fetchval(
        conn,
        "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id",
        (player_2_name,),
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
        VALUES (%s, %s, %s, %s, %s, %s, 'R32', 3, FALSE,
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


def _evidence(
    *,
    event_id: str,
    home: str,
    away: str,
    league: str,
    start: datetime = BASE_START,
) -> PinnapiEventEvidence:
    return PinnapiEventEvidence(
        provider_event_id=event_id,
        home_name=home,
        away_name=away,
        scheduled_start=start,
        league_id="provider-league",
        league_name=league,
        parent_id=None,
        event_type="prematch",
    )


def test_existing_mapping_survives_mutable_tournament_wording_and_schedule_drift(
    db: psycopg.Connection,
):
    match_id = _insert_candidate(
        db,
        player_1_name="Laslo Djere",
        player_2_name="Gabriele Piraino",
        tournament_name="Porto Challenger",
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
    )
    event_id = "1636042534"

    initial = reconcile_pinnapi_event(
        db,
        evidence=_evidence(
            event_id=event_id,
            home="Laslo Djere",
            away="Gabriele Piraino",
            league="Porto Challenger",
        ),
        candidate_match_ids=[match_id],
        as_of=datetime(2026, 9, 8, 18, 0, tzinfo=UTC),
    )
    assert initial.status == "RESOLVED"

    retry = reconcile_pinnapi_event(
        db,
        evidence=_evidence(
            event_id=event_id,
            home="L. Djere",
            away="G. Piraino",
            league="ATP Challenger Genoa - R16",
            start=BASE_START + timedelta(minutes=90),
        ),
        candidate_match_ids=[match_id],
        as_of=datetime(2026, 9, 8, 19, 0, tzinfo=UTC),
    )

    assert retry.status == "RESOLVED"
    assert retry.match is not None
    assert retry.match.match_id == match_id


def test_existing_mapping_still_fails_closed_on_explicit_tour_scope_conflict(
    db: psycopg.Connection,
):
    match_id = _insert_candidate(
        db,
        player_1_name="Alex Example",
        player_2_name="Ben Sample",
        tournament_name="ATP Vienna",
        tour_scope="ATP_MAIN",
        competition_tier="ATP_TOUR",
    )
    event_id = "pinnapi-existing-tour-conflict"

    initial = reconcile_pinnapi_event(
        db,
        evidence=_evidence(
            event_id=event_id,
            home="Alex Example",
            away="Ben Sample",
            league="ATP Vienna",
        ),
        candidate_match_ids=[match_id],
        as_of=datetime(2026, 9, 8, 18, 0, tzinfo=UTC),
    )
    assert initial.status == "RESOLVED"

    conflict = reconcile_pinnapi_event(
        db,
        evidence=_evidence(
            event_id=event_id,
            home="Alex Example",
            away="Ben Sample",
            league="ATP Challenger Vienna",
        ),
        candidate_match_ids=[match_id],
        as_of=datetime(2026, 9, 8, 19, 0, tzinfo=UTC),
    )

    assert conflict.status == "IDENTITY_CONFLICT"
    assert conflict.reason == "existing_mapping_evidence_conflict"
