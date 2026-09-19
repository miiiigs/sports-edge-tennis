from __future__ import annotations

from datetime import UTC, datetime

import psycopg

from sports_edge_tennis.live.api_tennis_fixture_compat import (
    enrich_api_tennis_fixture_surfaces,
)
from sports_edge_tennis.live.transport import TransportError


def _seed_tournament(
    db: psycopg.Connection,
    *,
    tournament_key: str,
    surfaces_by_year: dict[int, str | None],
) -> None:
    tournament_id = db.execute(
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES ('Surface Test', 'CHALLENGER', 'CHALLENGER')
        RETURNING tournament_id
        """
    ).fetchone()[0]
    db.execute(
        """
        INSERT INTO tournament_source_ids (
            tournament_id, source_system, source_tournament_id
        )
        VALUES (%s, 'API_TENNIS', %s)
        """,
        (tournament_id, tournament_key),
    )
    for year, surface in surfaces_by_year.items():
        db.execute(
            """
            INSERT INTO tournament_editions (
                tournament_id, season_year, edition_name, surface, start_date
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                tournament_id,
                year,
                f'Surface Test {year}',
                surface,
                datetime(year, 9, 1, tzinfo=UTC).date(),
            ),
        )


def _fixture(
    tournament_key: str,
    *,
    tournament_name: str = 'Surface Test',
    event_type_type: str = 'Atp Challenger Singles',
) -> dict[str, object]:
    return {
        'event_key': 'surface-test-event',
        'tournament_key': tournament_key,
        'tournament_name': tournament_name,
        'event_type_type': event_type_type,
        'event_date': '2026-09-17',
        'event_time': '12:00',
        'event_surface': None,
    }


def test_successful_draw_without_surface_uses_unanimous_prior_seasons(
    db: psycopg.Connection,
):
    _seed_tournament(
        db,
        tournament_key='2706',
        surfaces_by_year={2024: 'HARD', 2025: 'HARD'},
    )

    def fetch_draw(api_key: str, tournament_key: str, season_year: int):
        assert (api_key, tournament_key, season_year) == ('KEY', '2706', 2026)
        return datetime(2026, 9, 17, 10, 0, tzinfo=UTC), {'tournament': {}}

    rows = enrich_api_tennis_fixture_surfaces(
        db,
        [_fixture('2706')],
        api_key='KEY',
        fetch_draw=fetch_draw,
    )

    assert rows[0]['surface'] == 'Hard'


def test_conflicting_prior_seasons_fail_closed_when_draw_has_no_surface(
    db: psycopg.Connection,
):
    _seed_tournament(
        db,
        tournament_key='2707',
        surfaces_by_year={2024: 'HARD', 2025: 'CLAY'},
    )

    rows = enrich_api_tennis_fixture_surfaces(
        db,
        [_fixture('2707')],
        api_key='KEY',
        fetch_draw=lambda *_args: (
            datetime(2026, 9, 17, 10, 0, tzinfo=UTC),
            {'tournament': {}},
        ),
    )

    assert rows[0].get('surface') is None


def test_draw_transport_failure_does_not_use_prior_season_fallback(
    db: psycopg.Connection,
):
    _seed_tournament(
        db,
        tournament_key='2708',
        surfaces_by_year={2024: 'HARD', 2025: 'HARD'},
    )

    def fetch_draw(*_args):
        raise TransportError('TIMEOUT', 'provider timed out')

    rows = enrich_api_tennis_fixture_surfaces(
        db,
        [_fixture('2708')],
        api_key='KEY',
        fetch_draw=fetch_draw,
    )

    assert rows[0].get('surface') is None


def test_davis_cup_never_inherits_prior_season_surface(
    db: psycopg.Connection,
    capsys,
):
    _seed_tournament(
        db,
        tournament_key='3447',
        surfaces_by_year={2024: 'HARD', 2025: 'HARD'},
    )

    rows = enrich_api_tennis_fixture_surfaces(
        db,
        [
            _fixture(
                '3447',
                tournament_name='ATP Davis Cup - World Group II',
                event_type_type='Atp Davis Cup Singles',
            )
        ],
        api_key='KEY',
        fetch_draw=lambda *_args: (
            datetime(2026, 9, 17, 10, 0, tzinfo=UTC),
            {'tournament': {}},
        ),
    )

    assert rows[0].get('surface') is None
    out = capsys.readouterr().out
    assert 'kind=DRAW_NO_SURFACE_TEAM_EVENT' in out


def test_davis_cup_accepts_explicit_current_draw_surface(
    db: psycopg.Connection,
):
    _seed_tournament(
        db,
        tournament_key='3445',
        surfaces_by_year={2024: 'CLAY', 2025: 'CLAY'},
    )

    rows = enrich_api_tennis_fixture_surfaces(
        db,
        [
            _fixture(
                '3445',
                tournament_name='ATP Davis Cup - World Group I',
                event_type_type='Atp Davis Cup Singles',
            )
        ],
        api_key='KEY',
        fetch_draw=lambda *_args: (
            datetime(2026, 9, 17, 10, 0, tzinfo=UTC),
            {'tournament': {'tournament_surface': 'Hard'}},
        ),
    )

    assert rows[0]['surface'] == 'Hard'
