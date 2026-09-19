from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from sports_edge_tennis.live import offered as module
from sports_edge_tennis.live.offered import ingest_api_tennis_offered_odds


def test_offered_coverage_counters_split_identity_provider_and_market_gaps(
    monkeypatch,
    capsys,
):
    identities = {
        1: None,
        2: ("event-2", "p1", "p2"),
        3: ("event-3", "p1", "p2"),
        4: ("event-4", "p1", "p2"),
    }

    monkeypatch.setattr(
        module,
        "_load_api_tennis_identity",
        lambda conn, *, match_id, as_of: identities[match_id],
    )

    def build_observation(**kwargs):
        if kwargs["provider_event_id"] == "event-3":
            markets = ()
        else:
            markets = (SimpleNamespace(provider_bookmaker_key="Book A"),)
        return SimpleNamespace(markets=markets)

    monkeypatch.setattr(module, "build_api_tennis_offered_observation", build_observation)
    monkeypatch.setattr(
        module,
        "ensure_api_tennis_offered_mapping",
        lambda conn, **kwargs: 1,
    )
    monkeypatch.setattr(
        module,
        "ingest_provider_observation",
        lambda conn, observation: SimpleNamespace(
            normalized_inserted=2 if observation.markets else 0,
            normalized_existing=0,
        ),
    )

    outcome = ingest_api_tennis_offered_odds(
        object(),
        odds_result={
            "event-3": {"Home/Away": {}},
            "event-4": {"Home/Away": {}},
        },
        received_at=datetime(2026, 9, 19, 5, 0, tzinfo=UTC),
        candidate_match_ids=(1, 2, 3, 4),
        response_id="test-response",
    )

    assert outcome.candidate_matches == 4
    assert outcome.events_with_odds == 2
    assert outcome.raw_observations == 2
    assert outcome.normalized_quotes == 2
    assert outcome.missing_events == 2
    assert outcome.missing_identity == 1
    assert outcome.provider_event_absent == 1
    assert outcome.events_without_complete_market == 1
    assert outcome.bookmaker_count == 1

    out = capsys.readouterr().out
    assert "OFFERED_BOOKMAKERS keys=Book A" in out
