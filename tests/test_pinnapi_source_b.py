from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest

from sports_edge_tennis.odds.contracts import (
    ProviderBookmakerMarket,
    ProviderObservation,
    ProviderSelection,
)
from sports_edge_tennis.odds.providers.pinnapi import (
    PINNAPI_ADAPTER_VERSION,
    PINNAPI_BOOKMAKER_KEY,
    PINNAPI_PROVIDER_CODE,
    PinnapiBoardError,
    build_pinnapi_market,
    parse_pinnapi_board,
    pinnapi_board_identity,
    pinnapi_event_observation_key,
    to_pinnapi_event_evidence,
    to_pinnapi_raw_observation,
)
from sports_edge_tennis.odds.reconciliation import (
    OrientedPinnapiMatch,
    PinnapiEventEvidence,
    reconcile_pinnapi_event,
)


def _received_at() -> datetime:
    return datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)


def _event(**overrides) -> dict:
    payload = {
        "event_id": "1635768302",
        "parent_id": None,
        "league_id": "202301",
        "league_name": "ATP Challenger Mallorca - SF",
        "start": "2026-09-05T13:10:00Z",
        "participants": ["Mark Lajal", "Felix Balshaw"],
        "periods": {
            "num_0": {
                "status": "open",
                "description": "Game",
                "money_line": {"home": 1.5682, "away": 2.55, "draw": None},
            }
        },
    }
    payload.update(overrides)
    return payload


def _board(*events) -> dict:
    return {"events": list(events)}


def _parse(payload, *, board_observation_id="board-1"):
    return parse_pinnapi_board(
        payload,
        received_at=_received_at(),
        board_observation_id=board_observation_id,
    )


def _only_event(result):
    assert len(result.events) == 1
    return result.events[0]


def _orientation() -> OrientedPinnapiMatch:
    return OrientedPinnapiMatch(
        match_id=1,
        match_version_id=2,
        provider_event_id="1635768302",
        home_canonical_player_id=101,
        away_canonical_player_id=202,
        home_selection_side="PLAYER_1",
        away_selection_side="PLAYER_2",
    )


# --- board parsing ---------------------------------------------------------


def test_parses_valid_top_level_event():
    result = _parse(_board(_event()))
    assert len(result.events) == 1
    assert len(result.rejected_events) == 0


def test_ignores_misleading_game_description():
    result = _parse(_board(_event(periods={"num_0": {"status": "open", "description": "Game", "money_line": {"home": 1.5, "away": 2.0, "draw": None}}})))
    assert len(result.events) == 1


def test_extracts_event_id():
    event = _only_event(_parse(_board(_event())))
    assert event.provider_event_id == "1635768302"


def test_extracts_home_away_names():
    event = _only_event(_parse(_board(_event())))
    assert event.home_name == "Mark Lajal"
    assert event.away_name == "Felix Balshaw"


def test_extracts_scheduled_start():
    event = _only_event(_parse(_board(_event())))
    assert event.scheduled_start == datetime(2026, 9, 5, 13, 10, 0, tzinfo=UTC)


def test_extracts_league_evidence():
    event = _only_event(_parse(_board(_event())))
    assert event.league_id == "202301"
    assert event.league_name == "ATP Challenger Mallorca - SF"


def test_extracts_moneyline_home_away_prices():
    event = _only_event(_parse(_board(_event())))
    assert event.home_decimal_price == Decimal("1.5682")
    assert event.away_decimal_price == Decimal("2.55")


# --- child / special -------------------------------------------------------


def test_parent_id_child_not_eligible():
    result = _parse(_board(_event(parent_id="1635768301")))
    assert result.events == ()
    assert result.rejected_events[0].reason == "child_parent_id"


def test_games_child_not_eligible():
    result = _parse(_board(_event(participants=["Mark Lajal (Games)", "Felix Balshaw (Games)"])))
    assert result.events == ()
    assert result.rejected_events[0].reason == "child_suffix"


def test_sets_child_not_eligible():
    result = _parse(_board(_event(participants=["Mark Lajal (Sets)", "Felix Balshaw (Sets)"])))
    assert result.events == ()
    assert result.rejected_events[0].reason == "child_suffix"


def test_non_prematch_not_eligible():
    result = _parse(_board(_event(event_type="live")))
    assert result.events == ()
    assert result.rejected_events[0].reason == "non_prematch"


# --- price validation ------------------------------------------------------


def test_valid_decimal_gt_1_accepted():
    result = _parse(_board(_event()))
    assert _only_event(result).home_decimal_price > Decimal("1")


def test_price_le_1_rejected():
    result = _parse(_board(_event(periods={"num_0": {"status": "open", "money_line": {"home": 1.0, "away": 2.55, "draw": None}}})))
    assert result.events == ()
    assert result.rejected_events[0].reason == "invalid_price:home"


def test_missing_home_rejected():
    result = _parse(_board(_event(periods={"num_0": {"status": "open", "money_line": {"away": 2.55, "draw": None}}})))
    assert result.rejected_events[0].reason == "missing_side:home"


def test_missing_away_rejected():
    result = _parse(_board(_event(periods={"num_0": {"status": "open", "money_line": {"home": 1.5682, "draw": None}}})))
    assert result.rejected_events[0].reason == "missing_side:away"


def test_non_numeric_price_rejected():
    result = _parse(_board(_event(periods={"num_0": {"status": "open", "money_line": {"home": "not-a-number", "away": 2.55, "draw": None}}})))
    assert result.rejected_events[0].reason == "invalid_price:home"


def test_three_way_draw_not_treated_as_selection():
    result = _parse(_board(_event(periods={"num_0": {"status": "open", "money_line": {"home": 1.5682, "away": 2.55, "draw": 3.4}}})))
    assert result.events == ()
    assert result.rejected_events[0].reason == "three_way_structure"


def test_invalid_start_rejected():
    result = _parse(_board(_event(start="not-a-date")))
    assert result.rejected_events[0].reason == "invalid_start"


def test_missing_event_id_rejected():
    result = _parse(_board(_event(event_id=None)))
    assert result.rejected_events[0].reason == "missing_event_id"


# --- observation identity --------------------------------------------------


def test_same_board_replay_same_event_key():
    first = _parse(_board(_event()), board_observation_id="board-1")
    second = _parse(_board(_event()), board_observation_id="board-1")
    assert _only_event(first).provider_event_id == _only_event(second).provider_event_id
    assert first.raw_observations()[0].provider_observation_key == second.raw_observations()[0].provider_observation_key


def test_later_board_different_event_key():
    first = _parse(_board(_event()), board_observation_id="board-1")
    later = _parse(_board(_event()), board_observation_id="board-2")
    assert first.raw_observations()[0].provider_observation_key != later.raw_observations()[0].provider_observation_key


def test_identical_payload_later_board_new_identity():
    payload = _board(_event())
    first = _parse(payload, board_observation_id="board-1")
    later = _parse(payload, board_observation_id="board-2")
    assert first.board_identity != later.board_identity
    assert first.raw_observations()[0].provider_observation_key != later.raw_observations()[0].provider_observation_key


def test_distinct_events_same_board_have_distinct_keys():
    result = _parse(_board(_event(), _event(event_id="1635768303")))
    assert len(result.events) == 2
    keys = {obs.provider_observation_key for obs in result.raw_observations()}
    assert len(keys) == 2


# --- receive time ----------------------------------------------------------


def test_received_at_authoritative():
    obs = _parse(_board(_event())).raw_observations()[0]
    assert obs.received_at == _received_at()
    assert obs.provider_observed_at is None


def test_provider_last_ignored_for_pit():
    event = _event(**{"last": 999999, "last_call": 888888})
    obs = _parse(_board(event)).raw_observations()[0]
    assert obs.provider_observed_at is None
    assert obs.observation_time == _received_at()


def test_event_last_ignored_for_pit():
    event = _event()
    event["last"] = 999999
    obs = _parse(_board(event)).raw_observations()[0]
    assert obs.provider_observed_at is None
    assert obs.observation_time == _received_at()


def test_receive_time_basis_preserved():
    obs = _parse(_board(_event())).raw_observations()[0]
    assert obs.observation_time_basis == "RECEIVE_TIME"
    assert obs.observation_time == _received_at()


# --- reconciliation boundary ---------------------------------------------


def test_parser_does_not_assign_canonical_player_id():
    event = _only_event(_parse(_board(_event())))
    obs = _parse(_board(_event())).raw_observations()[0]
    assert not hasattr(event, "canonical_player_id")
    assert obs.markets == ()
    assert _only_event(_parse(_board(_event()))).home_decimal_price == Decimal("1.5682")


def test_build_market_uses_orientation_not_provider_ids():
    event = _only_event(_parse(_board(_event())))
    market = build_pinnapi_market(event, _orientation())
    assert isinstance(market, ProviderBookmakerMarket)
    assert market.provider_bookmaker_key == PINNAPI_BOOKMAKER_KEY
    assert len(market.selections) == 2
    home, away = market.selections
    assert home.canonical_player_id == 101 and home.provider_player_id is None
    assert away.canonical_player_id == 202 and away.provider_player_id is None
    assert {home.decimal_price, away.decimal_price} == {Decimal("1.5682"), Decimal("2.55")}


def test_parser_produces_step2_compatible_evidence():
    event = _only_event(_parse(_board(_event())))
    evidence = to_pinnapi_event_evidence(event)
    assert isinstance(evidence, PinnapiEventEvidence)
    assert evidence.provider_event_id == "1635768302"
    assert evidence.home_name == "Mark Lajal"
    assert evidence.away_name == "Felix Balshaw"
    assert evidence.scheduled_start == datetime(2026, 9, 5, 13, 10, 0, tzinfo=UTC)
    assert evidence.league_id == "202301"
    assert evidence.league_name == "ATP Challenger Mallorca - SF"
    assert evidence.parent_id is None
    assert evidence.event_type == "prematch"


def test_reversed_participant_ordering_preserves_provider_orientation():
    event = _only_event(_parse(_board(_event(participants=["Felix Balshaw", "Mark Lajal"]))))
    evidence = to_pinnapi_event_evidence(event)
    assert evidence.home_name == "Felix Balshaw"
    assert evidence.away_name == "Mark Lajal"


def test_unsupported_event_rejected_not_raw_normalizable():
    result = _parse(_board(_event(parent_id="123"), _event()))
    assert len(result.rejected_events) == 1
    assert len(result.events) == 1
    assert len(result.raw_observations()) == 1


def test_malformed_board_raises():
    with pytest.raises(PinnapiBoardError):
        _parse({"events": "not-a-list"})
    with pytest.raises(PinnapiBoardError):
        _parse(["not", "a", "mapping"])


def test_board_identity_helpers():
    identity = pinnapi_board_identity(received_at=_received_at(), board_observation_id="board-1")
    key = pinnapi_event_observation_key(board_identity=identity, provider_event_id="1635768302")
    assert key.startswith("PINNAPI_BOARD_V1:")
    assert key.endswith(":EVENT:1635768302")


def test_raw_observation_fields():
    event = _only_event(_parse(_board(_event())))
    obs = to_pinnapi_raw_observation(event, board_identity="PINNAPI_BOARD_V1:x", received_at=_received_at())
    assert obs.provider_code == PINNAPI_PROVIDER_CODE
    assert obs.provider_event_id == "1635768302"
    assert obs.adapter_version == PINNAPI_ADAPTER_VERSION
    assert obs.provider_observed_at is None
    assert obs.received_at == _received_at()


# --- step 2 handoff (PostgreSQL) -----------------------------------------


def _fetchval(conn: psycopg.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _insert_canonical_match(conn: psycopg.Connection) -> dict[str, int]:
    player_1_id = _fetchval(conn, "INSERT INTO players (canonical_name) VALUES ('Mark Lajal') RETURNING player_id")
    player_2_id = _fetchval(conn, "INSERT INTO players (canonical_name) VALUES ('Felix Balshaw') RETURNING player_id")
    tournament_id = _fetchval(
        conn,
        "INSERT INTO tournaments (canonical_name, tour_scope, competition_tier) VALUES ('ATP Challenger Mallorca', 'CHALLENGER', 'CHALLENGER') RETURNING tournament_id",
    )
    edition_id = _fetchval(
        conn,
        "INSERT INTO tournament_editions (tournament_id, season_year, edition_name, start_date) VALUES (%s, 2026, 'ATP Challenger Mallorca 2026', '2026-09-01') RETURNING tournament_edition_id",
        (tournament_id,),
    )
    match_id = _fetchval(conn, "INSERT INTO matches (tournament_edition_id) VALUES (%s) RETURNING match_id", (edition_id,))
    start = datetime(2026, 9, 5, 13, 10, 0, tzinfo=UTC)
    observed = datetime(2026, 9, 4, 10, 0, 0, tzinfo=UTC)
    version_id = _fetchval(
        conn,
        """
        INSERT INTO match_versions (
            match_id, player_1_id, player_2_id, match_start_at, tour_scope, competition_tier,
            round_code, best_of_sets, qualifying_flag, match_status, source_observed_at, as_of
        )
        VALUES (%s, %s, %s, %s, 'CHALLENGER', 'CHALLENGER', 'SF', 3, FALSE, 'SCHEDULED', %s, %s)
        RETURNING match_version_id
        """,
        (match_id, player_1_id, player_2_id, start, observed, observed),
    )
    conn.execute(
        "INSERT INTO match_source_ids (match_id, match_version_id, source_system, source_match_id) VALUES (%s, %s, 'API_TENNIS', 'api-event-1')",
        (match_id, version_id),
    )
    return {"match_id": match_id, "player_1_id": player_1_id, "player_2_id": player_2_id}


def test_top_level_event_passes_into_frozen_reconciliation(db: psycopg.Connection):
    candidate = _insert_canonical_match(db)
    event = _only_event(_parse(_board(_event())))
    evidence = to_pinnapi_event_evidence(event)

    result = reconcile_pinnapi_event(
        db,
        evidence=evidence,
        candidate_match_ids=[candidate["match_id"]],
        as_of=_received_at(),
    )

    assert result.status == "RESOLVED"
    assert result.match is not None
    assert result.match.home_canonical_player_id == candidate["player_1_id"]
    assert result.match.away_canonical_player_id == candidate["player_2_id"]
    assert result.match.home_selection_side == "PLAYER_1"
    assert result.match.away_selection_side == "PLAYER_2"
