from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sports_edge_tennis.live.sharp_blockers import (
    SharpReferenceBlocker,
    format_sharp_blocker,
    load_missing_sharp_reference_blockers,
)


def test_empty_database_has_no_sharp_reference_blockers(db):
    assert load_missing_sharp_reference_blockers(db, prediction_run_id=1) == ()


def test_sharp_blocker_log_is_bounded_and_explicit():
    blocker = SharpReferenceBlocker(
        prediction_run_id=243,
        decision_id=9001,
        match_id=130412,
        selected_side=1,
        consensus_probability=Decimal("0.712345"),
        player_1_name='Player "One"',
        player_2_name="Player Two",
        tournament_name="Example Challenger\nMain Draw",
        match_start_at=datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc),
        pinnapi_mapping_count=0,
        pinnapi_provider_event_id=None,
        sharp_quotes_at_decision=0,
        sharp_quotes_total=0,
        latest_sharp_observed_at=None,
    )

    line = format_sharp_blocker(blocker)

    assert line.startswith("SHARP_BLOCKER prediction_run_id=243")
    assert "match_id=130412" in line
    assert 'player1="Player \'One\'"' in line
    assert 'tournament="Example Challenger Main Draw"' in line
    assert "pinnapi_mapping_count=0" in line
    assert "pinnapi_event_id=-" in line
    assert "sharp_quotes_at_decision=0" in line
    assert "sharp_quotes_total=0" in line
    assert "latest_sharp_at=-" in line
