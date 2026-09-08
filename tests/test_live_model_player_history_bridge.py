from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg

from sports_edge_tennis.data.repository import HistoricalRepository
from sports_edge_tennis.runner.targets import _resolve_model_player_id, resolve_target


def _ingest_run(db: psycopg.Connection, source_system: str, observed_at: datetime) -> int:
    return db.execute(
        """
        INSERT INTO ingest_runs (source_system, source_dataset, started_at, status)
        VALUES (%s, 'identity-bridge-test', %s, 'COMPLETED')
        RETURNING ingest_run_id
        """,
        (source_system, observed_at),
    ).fetchone()[0]


def _player(
    db: psycopg.Connection,
    *,
    source_system: str,
    source_player_id: str,
    name: str,
    observed_at: datetime,
) -> int:
    repo = HistoricalRepository(db, _ingest_run(db, source_system, observed_at))
    return repo.create_player_with_source_id(
        source_system=source_system,
        source_player_id=source_player_id,
        canonical_name=name,
        observed_at=observed_at,
    )


def _match(
    db: psycopg.Connection,
    *,
    player_1_id: int,
    player_2_id: int,
    start_at: datetime,
    status: str,
    observed_at: datetime,
    name: str,
) -> int:
    tournament_id = db.execute(
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES (%s, 'ATP_MAIN', 'ATP_250')
        RETURNING tournament_id
        """,
        (name,),
    ).fetchone()[0]
    edition_id = db.execute(
        """
        INSERT INTO tournament_editions (
            tournament_id, season_year, edition_name, surface, start_date
        )
        VALUES (%s, %s, %s, 'HARD', %s)
        RETURNING tournament_edition_id
        """,
        (tournament_id, start_at.year, f"{name} {start_at.year}", start_at.date()),
    ).fetchone()[0]
    match_id = db.execute(
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    ).fetchone()[0]
    db.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at,
            tour_scope, competition_tier, surface, round_code, best_of_sets,
            qualifying_flag, match_status, source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3,
                FALSE, %s, %s, %s)
        """,
        (
            match_id,
            player_1_id,
            player_2_id,
            start_at,
            status,
            observed_at,
            observed_at,
        ),
    )
    return match_id


def _seed_completed_history(
    db: psycopg.Connection,
    *,
    player_id: int,
    observed_at: datetime,
    suffix: str,
) -> None:
    opponent = _player(
        db,
        source_system="SACKMANN",
        source_player_id=f"opp-{suffix}",
        name=f"Historical Opponent {suffix}",
        observed_at=observed_at,
    )
    _match(
        db,
        player_1_id=player_id,
        player_2_id=opponent,
        start_at=observed_at - timedelta(days=30),
        status="COMPLETED",
        observed_at=observed_at,
        name=f"Historical Event {suffix}",
    )


def test_live_only_player_resolves_to_unique_historical_identity(db: psycopg.Connection):
    historical_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    prediction_as_of = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)
    historical_id = _player(
        db,
        source_system="SACKMANN",
        source_player_id="hist-alcaraz",
        name="Carlos Alcaraz",
        observed_at=historical_at,
    )
    _seed_completed_history(db, player_id=historical_id, observed_at=historical_at, suffix="a")
    live_id = _player(
        db,
        source_system="API_TENNIS",
        source_player_id="api-alcaraz",
        name="Carlos Alcaraz",
        observed_at=prediction_as_of - timedelta(minutes=1),
    )

    assert live_id != historical_id
    assert (
        _resolve_model_player_id(db, player_id=live_id, as_of=prediction_as_of)
        == historical_id
    )


def test_live_player_bridge_fails_closed_on_ambiguous_historical_alias(db: psycopg.Connection):
    historical_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    prediction_as_of = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)
    first = _player(
        db,
        source_system="SACKMANN",
        source_player_id="hist-one",
        name="Alex Smith",
        observed_at=historical_at,
    )
    second = _player(
        db,
        source_system="SACKMANN",
        source_player_id="hist-two",
        name="Alex Smith",
        observed_at=historical_at,
    )
    _seed_completed_history(db, player_id=first, observed_at=historical_at, suffix="one")
    _seed_completed_history(db, player_id=second, observed_at=historical_at, suffix="two")
    live_id = _player(
        db,
        source_system="API_TENNIS",
        source_player_id="api-smith",
        name="Alex Smith",
        observed_at=prediction_as_of - timedelta(minutes=1),
    )

    assert _resolve_model_player_id(db, player_id=live_id, as_of=prediction_as_of) == live_id


def test_resolve_target_uses_historical_ids_without_rewriting_live_match_identity(
    db: psycopg.Connection,
):
    historical_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    prediction_as_of = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)

    hist_1 = _player(
        db,
        source_system="SACKMANN",
        source_player_id="hist-p1",
        name="Player One",
        observed_at=historical_at,
    )
    hist_2 = _player(
        db,
        source_system="SACKMANN",
        source_player_id="hist-p2",
        name="Player Two",
        observed_at=historical_at,
    )
    _seed_completed_history(db, player_id=hist_1, observed_at=historical_at, suffix="p1")
    _seed_completed_history(db, player_id=hist_2, observed_at=historical_at, suffix="p2")

    live_1 = _player(
        db,
        source_system="API_TENNIS",
        source_player_id="api-p1",
        name="Player One",
        observed_at=prediction_as_of - timedelta(minutes=2),
    )
    live_2 = _player(
        db,
        source_system="API_TENNIS",
        source_player_id="api-p2",
        name="Player Two",
        observed_at=prediction_as_of - timedelta(minutes=2),
    )
    live_match_id = _match(
        db,
        player_1_id=live_1,
        player_2_id=live_2,
        start_at=prediction_as_of + timedelta(hours=2),
        status="SCHEDULED",
        observed_at=prediction_as_of - timedelta(minutes=1),
        name="Live API Event",
    )

    resolved = resolve_target(db, match_id=live_match_id, as_of=prediction_as_of)
    assert resolved.player_1_id == hist_1
    assert resolved.player_2_id == hist_2

    persisted = db.execute(
        """
        SELECT player_1_id, player_2_id
        FROM match_versions
        WHERE match_id = %s
        ORDER BY match_version_id DESC
        LIMIT 1
        """,
        (live_match_id,),
    ).fetchone()
    assert persisted == (live_1, live_2)
