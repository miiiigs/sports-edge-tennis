from __future__ import annotations

from decimal import Decimal

from replay_helpers import model_pick, utc, value_bet

from sports_edge_tennis.replay.first_qualified import (
    first_qualified_model_picks,
    first_qualified_value_bets,
    model_pick_metrics,
    value_bet_metrics,
)


def test_first_qualified_model_pick_uses_earliest_run():
    rows = [
        model_pick(match_id=1, run_sequence=2, decision_as_of=utc(12)),
        model_pick(match_id=1, run_sequence=1, decision_as_of=utc(11)),
        model_pick(match_id=2, run_sequence=1, decision_as_of=utc(11)),
    ]
    first = first_qualified_model_picks(rows)
    assert [row.sporting_match_id for row in first] == [1, 2]
    match_one = next(row for row in first if row.sporting_match_id == 1)
    assert match_one.run_sequence == 1
    assert match_one.decision_as_of == utc(11)


def test_multiple_hourly_runs_do_not_inflate_model_pick_metrics():
    rows = [
        model_pick(match_id=1, run_sequence=sequence, decision_as_of=utc(11 + sequence - 1))
        for sequence in (1, 2, 3)
    ]
    metrics = model_pick_metrics(first_qualified_model_picks(rows))
    assert metrics.count == 1


def test_wide_spread_excluded_from_default_model_picks_but_available():
    rows = [
        model_pick(match_id=1, run_sequence=1, classification="MANAGER_QUALIFIED"),
        model_pick(
            match_id=2,
            run_sequence=1,
            classification="A1_A2_AGREE_WIDE_SPREAD_NOT_MANAGER_QUALIFIED",
        ),
    ]
    default = first_qualified_model_picks(rows)
    assert {row.sporting_match_id for row in default} == {1}

    widened = first_qualified_model_picks(
        rows,
        classifications=(
            "MANAGER_QUALIFIED",
            "A1_A2_AGREE_WIDE_SPREAD_NOT_MANAGER_QUALIFIED",
        ),
    )
    assert {row.sporting_match_id for row in widened} == {1, 2}


def test_first_qualified_value_bet_uses_earliest_qualification():
    rows = [
        value_bet(match_id=1, run_sequence=2, decision_as_of=utc(13)),
        value_bet(match_id=1, run_sequence=1, decision_as_of=utc(11)),
    ]
    first = first_qualified_value_bets(rows)
    assert len(first) == 1
    assert first[0].decision_as_of == utc(11)
    assert first[0].run_sequence == 1


def test_value_bet_metrics_one_unit_convention():
    rows = [
        value_bet(match_id=1, actual_result="WIN", units=Decimal("1.30"), price=Decimal("2.30")),
        value_bet(match_id=2, actual_result="LOSS", units=Decimal("-1")),
        value_bet(match_id=3, actual_result="VOID", units=Decimal("0")),
        value_bet(match_id=4, actual_result="CANCELLED", units=Decimal("0")),
        value_bet(match_id=5, actual_result="PENDING", units=None),
    ]
    metrics = value_bet_metrics(rows)
    assert metrics.bets == 5
    assert metrics.settled == 4
    assert metrics.pending == 1
    assert metrics.wins == 1
    assert metrics.losses == 1
    assert metrics.voids == 1
    assert metrics.cancelled == 1
    assert metrics.profit_units == Decimal("0.30")
    assert metrics.roi == Decimal("0.30") / Decimal(4)
    assert metrics.hit_rate == 0.5


def test_model_pick_metrics_ignore_units():
    rows = [
        model_pick(match_id=1, actual_result="WIN"),
        model_pick(match_id=2, actual_result="LOSS"),
        model_pick(match_id=3, actual_result="VOID"),
        model_pick(match_id=4, actual_result="PENDING"),
    ]
    metrics = model_pick_metrics(rows)
    assert metrics.count == 4
    assert metrics.wins == 1
    assert metrics.losses == 1
    assert metrics.voids == 1
    assert metrics.pending == 1
    assert metrics.hit_rate == 0.5


def test_cancelled_and_retired_matches_keep_single_observation():
    rows = [
        value_bet(match_id=9, run_sequence=1, actual_result="VOID", units=Decimal("0")),
        value_bet(match_id=9, run_sequence=2, actual_result="VOID", units=Decimal("0")),
    ]
    first = first_qualified_value_bets(rows)
    assert len(first) == 1
    assert first[0].run_sequence == 1
    assert first[0].actual_result == "VOID"
