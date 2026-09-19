from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import sports_edge_tennis.live.goalserve_odds as goalserve_odds
from sports_edge_tennis.live.goalserve import GoalserveTennisMatchEnvelope


RECEIVED = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


class _Conn:
    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split()).lower()
        if "from lateral" in normalized and "goalserve_tennis" in normalized:
            return _Result((1, 2, "p1", "p2"))
        raise AssertionError(normalized)


class _Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


def _envelope(event_id="e1"):
    return GoalserveTennisMatchEnvelope(
        category_id="100",
        category_name="Atp - Singles: Test, Hard",
        provider_event_id=event_id,
        match_start_at=datetime(2026, 9, 19, 13, 0, tzinfo=UTC),
        player_1_id="p1",
        player_1_name="A. Player",
        player_2_id="p2",
        player_2_name="B. Player",
        match_payload={
            "id": event_id,
            "player": [
                {"id": "p1", "name": "A. Player"},
                {"id": "p2", "name": "B. Player"},
            ],
            "odds": {
                "type": {
                    "id": "1",
                    "value": "Home/Away",
                    "bookmaker": {
                        "id": "18",
                        "name": "Pinnacle",
                        "odd": [
                            {"name": "1", "value": "1.90"},
                            {"name": "2", "value": "2.00"},
                        ],
                    },
                }
            },
        },
    )


def test_goalserve_odds_ingests_only_exact_or_existing_events(monkeypatch):
    envelopes = (_envelope("exact"), _envelope("missing"), _envelope("ambiguous"))
    monkeypatch.setattr(
        goalserve_odds,
        "iter_goalserve_tennis_envelopes",
        lambda payload: envelopes,
    )

    statuses = iter(
        [
            SimpleNamespace(status="EXACT", match_id=10, candidate=object()),
            SimpleNamespace(status="UNMATCHED", match_id=None, candidate=None),
            SimpleNamespace(status="AMBIGUOUS", match_id=None, candidate=None),
        ]
    )
    monkeypatch.setattr(
        goalserve_odds,
        "reconcile_goalserve_event",
        lambda *a, **k: next(statuses),
    )
    applied = []
    monkeypatch.setattr(
        goalserve_odds,
        "apply_goalserve_identity_bridge",
        lambda *a, **k: applied.append(k["envelope"].provider_event_id),
    )
    monkeypatch.setattr(
        goalserve_odds,
        "build_goalserve_tennis_observation",
        lambda **kwargs: SimpleNamespace(provider_event_id=kwargs["match_payload"]["id"]),
    )
    monkeypatch.setattr(
        goalserve_odds,
        "ingest_provider_observation",
        lambda *a, **k: SimpleNamespace(
            normalized_inserted=2,
            normalized_existing=0,
            unknown_bookmaker=0,
            unknown_participant=0,
        ),
    )

    result = goalserve_odds.ingest_goalserve_tennis_odds(
        _Conn(),
        payload={},
        received_at=RECEIVED,
        candidate_match_ids=(10, 20),
        response_id="response-1",
    )

    assert result.provider_events_seen == 3
    assert result.exact_events == 1
    assert result.unmatched_events == 1
    assert result.ambiguous_events == 1
    assert result.raw_observations == 1
    assert result.normalized_quotes == 2
    assert applied == ["exact"]


def test_goalserve_odds_tracks_raw_only_unknown_bookmaker(monkeypatch):
    monkeypatch.setattr(
        goalserve_odds,
        "iter_goalserve_tennis_envelopes",
        lambda payload: (_envelope(),),
    )
    monkeypatch.setattr(
        goalserve_odds,
        "reconcile_goalserve_event",
        lambda *a, **k: SimpleNamespace(
            status="EXISTING",
            match_id=10,
            candidate=None,
        ),
    )
    monkeypatch.setattr(
        goalserve_odds,
        "apply_goalserve_identity_bridge",
        lambda *a, **k: "EXISTING",
    )
    monkeypatch.setattr(
        goalserve_odds,
        "build_goalserve_tennis_observation",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        goalserve_odds,
        "ingest_provider_observation",
        lambda *a, **k: SimpleNamespace(
            normalized_inserted=0,
            normalized_existing=0,
            unknown_bookmaker=1,
            unknown_participant=0,
        ),
    )

    result = goalserve_odds.ingest_goalserve_tennis_odds(
        _Conn(),
        payload={},
        received_at=RECEIVED,
        candidate_match_ids=(10,),
        response_id="response-1",
    )

    assert result.existing_events == 1
    assert result.raw_observations == 1
    assert result.normalized_quotes == 0
    assert result.unknown_bookmaker_groups == 1


def test_provider_orientation_accepts_reversed_provider_ids():
    envelope = GoalserveTennisMatchEnvelope(
        category_id="100",
        category_name="Atp - Singles: Test, Hard",
        provider_event_id="e1",
        match_start_at=datetime(2026, 9, 19, 13, 0, tzinfo=UTC),
        player_1_id="p2",
        player_1_name="B. Player",
        player_2_id="p1",
        player_2_name="A. Player",
        match_payload={"id": "e1"},
    )
    names_and_ids = goalserve_odds._provider_orientation(
        _Conn(),
        envelope=envelope,
        match_id=10,
        as_of=RECEIVED,
    )
    assert names_and_ids == ("B. Player", "A. Player", "p2", "p1")
