"""Deterministic, season-aware historical tournament surface recovery tests.

The live API-Tennis tournament identity and the historical corpus tournament
identity are frequently stored as separate canonical tournaments (for example the
live ``Columbus`` and the historical ``Columbus CH``). Surface is recovered only
from the historical corpus tournament when:

* the tour scope is identical;
* the normalized identity matches after removing the documented `` CH`` edition
  suffix;
* the historical identity carries a non-live corpus source id; and
* **every** point-in-time-visible historical season, across every matched identity,
  agrees on a single surface (a single changed season anywhere in the visible
  history fails closed).

Everything else fails closed. No name similarity, city, month, or player history
is used.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg

from sports_edge_tennis.replay.surface_pit import (
    SURFACE_CONFLICT_AT_T,
    SURFACE_NOT_AVAILABLE_PIT,
    resolve_surface_pit,
)
from sports_edge_tennis.runner.targets import _resolve_model_surface

HISTORICAL_AT = datetime(2025, 9, 1, 12, 0, tzinfo=UTC)
PREDICTION_AS_OF = datetime(2026, 9, 27, 10, 0, tzinfo=UTC)


def _tournament(
    db: psycopg.Connection,
    *,
    name: str,
    scope: str = "CHALLENGER",
    tier: str = "CHALLENGER",
    corpus_source_id: str | None = "sack-1",
) -> int:
    tournament_id = db.execute(
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES (%s, %s, %s) RETURNING tournament_id
        """,
        (name, scope, tier),
    ).fetchone()[0]
    if corpus_source_id is not None:
        db.execute(
            """
            INSERT INTO tournament_source_ids (tournament_id, source_system, source_tournament_id)
            VALUES (%s, 'SACKMANN', %s)
            """,
            (tournament_id, corpus_source_id),
        )
    return int(tournament_id)


def _edition(
    db: psycopg.Connection,
    *,
    tournament_id: int,
    season_year: int,
    surface: str,
    observed_at: datetime = HISTORICAL_AT,
) -> None:
    p1 = db.execute(
        "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id",
        (f"P1 {tournament_id} {season_year}",),
    ).fetchone()[0]
    p2 = db.execute(
        "INSERT INTO players (canonical_name) VALUES (%s) RETURNING player_id",
        (f"P2 {tournament_id} {season_year}",),
    ).fetchone()[0]
    edition_id = db.execute(
        """
        INSERT INTO tournament_editions (tournament_id, season_year, edition_name, surface, start_date)
        VALUES (%s, %s, %s, %s, %s) RETURNING tournament_edition_id
        """,
        (tournament_id, season_year, f"Edition {season_year}", surface, observed_at.date()),
    ).fetchone()[0]
    match_id = db.execute(
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    ).fetchone()[0]
    db.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope, competition_tier,
            surface, round_code, best_of_sets, qualifying_flag, match_status,
            source_observed_at, as_of, ingested_at
        )
        VALUES (%s, %s, %s, %s, 'CHALLENGER', 'CHALLENGER', %s, 'R32', 3, FALSE,
                'COMPLETED', %s, %s, %s)
        """,
        (
            match_id,
            p1,
            p2,
            observed_at - timedelta(days=1),
            surface,
            observed_at,
            observed_at,
            observed_at,
        ),
    )
    db.execute(
        """
        INSERT INTO match_result_observations (
            match_id, outcome_status, winner_side, source_observed_at, as_of, ingested_at
        )
        VALUES (%s, 'COMPLETED', 1, %s, %s, %s)
        """,
        (match_id, observed_at, observed_at, observed_at),
    )


def test_all_hard_history_across_three_seasons_recovers_hard(db: psycopg.Connection):
    live_id = _tournament(db, name="Columbus", corpus_source_id=None)
    historical_id = _tournament(db, name="Columbus CH")
    _edition(db, tournament_id=historical_id, season_year=2023, surface="HARD")
    _edition(db, tournament_id=historical_id, season_year=2024, surface="HARD")
    _edition(db, tournament_id=historical_id, season_year=2025, surface="HARD")

    surface, status = _resolve_model_surface(
        db,
        tournament_id=live_id,
        canonical_name="Columbus",
        tour_scope="CHALLENGER",
        season_year=2026,
        live_surface=None,
        edition_surface=None,
        as_of=PREDICTION_AS_OF,
    )

    assert (surface, status) == ("HARD", "HISTORICAL_TOURNAMENT_NORMALIZED")


def test_cross_season_surface_change_fails_closed(db: psycopg.Connection):
    """One changed season anywhere in visible history must fail closed."""
    live_id = _tournament(db, name="Turin", corpus_source_id=None)
    historical_id = _tournament(db, name="Turin CH")
    _edition(db, tournament_id=historical_id, season_year=2023, surface="CLAY")
    _edition(db, tournament_id=historical_id, season_year=2024, surface="HARD")
    _edition(db, tournament_id=historical_id, season_year=2025, surface="HARD")

    surface, status = _resolve_model_surface(
        db,
        tournament_id=live_id,
        canonical_name="Turin",
        tour_scope="CHALLENGER",
        season_year=2026,
        live_surface=None,
        edition_surface=None,
        as_of=PREDICTION_AS_OF,
    )

    assert surface is None
    assert status == "AMBIGUOUS_HISTORY"


def test_two_consistent_corpus_identities_are_allowed(db: psycopg.Connection):
    """Multiple historical identities are allowed only when fully consistent."""
    live_id = _tournament(db, name="Mouilleron-Le-Captif", corpus_source_id=None)
    first = _tournament(db, name="Mouilleron-Le-Captif CH", corpus_source_id="sack-a")
    second = _tournament(db, name="Mouilleron Le Captif CH", corpus_source_id="sack-b")
    _edition(db, tournament_id=first, season_year=2023, surface="HARD")
    _edition(db, tournament_id=first, season_year=2024, surface="HARD")
    _edition(db, tournament_id=second, season_year=2025, surface="HARD")

    surface, status = _resolve_model_surface(
        db,
        tournament_id=live_id,
        canonical_name="Mouilleron-Le-Captif",
        tour_scope="CHALLENGER",
        season_year=2026,
        live_surface=None,
        edition_surface=None,
        as_of=PREDICTION_AS_OF,
    )

    assert (surface, status) == ("HARD", "HISTORICAL_TOURNAMENT_NORMALIZED")


def test_conflicting_historical_surfaces_fail_closed(db: psycopg.Connection):
    live_id = _tournament(db, name="Mouilleron-Le-Captif", corpus_source_id=None)
    # Two historical identities normalize to the same base and disagree in the
    # latest PIT-visible season.
    historical_id = _tournament(db, name="Mouilleron-Le-Captif CH", corpus_source_id="sack-a")
    other_id = _tournament(db, name="Mouilleron Le Captif CH", corpus_source_id="sack-b")
    _edition(db, tournament_id=historical_id, season_year=2024, surface="HARD")
    _edition(db, tournament_id=historical_id, season_year=2025, surface="HARD")
    _edition(db, tournament_id=other_id, season_year=2025, surface="CLAY")

    surface, status = _resolve_model_surface(
        db,
        tournament_id=live_id,
        canonical_name="Mouilleron-Le-Captif",
        tour_scope="CHALLENGER",
        season_year=2026,
        live_surface=None,
        edition_surface=None,
        as_of=PREDICTION_AS_OF,
    )

    assert surface is None
    assert status == "AMBIGUOUS_HISTORY"


def test_different_tour_scope_is_never_linked(db: psycopg.Connection):
    live_id = _tournament(db, name="Columbus", corpus_source_id=None)
    other = _tournament(db, name="Columbus CH", scope="ATP_MAIN", tier="ATP_250")
    _edition(db, tournament_id=other, season_year=2025, surface="HARD")

    surface, status = _resolve_model_surface(
        db,
        tournament_id=live_id,
        canonical_name="Columbus",
        tour_scope="CHALLENGER",
        season_year=2026,
        live_surface=None,
        edition_surface=None,
        as_of=PREDICTION_AS_OF,
    )

    assert (surface, status) == (None, "UNRESOLVED")


def test_live_only_source_tournament_is_never_used_as_history(db: psycopg.Connection):
    live_id = _tournament(db, name="Columbus", corpus_source_id=None)
    # Same normalized identity but the only source id is a live fixture provider.
    historical_id = db.execute(
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES ('Columbus CH', 'CHALLENGER', 'CHALLENGER') RETURNING tournament_id
        """
    ).fetchone()[0]
    db.execute(
        """
        INSERT INTO tournament_source_ids (tournament_id, source_system, source_tournament_id)
        VALUES (%s, 'API_TENNIS', '2344')
        """,
        (historical_id,),
    )
    _edition(db, tournament_id=int(historical_id), season_year=2025, surface="HARD")

    surface, status = _resolve_model_surface(
        db,
        tournament_id=live_id,
        canonical_name="Columbus",
        tour_scope="CHALLENGER",
        season_year=2026,
        live_surface=None,
        edition_surface=None,
        as_of=PREDICTION_AS_OF,
    )

    assert (surface, status) == (None, "UNRESOLVED")


def test_unrelated_normalized_name_is_never_linked(db: psycopg.Connection):
    live_id = _tournament(db, name="Columbus", corpus_source_id=None)
    other = _tournament(db, name="Curitiba CH")
    _edition(db, tournament_id=other, season_year=2025, surface="CLAY")

    surface, status = _resolve_model_surface(
        db,
        tournament_id=live_id,
        canonical_name="Columbus",
        tour_scope="CHALLENGER",
        season_year=2026,
        live_surface=None,
        edition_surface=None,
        as_of=PREDICTION_AS_OF,
    )

    assert (surface, status) == (None, "UNRESOLVED")


def test_future_historical_surface_evidence_cannot_leak_backward(db: psycopg.Connection):
    live_id = _tournament(db, name="Columbus", corpus_source_id=None)
    historical_id = _tournament(db, name="Columbus CH")
    future = PREDICTION_AS_OF + timedelta(days=30)
    _edition(db, tournament_id=historical_id, season_year=2025, surface="HARD", observed_at=future)

    surface, status = _resolve_model_surface(
        db,
        tournament_id=live_id,
        canonical_name="Columbus",
        tour_scope="CHALLENGER",
        season_year=2026,
        live_surface=None,
        edition_surface=None,
        as_of=PREDICTION_AS_OF,
    )

    assert (surface, status) == (None, "UNRESOLVED")


def test_live_surface_is_never_overridden_by_historical_fallback(db: psycopg.Connection):
    live_id = _tournament(db, name="Turin", corpus_source_id=None)
    historical_id = _tournament(db, name="Turin CH")
    _edition(db, tournament_id=historical_id, season_year=2023, surface="CLAY")
    _edition(db, tournament_id=historical_id, season_year=2025, surface="CLAY")

    surface, status = _resolve_model_surface(
        db,
        tournament_id=live_id,
        canonical_name="Turin",
        tour_scope="CHALLENGER",
        season_year=2026,
        live_surface="HARD",
        edition_surface=None,
        as_of=PREDICTION_AS_OF,
    )

    assert (surface, status) == ("HARD", "LIVE")


def test_current_edition_surface_is_never_overridden_by_historical_fallback(
    db: psycopg.Connection,
):
    live_id = _tournament(db, name="Turin", corpus_source_id=None)
    historical_id = _tournament(db, name="Turin CH")
    _edition(db, tournament_id=historical_id, season_year=2023, surface="CLAY")

    surface, status = _resolve_model_surface(
        db,
        tournament_id=live_id,
        canonical_name="Turin",
        tour_scope="CHALLENGER",
        season_year=2026,
        live_surface=None,
        edition_surface="GRASS",
        as_of=PREDICTION_AS_OF,
    )

    assert (surface, status) == ("GRASS", "CURRENT_EDITION")


def test_replay_surface_pit_fails_closed_on_cross_season_change(db: psycopg.Connection):
    _tournament(db, name="Turin", corpus_source_id=None)
    historical_id = _tournament(db, name="Turin CH")
    _edition(db, tournament_id=historical_id, season_year=2023, surface="CLAY")
    _edition(db, tournament_id=historical_id, season_year=2025, surface="HARD")

    resolution = resolve_surface_pit(
        db,
        match_id=999999,
        as_of=PREDICTION_AS_OF,
        canonical_name="Turin",
        tour_scope="CHALLENGER",
        season_year=2026,
    )

    assert resolution.surface is None
    assert resolution.status == SURFACE_CONFLICT_AT_T


def test_replay_surface_pit_recovers_documented_challenger_suffix(db: psycopg.Connection):
    _tournament(db, name="Columbus", corpus_source_id=None)
    historical_id = _tournament(db, name="Columbus CH")
    _edition(db, tournament_id=historical_id, season_year=2024, surface="HARD")
    _edition(db, tournament_id=historical_id, season_year=2025, surface="HARD")

    resolution = resolve_surface_pit(
        db,
        match_id=999999,
        as_of=PREDICTION_AS_OF,
        canonical_name="Columbus",
        tour_scope="CHALLENGER",
        season_year=2026,
    )

    assert resolution.surface == "HARD"
    assert resolution.authority == "HISTORICAL_MATCH_VERSION_NORMALIZED"


def test_replay_surface_pit_fails_closed_without_evidence(db: psycopg.Connection):
    _tournament(db, name="Columbus", corpus_source_id=None)

    resolution = resolve_surface_pit(
        db,
        match_id=999999,
        as_of=PREDICTION_AS_OF,
        canonical_name="Columbus",
        tour_scope="CHALLENGER",
        season_year=2026,
    )

    assert resolution.surface is None
    assert resolution.status == SURFACE_NOT_AVAILABLE_PIT
