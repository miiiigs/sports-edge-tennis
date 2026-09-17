from __future__ import annotations

from datetime import UTC, date, datetime

import psycopg

from sports_edge_tennis.data.a2_stats_coverage import A2_STATS_SKIP_REASONS
from sports_edge_tennis.live.api_tennis_match_stats import parse_api_tennis_match_point_stats
from sports_edge_tennis.live.history_refresh import refresh_history_chunk
from sports_edge_tennis.runner.live_model_cache import AGENT2_CACHE_KEY, save_checkpoint


def _received() -> datetime:
    return datetime(2026, 9, 17, 1, 30, tzinfo=UTC)


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


def _fixture(*, statistics=None):
    return {
        "event_key": "990777",
        "tournament_key": "7707",
        "tournament_name": "Tiburon Challenger Men",
        "event_type_type": "Atp Challenger Singles",
        "event_date": "2026-09-16",
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
        "statistics": statistics or [],
    }


def _complete_stats():
    return [
        _stat("p1", "Aces", value="7"),
        _stat("p1", "Double Faults", value="2"),
        _stat("p1", "1st serve points won", value="65%", won=31, total=48),
        _stat("p1", "2nd serve points won", value="50%", won=10, total=20),
        _stat("p2", "Aces", value="4"),
        _stat("p2", "Double Faults", value="3"),
        _stat("p2", "1st serve points won", value="57%", won=25, total=44),
        _stat("p2", "2nd serve points won", value="52%", won=11, total=21),
    ]


def _fetch(fixtures):
    def fetch(_api_key: str, _start: date, _end: date):
        return _received(), fixtures

    return fetch


def test_skip_reason_contract_is_bounded_and_contains_logged_reasons():
    assert A2_STATS_SKIP_REASONS == {
        "MISSING_PLAYER_KEYS",
        "NO_MATCH_PERIOD_STATS",
        "FIRST_PLAYER_INCOMPLETE",
        "SECOND_PLAYER_INCOMPLETE",
        "BOTH_PLAYERS_INCOMPLETE",
        "MISSING_INGESTED_FIXTURE",
        "UNKNOWN_PARSE_GAP",
    }


def test_parse_api_tennis_match_stats_derives_service_and_return_basis():
    parsed = parse_api_tennis_match_point_stats(_fixture(statistics=_complete_stats()))
    assert parsed is not None
    assert parsed.first.service_points_played == 68
    assert parsed.first.service_points_won == 41
    assert parsed.second.service_points_played == 65
    assert parsed.second.service_points_won == 36


def test_parse_api_tennis_match_stats_fails_closed_on_partial_point_totals():
    incomplete = _complete_stats()[:-1]
    parsed = parse_api_tennis_match_point_stats(_fixture(statistics=incomplete))
    assert parsed is None


def test_history_refresh_persists_api_tennis_stats_for_agent2(db: psycopg.Connection):
    save_checkpoint(
        db,
        checkpoint_key=AGENT2_CACHE_KEY,
        model_version="test-model",
        feature_version="test-feature",
        checkpoint_cutoff=_received(),
        observation_count=1,
        payload=b"{}",
    )

    outcome = refresh_history_chunk(
        db,
        api_key="secret",
        start_date=date(2026, 9, 16),
        end_date=date(2026, 9, 16),
        fetch_window=_fetch([_fixture(statistics=_complete_stats())]),
    )
    assert outcome.results_inserted == 1

    rows = db.execute(
        """
        SELECT
            p.canonical_name,
            mso.first_serve_points_won,
            mso.second_serve_points_won,
            mso.service_points_played,
            mso.return_points_won,
            mso.aces,
            mso.double_faults,
            mso.source_observed_at,
            mso.as_of
        FROM match_stat_observations mso
        JOIN players p ON p.player_id = mso.player_id
        WHERE mso.source_system = 'API_TENNIS'
        ORDER BY p.canonical_name
        """
    ).fetchall()
    assert rows == [
        ("Player One", 31, 10, 68, 29, 7, 2, _received(), _received()),
        ("Player Two", 25, 11, 65, 27, 4, 3, _received(), _received()),
    ]

    assert db.execute(
        "SELECT count(*) FROM live_model_state_checkpoints WHERE checkpoint_key = %s",
        (AGENT2_CACHE_KEY,),
    ).fetchone()[0] == 0


def test_history_refresh_semantically_dedupes_same_api_tennis_stats(db: psycopg.Connection):
    fixture = _fixture(statistics=_complete_stats())
    for _ in range(2):
        refresh_history_chunk(
            db,
            api_key="secret",
            start_date=date(2026, 9, 16),
            end_date=date(2026, 9, 16),
            fetch_window=_fetch([fixture]),
        )

    assert db.execute(
        "SELECT count(*) FROM match_stat_observations WHERE source_system = 'API_TENNIS'"
    ).fetchone()[0] == 2


def test_history_refresh_logs_no_match_period_stats_reason(
    db: psycopg.Connection,
    capsys,
):
    refresh_history_chunk(
        db,
        api_key="secret",
        start_date=date(2026, 9, 16),
        end_date=date(2026, 9, 16),
        fetch_window=_fetch([_fixture(statistics=[])]),
    )

    output = capsys.readouterr().out
    assert "API_TENNIS_MATCH_STATS_DETAIL" in output
    assert "eligible_matches=0" in output
    assert "skipped_matches=1" in output
    assert "skip_reasons=NO_MATCH_PERIOD_STATS:1" in output


def test_history_refresh_logs_asymmetric_incomplete_stats_reason(
    db: psycopg.Connection,
    capsys,
):
    incomplete = _complete_stats()[:-1]
    refresh_history_chunk(
        db,
        api_key="secret",
        start_date=date(2026, 9, 16),
        end_date=date(2026, 9, 16),
        fetch_window=_fetch([_fixture(statistics=incomplete)]),
    )

    output = capsys.readouterr().out
    assert "API_TENNIS_MATCH_STATS_DETAIL" in output
    assert "eligible_matches=0" in output
    assert "skipped_matches=1" in output
    assert "skip_reasons=SECOND_PLAYER_INCOMPLETE:1" in output
