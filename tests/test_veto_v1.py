from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sports_edge_tennis.veto.contracts import PreviousMatchEvidence, VetoContext
from sports_edge_tennis.veto.service import evaluate_veto_context


NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
START = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def _previous(
    *,
    player_id: int,
    target_side: int,
    age: timedelta,
    retired: bool = False,
    sets: tuple[int, int] = (2, 0),
) -> PreviousMatchEvidence:
    return PreviousMatchEvidence(
        player_id=player_id,
        player_side_in_target=target_side,
        match_id=1000 + player_id,
        match_start_at=START - age,
        outcome_status="RETIRED" if retired else "COMPLETED",
        player_retired=retired,
        sets_won_player_1=sets[0],
        sets_won_player_2=sets[1],
    )


def _context(**overrides) -> VetoContext:
    values = {
        "match_id": 99,
        "prediction_run_id": 77,
        "selected_side": 1,
        "decision_as_of": NOW,
        "target_start_at": START,
        "current_match_status": "SCHEDULED",
        "fixture_source_observed_at": NOW - timedelta(minutes=10),
        "previous_matches": (
            _previous(player_id=1, target_side=1, age=timedelta(days=7)),
            _previous(player_id=2, target_side=2, age=timedelta(days=9)),
        ),
        "sharp_move_against_pct": Decimal("0.05"),
    }
    values.update(overrides)
    return VetoContext(**values)


def test_veto_does_not_create_a_block_without_supported_trigger():
    result = evaluate_veto_context(_context())

    assert result.vetoed is False
    assert result.triggered_codes == ()
    assert "INJURY_72H" in result.unavailable_codes
    assert "PREVIOUS_MATCH_MEDICAL_TIMEOUT" in result.unavailable_codes
    assert "PREVIOUS_MATCH_DURATION" in result.unavailable_codes


def test_veto_kills_on_retirement_long_absence_stale_fixture_and_market_move():
    result = evaluate_veto_context(
        _context(
            fixture_source_observed_at=NOW - timedelta(hours=7),
            previous_matches=(
                _previous(
                    player_id=1,
                    target_side=1,
                    age=timedelta(days=50),
                    retired=True,
                ),
                _previous(player_id=2, target_side=2, age=timedelta(days=10)),
            ),
            sharp_move_against_pct=Decimal("0.151"),
        )
    )

    assert result.vetoed is True
    assert "FIXTURE_FEED_STALE" in result.triggered_codes
    assert "PREVIOUS_MATCH_RETIRED" in result.triggered_codes
    assert "RETURN_FROM_6W_ABSENCE" in result.triggered_codes
    assert "ODDS_MOVED_AGAINST_15PCT" in result.triggered_codes


def test_veto_uses_safe_start_time_bound_for_tight_turnaround_and_five_sets():
    result = evaluate_veto_context(
        _context(
            previous_matches=(
                _previous(
                    player_id=1,
                    target_side=1,
                    age=timedelta(hours=17),
                    sets=(3, 2),
                ),
                _previous(player_id=2, target_side=2, age=timedelta(days=4)),
            )
        )
    )

    assert result.vetoed is True
    assert "TIGHT_TURNAROUND_18H" in result.triggered_codes
    assert "FIVE_SET_UNDER_24H" in result.triggered_codes


def test_veto_blocks_current_match_that_is_no_longer_scheduled():
    result = evaluate_veto_context(_context(current_match_status="WALKOVER"))

    assert result.vetoed is True
    assert result.triggered_codes[0] == "CURRENT_MATCH_NOT_SCHEDULED"


def test_missing_generation_price_is_explicitly_unavailable_not_fabricated():
    result = evaluate_veto_context(_context(sharp_move_against_pct=None))

    assert result.vetoed is False
    assert "ODDS_MOVE_BASELINE" in result.unavailable_codes
    assert "ODDS_MOVED_AGAINST_15PCT" not in result.triggered_codes
