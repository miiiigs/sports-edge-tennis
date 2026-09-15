from datetime import UTC, datetime, timedelta

import psycopg

from sports_edge_tennis.odds.reconciliation import (
    PinnapiEventEvidence,
    _participant_name_compatible,
    reconcile_pinnapi_event,
)


BASE_START = datetime(2026, 9, 15, 20, 0, tzinfo=UTC)
OBSERVED = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)


def _fetchval(conn: psycopg.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _insert_candidate(
    conn: psycopg.Connection,
    *,
    player_1_name: str,
    player_2_name: str,
    tournament_name: str = "Tiburon Challenger",
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
        VALUES (%s, 'CHALLENGER', 'CHALLENGER')
        RETURNING tournament_id
        """,
        (tournament_name,),
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
        VALUES (%s, %s, %s, %s, 'CHALLENGER', 'CHALLENGER', 'R32', 3,
                FALSE, 'SCHEDULED', %s, %s)
        RETURNING match_version_id
        """,
        (match_id, p1, p2, BASE_START, OBSERVED, OBSERVED),
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
    home_name: str,
    away_name: str,
    start_delta: timedelta,
    provider_event_id: str,
) -> PinnapiEventEvidence:
    return PinnapiEventEvidence(
        provider_event_id=provider_event_id,
        home_name=home_name,
        away_name=away_name,
        scheduled_start=BASE_START + start_delta,
        league_id="challenger-tiburon",
        league_name="Tiburon Challenger",
        parent_id=None,
        event_type="prematch",
    )


def test_middle_initial_variant_is_deterministic_not_fuzzy():
    assert _participant_name_compatible("Thomas Fancutt", "T. J. Fancutt")
    assert _participant_name_compatible("James Trotter", "J. K. Trotter")
    assert not _participant_name_compatible("Thomas Fancutt", "T. J. Fantini")


def test_known_dar_abbreviation_maps_only_to_explicit_alias():
    assert _participant_name_compatible("Darwin Blanch", "Dar. Blanch")
    assert not _participant_name_compatible("Dario Blanch", "Dar. Blanch")


def test_initial_variant_can_reconcile_at_exactly_sixty_minutes(
    db: psycopg.Connection,
):
    match_id = _insert_candidate(
        db,
        player_1_name="I. Marcondes",
        player_2_name="N. Basavareddy",
    )

    result = reconcile_pinnapi_event(
        db,
        evidence=_evidence(
            home_name="Igor Marcondes",
            away_name="Nishesh Basavareddy",
            start_delta=timedelta(minutes=60),
            provider_event_id="1636578547",
        ),
        candidate_match_ids=[match_id],
        as_of=OBSERVED + timedelta(hours=1),
    )

    assert result.status == "RESOLVED"
    assert result.match is not None
    assert result.match.match_id == match_id
    assert result.reason == "deterministic_participant_variant_time_fallback"


def test_initial_variant_still_fails_closed_beyond_sixty_minutes(
    db: psycopg.Connection,
):
    match_id = _insert_candidate(
        db,
        player_1_name="T. Legout",
        player_2_name="H. Searle",
    )

    result = reconcile_pinnapi_event(
        db,
        evidence=_evidence(
            home_name="Timo Legout",
            away_name="Henry Searle",
            start_delta=timedelta(minutes=61),
            provider_event_id="1636636588",
        ),
        candidate_match_ids=[match_id],
        as_of=OBSERVED + timedelta(hours=1),
    )

    assert result.status == "UNMATCHED"
    assert result.reason == "start_or_tournament_mismatch"


def test_exact_names_do_not_receive_extended_time_tolerance(
    db: psycopg.Connection,
):
    match_id = _insert_candidate(
        db,
        player_1_name="Alex Example",
        player_2_name="Ben Sample",
    )

    result = reconcile_pinnapi_event(
        db,
        evidence=_evidence(
            home_name="Alex Example",
            away_name="Ben Sample",
            start_delta=timedelta(minutes=31),
            provider_event_id="exact-name-31",
        ),
        candidate_match_ids=[match_id],
        as_of=OBSERVED + timedelta(hours=1),
    )

    assert result.status == "UNMATCHED"
    assert result.reason == "start_or_tournament_mismatch"
