from __future__ import annotations

from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.live.sportradar_ingest import (
    ingest_sportradar_daily_summaries,
)

RECEIVED_AT = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
MATCH_START = datetime(2026, 9, 19, 13, 0, tzinfo=UTC)
SEED_OBSERVED_AT = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def _seed_completed_match(
    db: psycopg.Connection, *, winner_side: int = 1
) -> tuple[int, int, int]:
    p1 = db.execute(
        "INSERT INTO players (canonical_name) VALUES ('Cohort A') RETURNING player_id"
    ).fetchone()[0]
    p2 = db.execute(
        "INSERT INTO players (canonical_name) VALUES ('Cohort B') RETURNING player_id"
    ).fetchone()[0]
    db.execute(
        """
        INSERT INTO player_source_ids (player_id, source_system, source_player_id)
        VALUES (%s, 'SPORTRADAR', 'sr:p1'), (%s, 'SPORTRADAR', 'sr:p2')
        """,
        (p1, p2),
    )
    tournament_id = db.execute(
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES ('Cohort Challenger Singles', 'CHALLENGER', 'CHALLENGER')
        RETURNING tournament_id
        """
    ).fetchone()[0]
    edition_id = db.execute(
        """
        INSERT INTO tournament_editions (
            tournament_id, season_year, edition_name, surface, start_date
        )
        VALUES (%s, 2026, 'Cohort Challenger Singles 2026', 'HARD', %s)
        RETURNING tournament_edition_id
        """,
        (tournament_id, MATCH_START.date()),
    ).fetchone()[0]
    match_id = db.execute(
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    ).fetchone()[0]
    version_id = db.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope,
            competition_tier, surface, round_code, best_of_sets, qualifying_flag,
            match_status, source_observed_at, as_of
        )
        VALUES (
            %s, %s, %s, %s, 'CHALLENGER', 'CHALLENGER', 'HARD', 'Round of 32',
            3, FALSE, 'COMPLETED', %s, %s
        )
        RETURNING match_version_id
        """,
        (match_id, p1, p2, MATCH_START, SEED_OBSERVED_AT, SEED_OBSERVED_AT),
    ).fetchone()[0]
    db.execute(
        """
        INSERT INTO match_source_ids (
            match_id, match_version_id, source_system, source_match_id
        )
        VALUES (%s, %s, 'API_TENNIS', 'at:match:cohort')
        """,
        (match_id, version_id),
    )
    db.execute(
        """
        INSERT INTO match_result_observations (
            match_id, outcome_status, winner_side, sets_won_player_1,
            sets_won_player_2, source_observed_at, as_of
        )
        VALUES (%s, 'COMPLETED', %s, 2, 0, %s, %s)
        """,
        (match_id, winner_side, SEED_OBSERVED_AT, SEED_OBSERVED_AT),
    )
    return match_id, p1, p2


def _summary(*, winner_provider_id: str = "sr:p1") -> dict:
    return {
        "sport_event": {
            "id": "sr:event:cohort",
            "start_time": "2026-09-19T13:00:00Z",
            "sport_event_context": {
                "competition": {
                    "id": "sr:competition:cohort",
                    "name": "Cohort Challenger Men Singles",
                    "level": "challenger",
                    "type": "singles",
                    "gender": "men",
                },
                "category": {"name": "ATP"},
                "season": {"id": "sr:season:cohort"},
                "surface": "hard",
                "mode": {"best_of": "3"},
                "round": {"name": "Round of 32"},
                "stage": {"phase": "main"},
            },
            "competitors": [
                {"id": "sr:p1", "name": "Cohort A", "qualifier": "home"},
                {"id": "sr:p2", "name": "Cohort B", "qualifier": "away"},
            ],
        },
        "sport_event_status": {
            "status": "closed",
            "match_status": "ended",
            "winner_id": winner_provider_id,
            "home_score": 2,
            "away_score": 0,
            "period_scores": [
                {"type": "set", "home_score": 6, "away_score": 4},
                {"type": "set", "home_score": 6, "away_score": 3},
            ],
        },
    }


def _sr_event_match_id(db: psycopg.Connection) -> int:
    return db.execute(
        """
        SELECT match_id
        FROM match_source_ids
        WHERE source_system = 'SPORTRADAR'
          AND source_match_id = 'sr:event:cohort'
        """
    ).fetchone()[0]


def test_terminal_event_links_to_completed_canonical_match_without_duplicate(
    db: psycopg.Connection,
) -> None:
    canonical_match_id, _, _ = _seed_completed_match(db)

    outcome = ingest_sportradar_daily_summaries(
        db,
        {"summaries": [_summary()]},
        received_at=RECEIVED_AT,
        allow_new_events=True,
    )

    assert outcome.rows_ingested == 1
    assert outcome.rows_deferred_new == 0
    assert outcome.matches[0].overlap_status == "EXACT_EXISTING_MATCH"
    assert outcome.matches[0].match_id == canonical_match_id
    assert db.execute("SELECT count(*) FROM matches").fetchone()[0] == 1
    assert _sr_event_match_id(db) == canonical_match_id


def test_terminal_event_winner_conflict_is_rejected_without_duplicate(
    db: psycopg.Connection,
) -> None:
    canonical_match_id, _, _ = _seed_completed_match(db, winner_side=2)

    outcome = ingest_sportradar_daily_summaries(
        db,
        {"summaries": [_summary(winner_provider_id="sr:p1")]},
        received_at=RECEIVED_AT,
        allow_new_events=True,
    )

    assert outcome.rows_ingested == 0
    assert outcome.rows_rejected == 1
    assert "TERMINAL_TRUTH_CONFLICT" in str(outcome.rejected_rows)
    assert db.execute("SELECT count(*) FROM matches").fetchone()[0] == 1
    assert db.execute(
        "SELECT count(*) FROM match_source_ids WHERE source_system = 'SPORTRADAR'"
    ).fetchone()[0] == 0
    assert canonical_match_id is not None


def test_replayed_terminal_event_is_idempotent_without_duplicate(
    db: psycopg.Connection,
) -> None:
    canonical_match_id, _, _ = _seed_completed_match(db)

    first = ingest_sportradar_daily_summaries(
        db,
        {"summaries": [_summary()]},
        received_at=RECEIVED_AT,
        allow_new_events=True,
    )
    second = ingest_sportradar_daily_summaries(
        db,
        {"summaries": [_summary()]},
        received_at=RECEIVED_AT,
        allow_new_events=True,
    )

    assert first.matches[0].match_id == canonical_match_id
    assert second.matches[0].match_id == canonical_match_id
    assert second.matches[0].overlap_status == "SOURCE_ID_RESOLVED"
    assert db.execute("SELECT count(*) FROM matches").fetchone()[0] == 1
