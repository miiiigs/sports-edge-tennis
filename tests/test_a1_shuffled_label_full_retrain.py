"""A1 full negative-control retrain must not see held-out outcomes or backfills."""
from dataclasses import replace
from datetime import UTC,datetime,timedelta
import pytest

from sports_edge_tennis.validation.a1_shuffled_label_full_retrain import (
    split_and_validate,run_negative_control,
)
from sports_edge_tennis.validation.agent1_walk_forward import ValidationObservation

CUT=datetime(2026,5,1,tzinfo=UTC)
END=datetime(2026,6,30,tzinfo=UTC)


def observation(mid,at,*,winner=1,visible_days=1):
    p1=1+(mid%5)
    p2=10+((mid*3)%7)
    return ValidationObservation(
        season_year=at.year,match_id=mid,
        player_1_id=p1,player_2_id=p2,
        surface="HARD" if mid%2 else "CLAY",
        tour_scope="CHALLENGER",
        competition_tier="CHALLENGER",
        best_of_sets=3,qualifying_flag=False,
        match_start_at=at,
        outcome_status="COMPLETED",winner_side=winner,
        source_observed_at=at+timedelta(days=visible_days),
        as_of=at+timedelta(days=visible_days),
    )


def corpus():
    training=[
        observation(i,datetime(2026,1,1,tzinfo=UTC)+timedelta(hours=12*i),
                    winner=(1 if i%4 else 2))
        for i in range(1,100)
    ]
    evaluation=[
        observation(1000+i,datetime(2026,5,5,tzinfo=UTC)+timedelta(hours=12*i),
                    winner=(1 if i%3 else 2))
        for i in range(1,54)
    ]
    return training+evaluation


def test_full_retrain_is_deterministic_and_training_histories_equal():
    inputs=corpus()
    a=run_negative_control(inputs,train_cutoff=CUT,evaluation_end=END,
                           min_train=60,min_eval=30,repeats=3)
    b=run_negative_control(list(reversed(inputs)),train_cutoff=CUT,
                           evaluation_end=END,min_train=60,min_eval=30,repeats=3)
    assert a==b
    assert a["training_visible_original_outcomes"]==99
    assert a["fixed_evaluation_completed_matches"]==53
    assert a["real_label_trained_model"]["trained_history_count"]==99
    assert all(q["trained_history_count"]==99 for q in a["shuffled_training_retrains"])
    assert all(q["predictions_sha256"] for q in a["shuffled_training_retrains"])
    assert a["historical_evidence_classification"]=="AMBER_RECONSTRUCTED_RESEARCH_ONLY"
    assert a["not_complete_a1_a2_a3_manager_retraining"] is True


def test_holding_outcome_labels_fixed_keeps_predicted_probabilities_invariant():
    values=corpus()
    before=run_negative_control(values,train_cutoff=CUT,evaluation_end=END,
                                min_train=60,min_eval=30,repeats=2)
    after=run_negative_control([
        replace(rec,winner_side=(3-rec.winner_side))
        if rec.match_id>=1000 else rec
        for rec in values
    ],train_cutoff=CUT,evaluation_end=END,min_train=60,min_eval=30,repeats=2)
    # Predictions are made only from training data. Evaluation labels
    # contribute to metrics but cannot leak back into the frozen model.
    assert before["real_label_trained_model"]["forecast_only_sha256"]==after["real_label_trained_model"]["forecast_only_sha256"]
    assert all(a["forecast_only_sha256"]==b["forecast_only_sha256"] for a,b in zip(before["shuffled_training_retrains"],after["shuffled_training_retrains"]))
    # The labelled result hash DOES change, but frozen forecasts cannot.
    assert before["training_label_counts"]==after["training_label_counts"]
    assert before["real_label_trained_model"]["brier"]!=after["real_label_trained_model"]["brier"]



def test_postcutoff_backfill_cannot_train_historical_model():
    rows=corpus()
    updated=[replace(row,as_of=CUT+timedelta(hours=12),
                     source_observed_at=CUT+timedelta(hours=12))
             if row.match_id==7 else row for row in rows]
    train,hold=split_and_validate(updated,train_cutoff=CUT,evaluation_end=END,
                                  min_train=60,min_eval=30)
    assert len(train)==98
    assert len(hold)==53
    assert 7 not in {r.match_id for r in train}


def test_reject_result_time_predating_match_event():
    rows=corpus()
    x=replace(rows[0],source_observed_at=rows[0].match_start_at-timedelta(hours=1),
              as_of=rows[0].match_start_at-timedelta(hours=1))
    with pytest.raises(ValueError,match="before match start"):
        split_and_validate([x]+rows[1:],train_cutoff=CUT,evaluation_end=END)


def test_duplicate_same_match_in_train_and_holdout_fails_closed():
    rows=corpus()
    with pytest.raises(ValueError,match="duplicate match"):
        split_and_validate(rows+[rows[0]],train_cutoff=CUT,evaluation_end=END)


def test_validation_boundary_min_samples_and_no_future_training():
    with pytest.raises(ValueError,match="insufficient time-safe"):
        run_negative_control(corpus()[:9],train_cutoff=CUT,
                             evaluation_end=END,repeats=2)
    with pytest.raises(ValueError,match="predeclare"):
        run_negative_control(corpus(),train_cutoff=CUT,evaluation_end=END,repeats=1)
    with pytest.raises(ValueError,match="evaluation period"):
        run_negative_control(corpus(),train_cutoff=END,evaluation_end=CUT,repeats=2)
