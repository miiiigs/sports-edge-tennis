from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from sports_edge_tennis.data.agent3_history import load_agent3_history
from sports_edge_tennis.data.model_identity import has_historical_model_identity
from sports_edge_tennis.data.repository import HistoricalRepository
from sports_edge_tennis.runner.targets import (
    _resolve_model_player_id,
    _resolve_model_surface,
    resolve_target,
)


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
    start_at: datetime | None,
    status: str,
    observed_at: datetime,
    name: str,
    surface: str | None = "HARD",
    tour_scope: str = "ATP_MAIN",
    competition_tier: str = "ATP_250",
) -> int:
    date_anchor = start_at or observed_at
    tournament_id = db.execute(
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES (%s, %s, %s)
        RETURNING tournament_id
        """,
        (name, tour_scope, competition_tier),
    ).fetchone()[0]
    edition_id = db.execute(
        """
        INSERT INTO tournament_editions (
            tournament_id, season_year, edition_name, surface, start_date
        )
        VALUES (%s, %s, %s, %s, %s)
        RETURNING tournament_edition_id
        """,
        (tournament_id, date_anchor.year, f"{name} {date_anchor.year}", surface, date_anchor.date()),
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
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'R32', 3,
                FALSE, %s, %s, %s)
        """,
        (
            match_id,
            player_1_id,
            player_2_id,
            start_at,
            tour_scope,
            competition_tier,
            surface,
            status,
            observed_at,
            observed_at,
        ),
    )
    return match_id


def _completed_result(db: psycopg.Connection, *, match_id: int, observed_at: datetime) -> None:
    db.execute(
        """
        INSERT INTO match_result_observations (
            match_id, outcome_status, winner_side, source_observed_at, as_of
        )
        VALUES (%s, 'COMPLETED', 1, %s, %s)
        """,
        (match_id, observed_at, observed_at),
    )


def _seed_completed_history(
    db: psycopg.Connection,
    *,
    player_id: int,
    observed_at: datetime,
    suffix: str,
    unknown_match_start: bool = False,
) -> None:
    opponent = _player(
        db,
        source_system="SACKMANN",
        source_player_id=f"opp-{suffix}",
        name=f"Historical Opponent {suffix}",
        observed_at=observed_at,
    )
    match_id = _match(
        db,
        player_1_id=player_id,
        player_2_id=opponent,
        start_at=None if unknown_match_start else observed_at - timedelta(days=30),
        status="COMPLETED",
        observed_at=observed_at,
        name=f"Historical Event {suffix}",
    )
    _completed_result(db, match_id=match_id, observed_at=observed_at)


def _add_source_id(
    db: psycopg.Connection,
    *,
    player_id: int,
    source_system: str,
    source_player_id: str,
    observed_at: datetime,
) -> None:
    ingest_run_id = _ingest_run(db, source_system, observed_at)
    db.execute(
        """
        INSERT INTO player_source_ids (player_id, source_system, source_player_id, ingest_run_id)
        VALUES (%s, %s, %s, %s)
        """,
        (player_id, source_system, source_player_id, ingest_run_id),
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


def test_live_bridge_uses_pit_result_when_historical_match_start_is_unknown(
    db: psycopg.Connection,
):
    historical_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    prediction_as_of = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)
    historical_id = _player(
        db,
        source_system="SACKMANN",
        source_player_id="hist-navone",
        name="Mariano Navone",
        observed_at=historical_at,
    )
    _seed_completed_history(
        db,
        player_id=historical_id,
        observed_at=historical_at,
        suffix="navone",
        unknown_match_start=True,
    )
    live_id = _player(
        db,
        source_system="API_TENNIS",
        source_player_id="api-navone",
        name="M. Navone",
        observed_at=prediction_as_of - timedelta(minutes=1),
    )

    assert _resolve_model_player_id(db, player_id=live_id, as_of=prediction_as_of) == historical_id


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


def test_missing_live_surface_resolves_from_latest_pit_tournament_history(
    db: psycopg.Connection,
):
    historical_at = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
    prediction_as_of = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)
    player_1 = _player(
        db,
        source_system="SACKMANN",
        source_player_id="surface-p1",
        name="Surface Player One",
        observed_at=historical_at,
    )
    player_2 = _player(
        db,
        source_system="SACKMANN",
        source_player_id="surface-p2",
        name="Surface Player Two",
        observed_at=historical_at,
    )
    historical_match_id = _match(
        db,
        player_1_id=player_1,
        player_2_id=player_2,
        start_at=historical_at - timedelta(days=1),
        status="COMPLETED",
        observed_at=historical_at,
        name="Acapulco",
        surface="HARD",
        competition_tier="ATP_250",
    )
    _completed_result(db, match_id=historical_match_id, observed_at=historical_at)
    historical_tournament_id = db.execute(
        """
        SELECT te.tournament_id
        FROM matches m
        JOIN tournament_editions te ON te.tournament_edition_id = m.tournament_edition_id
        WHERE m.match_id = %s
        """,
        (historical_match_id,),
    ).fetchone()[0]

    surface, status = _resolve_model_surface(
        db,
        tournament_id=int(historical_tournament_id),
        canonical_name="Acapulco",
        tour_scope="ATP_MAIN",
        season_year=2026,
        live_surface=None,
        edition_surface=None,
        as_of=prediction_as_of,
    )

    assert surface == "HARD"
    assert status == "HISTORICAL_TOURNAMENT"


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



def test_persisted_model_identity_bridge_is_pit_activated_and_disableable(
    db: psycopg.Connection,
):
    historical_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    before_activation = datetime(2026, 9, 18, 18, 0, tzinfo=UTC)
    activated_at = datetime(2026, 9, 18, 18, 35, tzinfo=UTC)
    after_activation = datetime(2026, 9, 18, 18, 40, tzinfo=UTC)
    disabled_at = datetime(2026, 9, 18, 19, 0, tzinfo=UTC)
    after_disable = datetime(2026, 9, 18, 19, 5, tzinfo=UTC)

    historical_id = _player(
        db,
        source_system="SACKMANN",
        source_player_id="hist-provider-bridge",
        name="Provider Historical Player",
        observed_at=historical_at,
    )
    _seed_completed_history(
        db,
        player_id=historical_id,
        observed_at=historical_at,
        suffix="provider-bridge",
    )
    live_id = _player(
        db,
        source_system="API_TENNIS",
        source_player_id="api-provider-bridge",
        name="Live Name That Does Not Alias",
        observed_at=before_activation - timedelta(minutes=1),
    )
    evidence_match = _match(
        db,
        player_1_id=live_id,
        player_2_id=historical_id,
        start_at=after_activation + timedelta(hours=2),
        status="SCHEDULED",
        observed_at=before_activation,
        name="Provider Bridge Evidence",
    )

    assert _resolve_model_player_id(db, player_id=live_id, as_of=before_activation) == live_id

    db.execute(
        """
        INSERT INTO model_identity_bridge_events (
            live_player_id,
            action,
            historical_player_id,
            evidence_source_system,
            evidence_match_id,
            evidence_provider_event_id,
            evidence_provider_player_id,
            evidence_provider_name,
            evidence_status,
            evidence_as_of,
            activated_at,
            actor,
            reason
        )
        VALUES (
            %s, 'BRIDGE', %s, 'SPORTRADAR', %s,
            'sr:sport_event:test', 'sr:competitor:test',
            'Provider Historical Player', 'UNIQUE_PIT_CANDIDATE',
            %s, %s, 'test', 'verified exact provider bridge'
        )
        """,
        (live_id, historical_id, evidence_match, before_activation, activated_at),
    )

    assert _resolve_model_player_id(db, player_id=live_id, as_of=before_activation) == live_id
    assert _resolve_model_player_id(db, player_id=live_id, as_of=after_activation) == historical_id

    db.execute(
        """
        INSERT INTO model_identity_bridge_events (
            live_player_id,
            action,
            evidence_as_of,
            activated_at,
            actor,
            reason
        )
        VALUES (%s, 'DISABLE', %s, %s, 'test', 'disable bridge for review')
        """,
        (live_id, activated_at, disabled_at),
    )

    assert _resolve_model_player_id(db, player_id=live_id, as_of=after_disable) == live_id


def test_model_identity_bridge_events_are_append_only(db: psycopg.Connection):
    observed_at = datetime(2026, 9, 18, 18, 0, tzinfo=UTC)
    live_id = _player(
        db,
        source_system="API_TENNIS",
        source_player_id="api-append-only",
        name="Append Only Live",
        observed_at=observed_at,
    )
    historical_id = _player(
        db,
        source_system="SACKMANN",
        source_player_id="hist-append-only",
        name="Append Only Historical",
        observed_at=observed_at - timedelta(days=10),
    )
    match_id = _match(
        db,
        player_1_id=live_id,
        player_2_id=historical_id,
        start_at=observed_at + timedelta(hours=1),
        status="SCHEDULED",
        observed_at=observed_at,
        name="Append Only Evidence",
    )
    event_id = db.execute(
        """
        INSERT INTO model_identity_bridge_events (
            live_player_id, action, historical_player_id,
            evidence_source_system, evidence_match_id,
            evidence_provider_event_id, evidence_provider_player_id,
            evidence_provider_name, evidence_status,
            evidence_as_of, activated_at, actor, reason
        )
        VALUES (
            %s, 'BRIDGE', %s, 'SPORTRADAR', %s,
            'event', 'competitor', 'Historical',
            'UNIQUE_PIT_CANDIDATE', %s, %s, 'test', 'reason'
        )
        RETURNING model_identity_bridge_event_id
        """,
        (live_id, historical_id, match_id, observed_at, observed_at),
    ).fetchone()[0]

    with pytest.raises(psycopg.Error):
        db.execute(
            """
            UPDATE model_identity_bridge_events
            SET reason = 'mutated'
            WHERE model_identity_bridge_event_id = %s
            """,
            (event_id,),
        )


def test_sportradar_live_identity_does_not_disable_historical_bridge(db: psycopg.Connection):
    historical_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    prediction_as_of = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)
    historical_id = _player(
        db,
        source_system="SACKMANN",
        source_player_id="hist-sportradar",
        name="Shared Historical Name",
        observed_at=historical_at,
    )
    _seed_completed_history(
        db,
        player_id=historical_id,
        observed_at=historical_at,
        suffix="sportradar",
    )
    live_id = _player(
        db,
        source_system="API_TENNIS",
        source_player_id="api-sportradar",
        name="Shared Historical Name",
        observed_at=prediction_as_of - timedelta(minutes=2),
    )
    _add_source_id(
        db,
        player_id=live_id,
        source_system="SPORTRADAR",
        source_player_id="sr:competitor:shared",
        observed_at=prediction_as_of - timedelta(minutes=1),
    )

    assert has_historical_model_identity(db, live_id) is False
    assert has_historical_model_identity(db, historical_id) is True
    assert (
        _resolve_model_player_id(db, player_id=live_id, as_of=prediction_as_of)
        == historical_id
    )


def test_sportradar_only_placeholder_identity_fails_closed(db: psycopg.Connection):
    prediction_as_of = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)
    placeholder_id = _player(
        db,
        source_system="SPORTRADAR",
        source_player_id="sr:competitor:placeholder",
        name="WQF3",
        observed_at=prediction_as_of - timedelta(minutes=1),
    )

    assert has_historical_model_identity(db, placeholder_id) is False
    assert (
        _resolve_model_player_id(db, player_id=placeholder_id, as_of=prediction_as_of)
        == placeholder_id
    )


def test_agent3_history_bridges_live_sportradar_players_to_historical_identity(
    db: psycopg.Connection,
):
    historical_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    live_at = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    cutoff = datetime(2026, 7, 2, 0, 0, tzinfo=UTC)

    hist_1 = _player(
        db,
        source_system="SACKMANN",
        source_player_id="a3-hist-1",
        name="A3 Alpha",
        observed_at=historical_at,
    )
    hist_2 = _player(
        db,
        source_system="SACKMANN",
        source_player_id="a3-hist-2",
        name="A3 Beta",
        observed_at=historical_at,
    )
    _seed_completed_history(db, player_id=hist_1, observed_at=historical_at, suffix="a3-1")
    _seed_completed_history(db, player_id=hist_2, observed_at=historical_at, suffix="a3-2")

    live_1 = _player(
        db,
        source_system="API_TENNIS",
        source_player_id="a3-api-1",
        name="A3 Alpha",
        observed_at=live_at,
    )
    live_2 = _player(
        db,
        source_system="API_TENNIS",
        source_player_id="a3-api-2",
        name="A3 Beta",
        observed_at=live_at,
    )
    _add_source_id(
        db,
        player_id=live_1,
        source_system="SPORTRADAR",
        source_player_id="sr:competitor:a3-1",
        observed_at=live_at,
    )
    _add_source_id(
        db,
        player_id=live_2,
        source_system="SPORTRADAR",
        source_player_id="sr:competitor:a3-2",
        observed_at=live_at,
    )

    match_id = _match(
        db,
        player_1_id=live_1,
        player_2_id=live_2,
        start_at=live_at,
        status="COMPLETED",
        observed_at=live_at,
        name="A3 Live Event",
    )
    _completed_result(db, match_id=match_id, observed_at=live_at)

    observations = load_agent3_history(db, cutoff=cutoff)
    bridged = {
        (observation.player_1_id, observation.player_2_id)
        for observation in observations
        if observation.match_id == match_id
    }
    assert bridged == {(hist_1, hist_2)}
