from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sports_edge_tennis.live.oddspapi_odds as oddspapi_odds
from sports_edge_tennis.live.oddspapi import (
    ODDSPAPI_PLAN_BOOKMAKERS,
    merge_oddspapi_odds_books,
)
from sports_edge_tennis.live.oddspapi_reconciliation import OddspapiEventResolution
from sports_edge_tennis.market.policy import is_fresh
from sports_edge_tennis.odds.contracts import LiveOddsReconciliation
from sports_edge_tennis.odds.normalizer import source_snapshot_id


FIXTURE_ID = "id1203477074753146"


def _price(price):
    return {"active": True, "price": price}


def _book(price_1, price_2):
    return {
        "suspended": False,
        "markets": {
            "121": {
                "marketActive": True,
                "outcomes": {
                    "121": {"players": {"0": _price(price_1)}},
                    "122": {"players": {"0": _price(price_2)}},
                },
            }
        },
    }


def _board_payload() -> dict:
    return {
        "fixtureId": FIXTURE_ID,
        "sportId": 12,
        "tournamentId": 34770,
        "participant1Id": 145122,
        "participant1Name": "M. Martineau",
        "participant2Id": 53483,
        "participant2Name": "D. Masur",
        "startTime": "2026-09-24T09:40:00Z",
        "bookmakerOdds": {
            "pinnacle+30": _book(2.02, 1.854),
            "1xbet": _book(1.89, 1.79),
            "unibet": _book(1.90, 1.86),
            "bet365": _book(1.87, 1.77),
        },
    }


def _install_fakes(monkeypatch, *, resolutions):
    captured = []
    calls = {"reconcile": 0}

    def fake_reconcile(conn, *, envelope, candidate_match_ids, as_of, persist=True):
        assert envelope.fixture_id == FIXTURE_ID
        calls["reconcile"] += 1
        return resolutions

    def fake_ingest(conn, observation):
        captured.append(observation)
        return LiveOddsReconciliation(normalized_inserted=2)

    monkeypatch.setattr(oddspapi_odds, "reconcile_oddspapi_event", fake_reconcile)
    monkeypatch.setattr(oddspapi_odds, "ingest_provider_observation", fake_ingest)
    return captured, calls


def test_warm_cache_board_rows_produce_fresh_observations(monkeypatch):
    received_at = datetime(2026, 9, 24, 9, 6, 56, tzinfo=UTC)
    payload = _board_payload()
    merged = merge_oddspapi_odds_books(
        [],
        [payload],
        trusted_fixture_ids=(FIXTURE_ID,),
    )
    assert len(merged) == 1

    captured, _ = _install_fakes(
        monkeypatch,
        resolutions=OddspapiEventResolution("EXISTING", 130870, None, 1),
    )
    outcome = oddspapi_odds.ingest_oddspapi_tennis_odds(
        None,
        payload_rows=merged,
        received_at=received_at,
        candidate_match_ids=(130870,),
    )

    assert outcome.existing_events == 1
    assert {observation.markets[0].provider_bookmaker_key for observation in captured} == set(
        ODDSPAPI_PLAN_BOOKMAKERS
    )
    assert all(observation.provider_event_id == FIXTURE_ID for observation in captured)
    assert all(observation.observation_time == received_at for observation in captured)
    assert all(
        is_fresh(observation.observation_time, received_at, 15)
        for observation in captured
    )


def test_no_discovery_and_no_cache_fails_closed(monkeypatch):
    payload = _board_payload()
    merged = merge_oddspapi_odds_books([], [payload], trusted_fixture_ids=())
    assert merged == []

    captured, calls = _install_fakes(
        monkeypatch,
        resolutions=OddspapiEventResolution("UNMATCHED", None, None, 0),
    )
    outcome = oddspapi_odds.ingest_oddspapi_tennis_odds(
        None,
        payload_rows=merged,
        received_at=datetime(2026, 9, 24, 9, 6, 56, tzinfo=UTC),
        candidate_match_ids=(130870,),
    )
    assert captured == []
    assert calls["reconcile"] == 0
    assert outcome.normalized_quotes == 0


def test_cached_identity_mismatch_is_rejected(monkeypatch):
    payload = _board_payload()
    merged = merge_oddspapi_odds_books(
        [], [payload], trusted_fixture_ids=(FIXTURE_ID,)
    )
    captured, _ = _install_fakes(
        monkeypatch,
        resolutions=OddspapiEventResolution("CONFLICT", None, None, 1),
    )
    outcome = oddspapi_odds.ingest_oddspapi_tennis_odds(
        None,
        payload_rows=merged,
        received_at=datetime(2026, 9, 24, 9, 6, 56, tzinfo=UTC),
        candidate_match_ids=(999,),
    )
    assert captured == []
    assert outcome.conflict_events == 1
    assert outcome.normalized_quotes == 0


def test_replayed_board_snapshot_is_not_processed_twice(monkeypatch):
    received_at = datetime(2026, 9, 24, 9, 6, 56, tzinfo=UTC)
    payload = _board_payload()
    captured, calls = _install_fakes(
        monkeypatch,
        resolutions=OddspapiEventResolution("EXISTING", 130870, None, 1),
    )
    outcome = oddspapi_odds.ingest_oddspapi_tennis_odds(
        None,
        payload_rows=[payload, payload],
        received_at=received_at,
        candidate_match_ids=(130870,),
    )
    assert calls["reconcile"] == 1
    assert len(captured) == len(ODDSPAPI_PLAN_BOOKMAKERS)
    assert outcome.provider_events_seen == 1


def test_hourly_refresh_advances_market_observation(monkeypatch):
    first = datetime(2026, 9, 24, 8, 7, 40, tzinfo=UTC)
    second = first + timedelta(minutes=59)

    captured, _ = _install_fakes(
        monkeypatch,
        resolutions=OddspapiEventResolution("EXISTING", 130870, None, 1),
    )
    oddspapi_odds.ingest_oddspapi_tennis_odds(
        None,
        payload_rows=[_board_payload()],
        received_at=first,
        candidate_match_ids=(130870,),
    )
    first_observations = list(captured)
    captured.clear()
    oddspapi_odds.ingest_oddspapi_tennis_odds(
        None,
        payload_rows=[_board_payload()],
        received_at=second,
        candidate_match_ids=(130870,),
    )
    second_observations = list(captured)

    assert first_observations and second_observations
    assert first_observations[0].observation_time == first
    assert second_observations[0].observation_time == second
    assert (
        source_snapshot_id(
            observation=first_observations[0],
            market=first_observations[0].markets[0],
        )
        != source_snapshot_id(
            observation=second_observations[0],
            market=second_observations[0].markets[0],
        )
    )
