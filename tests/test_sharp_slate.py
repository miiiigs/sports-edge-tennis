from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace as NS

import sports_edge_tennis.live.sharp_slate as sharp_slate


def test_full_slate_normalization_reuses_one_board_identity(monkeypatch):
    received_at = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    events = (NS(provider_event_id="a"), NS(provider_event_id="b"))
    seen = []

    monkeypatch.setattr(
        sharp_slate,
        "_parse_board",
        lambda payload, observed, board_id: NS(
            board_identity=f"BOARD:{board_id}",
            events=events,
            rejected_events=(NS(reason="child-market"),),
        ),
    )

    def fake_process(conn, event, board_identity, observed, candidates, as_of):
        seen.append((event.provider_event_id, board_identity, tuple(candidates)))
        if event.provider_event_id == "a":
            return {"match_id": 11, "normalized": True}
        return {"match_id": None, "normalized": False}

    monkeypatch.setattr(sharp_slate, "_process_event", fake_process)

    outcome = sharp_slate.normalize_pinnapi_board_for_matches(
        None,
        board_payload={"events": []},
        received_at=received_at,
        board_observation_id="fixed-id",
        candidate_match_ids=(11, 22, 33),
        as_of=received_at,
    )

    assert seen == [
        ("a", "BOARD:fixed-id", (11, 22, 33)),
        ("b", "BOARD:fixed-id", (11, 22, 33)),
    ]
    assert outcome.candidate_matches == 3
    assert outcome.board_events == 2
    assert outcome.resolved_events == 1
    assert outcome.normalized_events == 1
    assert outcome.rejected_events == 1


def test_full_slate_emits_bounded_reconciliation_diagnostics(monkeypatch):
    received_at = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    events = (
        NS(
            provider_event_id="pinn-1",
            home_name="Unknown Home",
            away_name="Unknown Away",
            scheduled_start=received_at,
            league_id="321",
            league_name="ATP Challenger Mallorca",
        ),
    )
    logs = []

    monkeypatch.setattr(
        sharp_slate,
        "_parse_board",
        lambda payload, observed, board_id: NS(
            board_identity=f"BOARD:{board_id}",
            events=events,
            rejected_events=(NS(reason="child-market"),),
        ),
    )
    monkeypatch.setattr(sharp_slate, "_load_candidates", lambda conn, **kwargs: {})

    def fake_process(conn, event, board_identity, observed, candidates, as_of):
        return {
            "event_id": event.provider_event_id,
            "reconciliation_status": "UNMATCHED",
            "reconciliation_reason": "participant_mismatch",
            "candidate_match_ids": (),
            "match_id": None,
            "normalized": False,
        }

    monkeypatch.setattr(sharp_slate, "_process_event", fake_process)

    outcome = sharp_slate.normalize_pinnapi_board_for_matches(
        None,
        board_payload={"events": []},
        received_at=received_at,
        board_observation_id="fixed-id",
        candidate_match_ids=(11, 22),
        as_of=received_at,
        log=logs.append,
    )

    assert outcome.resolved_events == 0
    assert outcome.normalized_events == 0
    assert logs[0].startswith("PINNACLE_RECONCILE provider_event_id=pinn-1")
    assert 'home="Unknown Home"' in logs[0]
    assert "reason=participant_mismatch" in logs[0]
    assert "PINNACLE_RECONCILE_SUMMARY events=1 resolved=0" in logs[1]
    assert "no_player_pair=1" in logs[1]
    assert "other=1" in logs[1]
