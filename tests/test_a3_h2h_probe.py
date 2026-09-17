from __future__ import annotations

from datetime import UTC, datetime

import sports_edge_tennis.live.a3_h2h_probe as probe
from sports_edge_tennis.live.a3_h2h_probe import (
    A3H2HProbeTarget,
    load_probe_targets,
    run_probe,
    summarize_h2h_payload,
)
from sports_edge_tennis.live.transport import TransportError


OBSERVED_AT = datetime(2026, 9, 17, 8, 0, tzinfo=UTC)


def _result(
    event_key: str,
    *,
    first: str,
    second: str,
    played_on: str = "2026-08-01",
    event_type: str = "ATP Challenger Singles",
    status: str = "Finished",
    winner: str = "First Player",
):
    return {
        "event_key": event_key,
        "event_date": played_on,
        "event_type_type": event_type,
        "event_status": status,
        "event_winner": winner,
        "first_player_key": first,
        "second_player_key": second,
    }


def test_h2h_probe_filters_scope_lookback_and_counts_shared_opponents():
    first_player = "101"
    second_player = "202"
    shared = [str(value) for value in range(301, 306)]

    payload = {
        "H2H": [
            _result("h1", first=first_player, second=second_player),
            _result("h2", first=second_player, second=first_player),
            _result("h2", first=second_player, second=first_player),  # duplicate event
            _result("h-old", first=first_player, second=second_player, played_on="2021-01-01"),
            _result("h-itf", first=first_player, second=second_player, event_type="ITF Men Singles"),
        ],
        "firstPlayerResults": [
            *[
                _result(f"f-{opponent}", first=first_player, second=opponent)
                for opponent in shared
            ],
            _result("f-itf", first=first_player, second="999", event_type="ITF Men Singles"),
        ],
        "secondPlayerResults": [
            *[
                _result(f"s-{opponent}", first=second_player, second=opponent)
                for opponent in shared
            ],
            _result("s-extra", first=second_player, second="998"),
        ],
    }

    evidence = summarize_h2h_payload(
        payload,
        first_player_key=first_player,
        second_player_key=second_player,
        observed_at=OBSERVED_AT,
    )

    assert evidence.raw_direct_rows == 4
    assert evidence.eligible_direct_rows == 2
    assert evidence.raw_direct_support_met is True
    assert evidence.eligible_first_player_rows == 5
    assert evidence.eligible_second_player_rows == 6
    assert evidence.shared_opponent_count == 5
    assert evidence.raw_shared_support_met is True


def test_empty_database_has_no_a3_h2h_probe_targets(db):
    assert load_probe_targets(db, as_of=OBSERVED_AT, horizon_days=3, limit=10) == ()


def test_provider_failure_is_fail_isolated(monkeypatch, capsys):
    target = A3H2HProbeTarget(
        match_id=77,
        first_player_key="101",
        second_player_key="202",
        match_start_at=datetime(2026, 9, 18, 10, 0, tzinfo=UTC),
        surface="HARD",
    )
    monkeypatch.setattr(probe, "load_probe_targets", lambda *args, **kwargs: (target,))

    def failed_fetch(*args, **kwargs):
        raise TransportError("HTTP_429", "provider throttled")

    rc = run_probe(
        object(),
        api_key="secret",
        as_of=OBSERVED_AT,
        fetch_h2h=failed_fetch,
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "A3_H2H_PROBE_UNAVAILABLE match_id=77 kind=HTTP_429" in out
    assert "A3_H2H_PROBE_SUMMARY targets=1 successes=0 failures=1" in out
    assert "writes=0" in out
