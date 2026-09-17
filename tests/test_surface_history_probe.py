from datetime import UTC, datetime, timedelta

import psycopg

import sports_edge_tennis.live.surface_history_probe as probe
from sports_edge_tennis.live.surface_history_probe import (
    SurfaceHistoryProbeTarget,
    load_probe_targets,
    run_probe,
)
from sports_edge_tennis.live.transport import TransportError


AS_OF = datetime(2026, 9, 17, 15, 0, tzinfo=UTC)


def _fetchval(conn: psycopg.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _insert_surface_gap(conn: psycopg.Connection) -> tuple[int, int]:
    tournament_id = _fetchval(
        conn,
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES ('Rennes', 'CHALLENGER', 'CHALLENGER')
        RETURNING tournament_id
        """,
    )
    edition_id = _fetchval(
        conn,
        """
        INSERT INTO tournament_editions (
            tournament_id, season_year, edition_name, start_date
        ) VALUES (%s, 2026, 'Rennes 2026', %s)
        RETURNING tournament_edition_id
        """,
        (tournament_id, AS_OF.date()),
    )
    match_id = _fetchval(
        conn,
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    )
    p1 = _fetchval(
        conn,
        "INSERT INTO players (canonical_name) VALUES ('Player One') RETURNING player_id",
    )
    p2 = _fetchval(
        conn,
        "INSERT INTO players (canonical_name) VALUES ('Player Two') RETURNING player_id",
    )
    conn.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at,
            tour_scope, competition_tier, round_code, best_of_sets,
            qualifying_flag, match_status, source_observed_at, as_of
        ) VALUES (
            %s, %s, %s, %s, 'CHALLENGER', 'CHALLENGER', 'R32', 3,
            FALSE, 'SCHEDULED', %s, %s
        )
        """,
        (match_id, p1, p2, AS_OF + timedelta(hours=8), AS_OF, AS_OF),
    )
    conn.execute(
        """
        INSERT INTO tournament_source_ids (
            tournament_id, source_system, source_tournament_id
        ) VALUES (%s, 'API_TENNIS', '2706')
        """,
        (tournament_id,),
    )
    return tournament_id, match_id


def test_load_probe_targets_selects_upcoming_api_tennis_surface_gap(
    db: psycopg.Connection,
):
    tournament_id, _match_id = _insert_surface_gap(db)

    targets = load_probe_targets(
        db,
        as_of=AS_OF,
        horizon_days=3,
        max_tournaments=6,
    )

    target = next(item for item in targets if item.tournament_id == tournament_id)
    assert target.tournament_name == 'Rennes'
    assert target.season_year == 2026
    assert target.tournament_keys == ('2706',)


def test_prior_draws_must_agree_before_candidate_is_reported(monkeypatch, capsys):
    target = SurfaceHistoryProbeTarget(
        tournament_id=628,
        tournament_name='Rennes',
        season_year=2026,
        tournament_keys=('2706',),
    )
    monkeypatch.setattr(probe, 'load_probe_targets', lambda *args, **kwargs: (target,))
    calls = []

    def fetch_draw(api_key: str, tournament_key: str, season_year: int):
        calls.append((api_key, tournament_key, season_year))
        return AS_OF, {'tournament': {'tournament_surface': 'Hard'}}

    outcome = run_probe(
        None,
        api_key='KEY123',
        as_of=AS_OF,
        years_back=2,
        fetch_draw=fetch_draw,
    )

    assert calls == [('KEY123', '2706', 2025), ('KEY123', '2706', 2024)]
    assert outcome.provider_calls == 2
    assert outcome.candidates == 1
    assert outcome.conflicts == 0
    assert outcome.no_evidence == 0
    output = capsys.readouterr().out
    assert 'SURFACE_HISTORY_PROBE_CANDIDATE' in output
    assert 'surface=HARD' in output
    assert 'mapping_changed=0' in output


def test_conflicting_prior_draw_surfaces_fail_closed(monkeypatch, capsys):
    target = SurfaceHistoryProbeTarget(
        tournament_id=628,
        tournament_name='Rennes',
        season_year=2026,
        tournament_keys=('2706',),
    )
    monkeypatch.setattr(probe, 'load_probe_targets', lambda *args, **kwargs: (target,))

    def fetch_draw(api_key: str, tournament_key: str, season_year: int):
        surface = 'Hard' if season_year == 2025 else 'Clay'
        return AS_OF, {'tournament': {'tournament_surface': surface}}

    outcome = run_probe(
        None,
        api_key='KEY123',
        as_of=AS_OF,
        years_back=2,
        fetch_draw=fetch_draw,
    )

    assert outcome.candidates == 0
    assert outcome.conflicts == 1
    assert outcome.no_evidence == 0
    output = capsys.readouterr().out
    assert 'SURFACE_HISTORY_PROBE_CONFLICT' in output
    assert 'surfaces=CLAY,HARD' in output
    assert 'mapping_changed=0' in output


def test_transport_failure_and_blank_draw_remain_observational(monkeypatch, capsys):
    target = SurfaceHistoryProbeTarget(
        tournament_id=628,
        tournament_name='Rennes',
        season_year=2026,
        tournament_keys=('2706',),
    )
    monkeypatch.setattr(probe, 'load_probe_targets', lambda *args, **kwargs: (target,))

    def fetch_draw(api_key: str, tournament_key: str, season_year: int):
        if season_year == 2025:
            raise TransportError('HTTP_404', 'not available')
        return AS_OF, {'tournament': {}}

    outcome = run_probe(
        None,
        api_key='KEY123',
        as_of=AS_OF,
        years_back=2,
        fetch_draw=fetch_draw,
    )

    assert outcome.provider_calls == 2
    assert outcome.unavailable_calls == 1
    assert outcome.candidates == 0
    assert outcome.no_evidence == 1
    output = capsys.readouterr().out
    assert 'SURFACE_HISTORY_PROBE_UNAVAILABLE' in output
    assert 'SURFACE_HISTORY_PROBE_NO_EVIDENCE' in output
    assert 'mapping_changed=0' in output
