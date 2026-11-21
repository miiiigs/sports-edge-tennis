"""Calibration research remains point-in-time, read-only, and non-promotional."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest

from sports_edge_tennis.validation.a1_pit_calibration_shadow import (
    FirstIssue, evaluate, normalize, run, source_sql,
)


def dt(day=1, hour=12):
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def row(*, mid=1, issued=None, prediction=None, start=None,
        status="COMPLETED", winner=1, known=None, prob="0.80",
        side=1, tour="CHALLENGER"):
    issued = issued or dt(1, 10)
    start = start or dt(1, 12)
    return (mid, prediction or issued, issued, Decimal(prob), side,
            start, tour, "HARD", status, winner, known or start+timedelta(hours=3))


def test_first_issue_rejects_after_start_even_with_known_winner():
    records, rejects = normalize([row(issued=dt(1, 11),start=dt(1, 11))], as_of=dt(3))
    assert records == []
    assert rejects == {"LATE_OR_CHRONOLOGICALLY_INVALID_FIRST_ISSUE": 1}


def test_result_after_frozen_start_only_and_latest_defaulted_is_never_scored():
    data = [
        row(mid=1,status="DEFAULTED", winner=1),
        row(mid=2,status="COMPLETED",winner=2),
        row(mid=3,status="COMPLETED",winner=1,known=dt(1,11)),
        row(mid=4,status="WALKOVER",winner=1),
    ]
    rows, rejects = normalize(data,as_of=dt(3))
    assert rejects == {"OUTCOME_KNOWN_BEFORE_FROZEN_START": 1}
    assert {r.match_id for r in rows} == {1, 2, 4}
    assert [r.outcome for r in rows] == [None, 0, None]


def test_source_requires_first_issuance_and_last_overall_result_not_hindsight_completed():
    sql = source_sql()
    assert "DISTINCT ON (p.match_id)" in sql
    assert "ORDER BY p.match_id, p.as_of, p.prediction_id" in sql
    assert "pr.experiment_id IS NULL" in sql
    assert "p.experiment_id IS NULL" in sql
    assert "mv0.ingested_at <= fi.prediction_as_of" in sql
    assert "mro.ingested_at <= %(as_of)s" in sql
    assert "mro.outcome_status = 'COMPLETED'" not in sql
    assert "ORDER BY as_of DESC, source_observed_at DESC" in sql


def series(*, delayed_train=False, delayed_validation=False, holdout_flipped=False):
    """40 unique calendar days, 10 unique matches/day; known outcomes after starts."""
    result = []
    start0 = datetime(2026, 9, 1, 12, tzinfo=UTC)
    for day in range(40):
        start = start0 + timedelta(days=day)
        for j in range(10):
            outcome = (j % 2)
            if holdout_flipped and day >= 32:
                outcome = 1-outcome
            known = start + timedelta(hours=2)
            if delayed_train and day < 24:
                known = start0+timedelta(days=28)
            if delayed_validation and 24 <= day < 32:
                known = start0+timedelta(days=34)
            result.append(FirstIssue(
                match_id=day*100+j,
                issued_at=start-timedelta(hours=2),
                start_at=start, probability=.9,
                outcome=outcome, result_known_at=known,
                tour="CHALLENGER",surface="HARD",
            ))
    return result


def test_actual_holdout_is_not_used_for_calibrator_selection():
    now = datetime(2026,11,20,tzinfo=UTC)
    a = evaluate(series(),as_of=now)
    b = evaluate(series(holdout_flipped=True),as_of=now)
    assert a["status"] == "EVALUATED_RESEARCH_ONLY"
    assert a["first_issued_prematch"] == 400
    assert a["splits"]["train_known_before_validation"] == 240
    assert a["splits"]["validation_known_before_holdout"] == 80
    assert a["splits"]["holdout_scored_as_of"] == 80
    assert a["selected_on_validation"] in {"platt","isotonic"}
    assert b["selected_on_validation"] == a["selected_on_validation"]
    assert a["holdout_brier_improvement"] > .10
    assert a["holdout_log_loss_improvement"] > 0
    assert a["holdout_selected"]["hit_rate"] == a["holdout_raw"]["hit_rate"]
    assert a["release_eligible"] is False


@pytest.mark.parametrize("delay",["train","validation"])
def test_outcomes_not_known_at_fit_boundary_cannot_leak(delay):
    now = datetime(2026,11,20,tzinfo=UTC)
    records = series(delayed_train=delay=="train",
                     delayed_validation=delay=="validation")
    result = evaluate(records,as_of=now)
    assert result["status"] == "INSUFFICIENT_NONLEAKY_TRAIN_VALIDATION_OR_HOLDOUT"
    if delay=="train":
        assert result["splits"]["train_known_before_validation"] == 0
    else:
        assert result["splits"]["validation_known_before_holdout"] == 0


def test_few_dates_cannot_emit_fake_backtest_results():
    records=[FirstIssue(1,dt(1,8),dt(1,12),.7,1,dt(1,14),"CHALLENGER","HARD")]
    report=evaluate(records,as_of=dt(3))
    assert report["status"] == "INSUFFICIENT_DISTINCT_DATES"
    assert report["research_only"]
    assert report["live_selection_changed"] is False


def test_read_only_sql_compiles_against_migrated_postgres(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        report = run(conn,as_of=datetime(2026,11,20,tzinfo=UTC))
    assert report["source_rows"] == 0
    assert report["research_only"] is True
    assert report["db_writes"] is False
    assert report["status"] == "INSUFFICIENT_DISTINCT_DATES"
