from __future__ import annotations

from datetime import UTC, date, datetime

import psycopg

from sports_edge_tennis.data.sources.api_tennis_live import ingest_api_tennis_source_a_fixtures
from sports_edge_tennis.live.api_tennis_stats_backfill import (
    backfill_stats_chunk,
    backfill_stats_range,
)
from sports_edge_tennis.runner.live_model_cache import AGENT2_CACHE_KEY, save_checkpoint


INGESTED_AT = datetime(2026, 9, 10, 0, 0, tzinfo=UTC)
BACKFILL_AT = datetime(2026, 9, 17, 16, 0, tzinfo=UTC)


def _stat(player_key: str, name: str, *, value=None, won=None, total=None):
    return {
        "player_key": player_key,
        "stat_period": "match",
        "stat_type": "Service",
        "stat_name": name,
        "stat_value": value,
        "stat_won": won,
        "stat_total": total,
    }


def _complete_stats():
    return [
        _stat("p1", "Aces", value="7"),
        _stat("p1", "Double Faults", value="2"),
        _stat("p1", "1st serve points won", won=31, total=48),
        _stat("p1", "2nd serve points won", won=10, total=20),
        _stat("p2", "Aces", value="4"),
        _stat("p2", "Double Faults", value="3"),
        _stat("p2", "1st serve points won", won=25, total=44),
        _stat("p2", "2nd serve points won", won=11, total=21),
    ]


def _fixture(**overrides):
    payload = {
        "event_key": "990777",
        "tournament_key": "7707",
        "tournament_name": "Tiburon Challenger Men",
        "event_type_type": "Atp Challenger Singles",
        "event_date": "2026-09-09",
        "event_time": "18:30",
        "event_status": "Finished",
        "event_first_player": "Player One",
        "event_second_player": "Player Two",
        "first_player_key": "p1",
        "second_player_key": "p2",
        "event_surface": "Hard",
        "event_round": "R32",
        "event_winner": "First Player",
        "event_final_result": "2 - 0",
        "statistics": _complete_stats(),
    }
    payload.update(overrides)
    return payload


def _fetch(fixtures, *, received_at=BACKFILL_AT):
    def fetch(_api_key: str, _start: date, _end: date):
        return received_at, fixtures

    return fetch


def _seed_existing_match(db: psycopg.Connection) -> None:
    fixture = _fixture(statistics=[])
    outcome = ingest_api_tennis_source_a_fixtures(
        db,
        [fixture],
        received_at=INGESTED_AT,
    )
    assert outcome.rows_ingested == 1


def _counts(db: psycopg.Connection) -> tuple[int, int, int]:
    return (
        db.execute("SELECT count(*) FROM matches").fetchone()[0],
        db.execute("SELECT count(*) FROM match_versions").fetchone()[0],
        db.execute("SELECT count(*) FROM match_result_observations").fetchone()[0],
    )


def test_backfill_writes_only_stats_for_existing_exact_identity(db: psycopg.Connection):
    _seed_existing_match(db)
    before = _counts(db)
    save_checkpoint(
        db,
        checkpoint_key=AGENT2_CACHE_KEY,
        model_version="test-model",
        feature_version="test-feature",
        checkpoint_cutoff=INGESTED_AT,
        observation_count=1,
        payload=b"{}",
    )

    outcome = backfill_stats_chunk(
        db,
        api_key="secret",
        start_date=date(2026, 9, 9),
        end_date=date(2026, 9, 9),
        fetch_window=_fetch([_fixture()]),
    )

    assert outcome.fixtures_seen == 1
    assert outcome.terminal_fixtures == 1
    assert outcome.matched_existing == 1
    assert outcome.unmatched_event == 0
    assert outcome.missing_player_identity == 0
    assert outcome.identity_mismatch == 0
    assert outcome.eligible_stat_matches == 1
    assert outcome.inserted_stat_rows == 2
    assert outcome.existing_stat_rows == 0
    assert _counts(db) == before

    rows = db.execute(
        """
        SELECT p.canonical_name, mso.service_points_played, mso.return_points_won,
               mso.source_observed_at, mso.as_of
        FROM match_stat_observations mso
        JOIN players p ON p.player_id = mso.player_id
        WHERE mso.source_system = 'API_TENNIS'
        ORDER BY p.canonical_name
        """
    ).fetchall()
    assert rows == [
        ("Player One", 68, 29, BACKFILL_AT, BACKFILL_AT),
        ("Player Two", 65, 27, BACKFILL_AT, BACKFILL_AT),
    ]
    assert db.execute(
        "SELECT count(*) FROM live_model_state_checkpoints WHERE checkpoint_key = %s",
        (AGENT2_CACHE_KEY,),
    ).fetchone()[0] == 0


def test_backfill_rerun_semantically_dedupes_and_keeps_cache_when_no_new_stats(
    db: psycopg.Connection,
):
    _seed_existing_match(db)
    fetch_window = _fetch([_fixture()])
    first = backfill_stats_chunk(
        db,
        api_key="secret",
        start_date=date(2026, 9, 9),
        end_date=date(2026, 9, 9),
        fetch_window=fetch_window,
    )
    assert first.inserted_stat_rows == 2

    save_checkpoint(
        db,
        checkpoint_key=AGENT2_CACHE_KEY,
        model_version="test-model",
        feature_version="test-feature",
        checkpoint_cutoff=BACKFILL_AT,
        observation_count=2,
        payload=b"{}",
    )
    second = backfill_stats_chunk(
        db,
        api_key="secret",
        start_date=date(2026, 9, 9),
        end_date=date(2026, 9, 9),
        fetch_window=fetch_window,
    )

    assert second.inserted_stat_rows == 0
    assert second.existing_stat_rows == 2
    assert db.execute(
        "SELECT count(*) FROM match_stat_observations WHERE source_system = 'API_TENNIS'"
    ).fetchone()[0] == 2
    assert db.execute(
        "SELECT count(*) FROM live_model_state_checkpoints WHERE checkpoint_key = %s",
        (AGENT2_CACHE_KEY,),
    ).fetchone()[0] == 1


def test_backfill_skips_provider_event_that_is_not_already_canonical(db: psycopg.Connection):
    outcome = backfill_stats_chunk(
        db,
        api_key="secret",
        start_date=date(2026, 9, 9),
        end_date=date(2026, 9, 9),
        fetch_window=_fetch([_fixture(event_key="unseen-event")]),
    )

    assert outcome.terminal_fixtures == 1
    assert outcome.matched_existing == 0
    assert outcome.unmatched_event == 1
    assert outcome.inserted_stat_rows == 0
    assert db.execute("SELECT count(*) FROM matches").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM match_stat_observations").fetchone()[0] == 0


def test_backfill_fails_closed_when_provider_players_do_not_match_existing_event(
    db: psycopg.Connection,
):
    _seed_existing_match(db)
    third_player = db.execute(
        "INSERT INTO players (canonical_name) VALUES ('Player Three') RETURNING player_id"
    ).fetchone()[0]
    db.execute(
        """
        INSERT INTO player_source_ids (player_id, source_system, source_player_id)
        VALUES (%s, 'API_TENNIS', 'p3')
        """,
        (third_player,),
    )

    outcome = backfill_stats_chunk(
        db,
        api_key="secret",
        start_date=date(2026, 9, 9),
        end_date=date(2026, 9, 9),
        fetch_window=_fetch([_fixture(second_player_key="p3")]),
    )

    assert outcome.matched_existing == 0
    assert outcome.identity_mismatch == 1
    assert outcome.inserted_stat_rows == 0
    assert db.execute("SELECT count(*) FROM match_stat_observations").fetchone()[0] == 0


def test_backfill_range_is_bounded(db: psycopg.Connection):
    try:
        backfill_stats_range(
            db,
            api_key="secret",
            start_date=date(2025, 1, 1),
            end_date=date(2026, 9, 17),
            fetch_window=_fetch([]),
        )
    except ValueError as exc:
        assert "window may not exceed" in str(exc)
    else:
        raise AssertionError("expected bounded-window ValueError")
