from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg

from sports_edge_tennis.odds.reconciliation import (
    PinnapiEventEvidence,
    reconcile_pinnapi_event,
)


BASE_START = datetime(2026, 9, 17, 21, 30, tzinfo=UTC)
OBSERVED = datetime(2026, 9, 17, 14, 0, tzinfo=UTC)


def _fetchval(conn: psycopg.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _insert_zheng_svajda(db: psycopg.Connection) -> int:
    p1 = _fetchval(
        db,
        "INSERT INTO players (canonical_name) VALUES ('M. Zheng') RETURNING player_id",
    )
    p2 = _fetchval(
        db,
        "INSERT INTO players (canonical_name) VALUES ('T. Svajda') RETURNING player_id",
    )
    tournament_id = _fetchval(
        db,
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES ('Tiburon', 'CHALLENGER', 'CHALLENGER')
        RETURNING tournament_id
        """,
    )
    edition_id = _fetchval(
        db,
        """
        INSERT INTO tournament_editions (
            tournament_id, season_year, edition_name, start_date
        )
        VALUES (%s, 2026, 'Tiburon 2026', '2026-09-17')
        RETURNING tournament_edition_id
        """,
        (tournament_id,),
    )
    match_id = _fetchval(
        db,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    )
    match_version_id = _fetchval(
        db,
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
    db.execute(
        """
        INSERT INTO match_source_ids (
            match_id, match_version_id, source_system, source_match_id
        )
        VALUES (%s, %s, 'API_TENNIS', %s)
        """,
        (match_id, match_version_id, f'api-{match_id}'),
    )
    return match_id


def _evidence(delta_minutes: int, provider_event_id: str) -> PinnapiEventEvidence:
    return PinnapiEventEvidence(
        provider_event_id=provider_event_id,
        home_name='Michael Zheng',
        away_name='Trevor Svajda',
        scheduled_start=BASE_START + timedelta(minutes=delta_minutes),
        league_id='challenger-tiburon',
        league_name='ATP Challenger Tiburon',
        parent_id=None,
        event_type='prematch',
    )


def test_production_shaped_100_minute_variant_reconciles(db: psycopg.Connection):
    match_id = _insert_zheng_svajda(db)

    result = reconcile_pinnapi_event(
        db,
        evidence=_evidence(100, '1636716474'),
        candidate_match_ids=[match_id],
        as_of=OBSERVED + timedelta(minutes=5),
    )

    assert result.status == 'RESOLVED'
    assert result.match is not None
    assert result.match.match_id == match_id
    assert result.reason == 'deterministic_participant_variant_time_fallback'


def test_deterministic_variant_still_fails_closed_beyond_120_minutes(
    db: psycopg.Connection,
):
    match_id = _insert_zheng_svajda(db)

    result = reconcile_pinnapi_event(
        db,
        evidence=_evidence(121, 'beyond-120'),
        candidate_match_ids=[match_id],
        as_of=OBSERVED + timedelta(minutes=5),
    )

    assert result.status == 'UNMATCHED'
    assert result.reason == 'start_or_tournament_mismatch'
