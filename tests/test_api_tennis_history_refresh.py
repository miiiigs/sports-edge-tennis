from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest

from sports_edge_tennis.data.agent3_history import (
    Agent3HistoricalMatch,
    build_history_snapshot,
    load_agent3_history,
)
from sports_edge_tennis.data.normalize import normalize_person_lookup
from sports_edge_tennis.live import history_refresh
from sports_edge_tennis.live.history_refresh import (
    HistoryRefreshError,
    fetch_api_tennis_fixture_window,
    parse_terminal_result,
    refresh_history_chunk,
)
from sports_edge_tennis.runner.agent3_live_snapshot import build_live_agent3_history_snapshot


def _received(hour: int = 3) -> datetime:
    return datetime(2026, 9, 9, hour, 0, tzinfo=UTC)


def _fixture(**overrides):
    payload = {
        "event_key": "990001",
        "tournament_key": "7701",
        "tournament_name": "ATP Winston Salem",
        "event_type_type": "Atp Singles",
        "event_date": "2026-08-15",
        "event_time": "12:00",
        "event_status": "Finished",
        "event_first_player": "Carlos Alcaraz",
        "event_second_player": "Alex Example",
        "first_player_key": "api-alcaraz",
        "second_player_key": "api-example",
        "event_surface": "Hard",
        "event_round": "R32",
        "event_winner": "First Player",
        "event_final_result": "2 - 0",
    }
    payload.update(overrides)
    return payload


def _fetch_at(received_at: datetime, fixtures: list[dict]):
    def fetch(_api_key: str, _start: date, _end: date):
        return received_at, fixtures

    return fetch


def test_parse_terminal_result_maps_completed_retired_and_cancelled():
    completed = parse_terminal_result(_fixture())
    assert completed is not None
    assert completed.outcome_status == "COMPLETED"
    assert completed.winner_side == 1
    assert completed.retired_side is None
    assert (completed.sets_won_player_1, completed.sets_won_player_2) == (2, 0)

    retired = parse_terminal_result(
        _fixture(event_status="Retired", event_winner="Second Player", event_final_result="0 - 1")
    )
    assert retired is not None
    assert retired.outcome_status == "RETIRED"
    assert retired.winner_side == 2
    assert retired.retired_side == 1

    cancelled = parse_terminal_result(
        _fixture(event_status="Cancelled", event_winner="First Player", event_final_result="-")
    )
    assert cancelled is not None
    assert cancelled.outcome_status == "CANCELLED"
    assert cancelled.winner_side is None


def test_parse_terminal_result_fails_closed_without_terminal_winner():
    with pytest.raises(HistoryRefreshError, match="missing_terminal_winner"):
        parse_terminal_result(_fixture(event_winner=""))


def test_fetch_fixture_window_uses_one_bounded_provider_request(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"success": 1, "result": [_fixture()]}

    def fake_get(url, *, params, timeout):
        calls.append((url, params, timeout))
        return Response()

    monkeypatch.setattr(history_refresh.requests, "get", fake_get)
    received_at, fixtures = fetch_api_tennis_fixture_window(
        "secret",
        date(2026, 8, 1),
        date(2026, 8, 31),
        clock=lambda: _received(),
    )

    assert received_at == _received()
    assert fixtures == [_fixture()]
    assert len(calls) == 1
    assert calls[0][1]["method"] == "get_fixtures"
    assert calls[0][1]["date_start"] == "2026-08-01"
    assert calls[0][1]["date_stop"] == "2026-08-31"
    assert calls[0][1]["timezone"] == "UTC"

    with pytest.raises(ValueError, match="31 days"):
        fetch_api_tennis_fixture_window(
            "secret",
            date(2026, 1, 1),
            date(2026, 2, 1),
        )


def test_refresh_persists_completed_result_once_and_preserves_pit_visibility(
    db: psycopg.Connection,
):
    received_at = _received()
    outcome = refresh_history_chunk(
        db,
        api_key="secret",
        start_date=date(2026, 8, 15),
        end_date=date(2026, 8, 15),
        fetch_window=_fetch_at(received_at, [_fixture()]),
    )

    assert outcome.fixtures_seen == 1
    assert outcome.fixtures_ingested == 1
    assert outcome.results_inserted == 1
    assert outcome.results_unchanged == 0
    persisted = db.execute(
        """
        SELECT outcome_status::text, winner_side, sets_won_player_1, sets_won_player_2,
               source_observed_at, as_of
        FROM match_result_observations
        """
    ).fetchone()
    assert persisted == ("COMPLETED", 1, 2, 0, received_at, received_at)

    assert load_agent3_history(db, cutoff=received_at - timedelta(microseconds=1)) == []
    history = load_agent3_history(db, cutoff=received_at + timedelta(seconds=1))
    assert len(history) == 1
    assert history[0].visibility_cohort == received_at
    assert history[0].recency_anchor == datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def test_refresh_semantically_dedupes_and_appends_provider_correction(db: psycopg.Connection):
    first_at = _received(3)
    second_at = _received(4)
    third_at = _received(5)

    first = refresh_history_chunk(
        db,
        api_key="secret",
        start_date=date(2026, 8, 15),
        end_date=date(2026, 8, 15),
        fetch_window=_fetch_at(first_at, [_fixture()]),
    )
    duplicate = refresh_history_chunk(
        db,
        api_key="secret",
        start_date=date(2026, 8, 15),
        end_date=date(2026, 8, 15),
        fetch_window=_fetch_at(second_at, [_fixture()]),
    )
    correction = refresh_history_chunk(
        db,
        api_key="secret",
        start_date=date(2026, 8, 15),
        end_date=date(2026, 8, 15),
        fetch_window=_fetch_at(
            third_at,
            [_fixture(event_winner="Second Player", event_final_result="1 - 2")],
        ),
    )

    assert first.results_inserted == 1
    assert duplicate.results_inserted == 0
    assert duplicate.results_unchanged == 1
    assert correction.results_inserted == 1
    assert db.execute("SELECT count(*) FROM match_result_observations").fetchone()[0] == 2
    latest = db.execute(
        """
        SELECT winner_side, sets_won_player_1, sets_won_player_2
        FROM match_result_observations
        ORDER BY as_of DESC
        LIMIT 1
        """
    ).fetchone()
    assert latest == (2, 1, 2)


def _seed_historical_identity(db: psycopg.Connection, name: str) -> int:
    visible_at = datetime(2025, 8, 1, 12, 0, tzinfo=UTC)
    player_id = db.execute(
        "INSERT INTO players (canonical_name, status) VALUES (%s, 'ACTIVE') RETURNING player_id",
        (name,),
    ).fetchone()[0]
    opponent_id = db.execute(
        "INSERT INTO players (canonical_name, status) VALUES ('Historical Opponent', 'ACTIVE') RETURNING player_id"
    ).fetchone()[0]
    db.execute(
        "INSERT INTO player_source_ids (player_id, source_system, source_player_id) VALUES (%s, 'SACKMANN', 'hist-player')",
        (player_id,),
    )
    db.execute(
        "INSERT INTO player_source_ids (player_id, source_system, source_player_id) VALUES (%s, 'SACKMANN', 'hist-opponent')",
        (opponent_id,),
    )
    db.execute(
        """
        INSERT INTO player_aliases (
            player_id, source_system, alias, normalized_alias, first_observed_at, last_observed_at
        ) VALUES (%s, 'TENNIS_DATA', %s, %s, %s, %s)
        """,
        (player_id, name, normalize_person_lookup(name), visible_at, visible_at),
    )
    tournament_id = db.execute(
        """
        INSERT INTO tournaments (canonical_name, tour_scope, competition_tier)
        VALUES ('Historical Open', 'ATP_MAIN', 'ATP_250') RETURNING tournament_id
        """
    ).fetchone()[0]
    edition_id = db.execute(
        """
        INSERT INTO tournament_editions (
            tournament_id, season_year, edition_name, surface, start_date
        ) VALUES (%s, 2025, 'Historical Open 2025', 'HARD', '2025-08-01')
        RETURNING tournament_edition_id
        """,
        (tournament_id,),
    ).fetchone()[0]
    match_id = db.execute(
        "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id",
        (edition_id,),
    ).fetchone()[0]
    db.execute(
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope,
            competition_tier, surface, round_code, best_of_sets, qualifying_flag,
            match_status, source_observed_at, as_of
        ) VALUES (%s, %s, %s, %s, 'ATP_MAIN', 'ATP_250', 'HARD', 'R32', 3,
                  FALSE, 'COMPLETED', %s, %s)
        """,
        (match_id, player_id, opponent_id, visible_at - timedelta(hours=2), visible_at, visible_at),
    )
    db.execute(
        """
        INSERT INTO match_result_observations (
            match_id, outcome_status, winner_side, source_observed_at, as_of
        ) VALUES (%s, 'COMPLETED', 1, %s, %s)
        """,
        (match_id, visible_at, visible_at),
    )
    return int(player_id)


def test_agent3_history_bridges_new_api_results_to_existing_historical_identity(
    db: psycopg.Connection,
):
    historical_id = _seed_historical_identity(db, "Carlos Alcaraz")
    received_at = _received()
    refresh_history_chunk(
        db,
        api_key="secret",
        start_date=date(2026, 8, 15),
        end_date=date(2026, 8, 15),
        fetch_window=_fetch_at(received_at, [_fixture()]),
    )

    history = load_agent3_history(db, cutoff=received_at + timedelta(seconds=1))
    recent = next(item for item in history if item.match_start_at.year == 2026)
    assert recent.player_1_id == historical_id

    api_player_id = db.execute(
        """
        SELECT player_id FROM player_source_ids
        WHERE source_system = 'API_TENNIS' AND source_player_id = 'api-alcaraz'
        """
    ).fetchone()[0]
    assert api_player_id != historical_id


def test_agent3_snapshots_use_match_time_for_decay_only_after_visibility():
    visible_at = _received()
    match_at = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
    observation = Agent3HistoricalMatch(
        match_id=1,
        player_1_id=1,
        player_2_id=2,
        match_start_at=match_at,
        surface="HARD",
        tour_scope="ATP_MAIN",
        competition_tier="ATP_250",
        outcome_status="COMPLETED",
        winner_side=1,
        result_source_observed_at=visible_at,
        result_as_of=visible_at,
        version_source_observed_at=visible_at,
        version_as_of=visible_at,
    )

    before = build_history_snapshot([observation], cutoff=visible_at)
    assert before.players == {}

    cutoff = visible_at + timedelta(seconds=1)
    reference = build_history_snapshot([observation], cutoff=cutoff)
    optimized = build_live_agent3_history_snapshot([observation], cutoff=cutoff)
    assert reference.players[1].all_observations[0].observed_at == match_at
    assert optimized.players[1].all_observations[0].observed_at == match_at

    invalid_start = Agent3HistoricalMatch(
        **{**observation.__dict__, "match_start_at": visible_at + timedelta(days=1)}
    )
    assert invalid_start.recency_anchor == visible_at
