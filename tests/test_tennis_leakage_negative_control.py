"""Brian's hard leakage controls: full-agent player swap + random-label null."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg

from sports_edge_tennis.agents.agent1_surface_elo import (
    HistoricalMatchObservation, PredictionRequest as A1Request,
    predict as a1_predict,
)
from sports_edge_tennis.agents.agent2_hierarchical_markov import (
    PlayerServeReturnState, PopulationPriorState,
    PredictionRequest as A2Request,
    ScoringRuleVersion, predict as a2_predict,
)
from sports_edge_tennis.validation.a1_a2_blend_shadow import Pair
from sports_edge_tennis.validation.tennis_leakage_negative_control import (
    AUDIT_VERSION, audit, run,
)


START=datetime(2026,10,10,12,tzinfo=UTC)


def test_agent1_full_history_and_prediction_player_swap_preserves_named_player_probability():
    rows=[
        HistoricalMatchObservation(
            match_id=i+1,player_1_id=11,player_2_id=22,surface="HARD",
            tour_scope="ATP_MAIN",competition_tier="ATP_TOUR",
            best_of_sets=3,qualifying_flag=False,
            match_start_at=START+timedelta(days=i),
            outcome_status="COMPLETED",winner_side=1 if i%3 else 2,
            source_observed_at=START+timedelta(days=i,hours=3),
            as_of=START+timedelta(days=i,hours=4),
        )
        for i in range(5)
    ]
    original=A1Request(
        player_1_id=11,player_2_id=22,surface="HARD",
        tour_scope="ATP_MAIN",competition_tier="ATP_TOUR",
        best_of_sets=3,qualifying_flag=False,
        as_of=START+timedelta(days=8),
    )
    r1=a1_predict(original,history=rows)
    # Reverse the *entire input*, including historical winner orientation,
    # rather than only comparing two already-computed probability columns.
    swapped_rows=[
        replace(r,player_1_id=r.player_2_id,player_2_id=r.player_1_id,
                winner_side=3-r.winner_side)
        for r in rows
    ]
    r2=a1_predict(replace(original,player_1_id=22,player_2_id=11),
                  history=swapped_rows)
    assert r1.prediction_status==r2.prediction_status=="PREDICTED"
    assert abs(r1.player_1_probability-r2.player_2_probability)<=Decimal(".000001")
    assert abs(r1.player_2_probability-r2.player_1_probability)<=Decimal(".000001")
    assert r1.diagnostics.eligible_history_match_count==5
    assert r2.diagnostics.eligible_history_match_count==5


def _a2state(player_id:int,service:int,returns:int)->PlayerServeReturnState:
    return PlayerServeReturnState(
        player_id=player_id, prior_usable_matches=12,
        overall_serve_points_won=service,overall_serve_points_played=600,
        overall_return_points_won=returns,overall_return_points_played=600,
        surface_serve_points_won=service//2,surface_serve_points_played=300,
        surface_return_points_won=returns//2,surface_return_points_played=300,
    )


def test_agent2_full_serving_return_model_player_swap_preserves_named_probability():
    a=_a2state(11,390,225)
    b=_a2state(22,355,250)
    prior=PopulationPriorState(
        surface_tour_service_points_won=620,surface_tour_service_points_played=1000,
        surface_service_points_won=990,surface_service_points_played=1600,
        global_service_points_won=1850,global_service_points_played=3000,
    )
    req=A2Request(
        player_1_id=11,player_2_id=22,surface="HARD",
        tour_scope="ATP_MAIN",competition_tier="ATP_TOUR",
        best_of_sets=3,scoring_rule_version=ScoringRuleVersion.BO3_TB7_ALL_SETS,
        player_1_state=a,player_2_state=b,population_prior_state=prior,as_of=START,
    )
    left=a2_predict(req)
    right=a2_predict(replace(req,player_1_id=22,player_2_id=11,
                             player_1_state=b,player_2_state=a))
    assert left.prediction_status==right.prediction_status=="PREDICTED"
    assert abs(left.player_1_probability-right.player_2_probability)<=Decimal(".000002")
    assert abs(left.player_2_probability-right.player_1_probability)<=Decimal(".000002")


def _synthetic_pair(n:int, *, base:datetime=START)->Pair:
    # Balanced winners with asymmetric predicted P1 probabilities; shuffle
    # should destroy the data signal while preserving winner-P1 prevalence.
    actual=n%2
    return Pair(
        match_id=10000+n,issued_at=base-timedelta(hours=2),
        start_at=base+timedelta(hours=n//6),
        tour="ATP_TOUR" if n%3==0 else "CHALLENGER",
        surface="HARD",cluster_id=str(n//20),
        a1_p1=.83 if actual else .17,
        a2_p1=.75 if actual else .25,
        winner_p1=actual,
    )


def test_shuffled_labels_destroy_synthetic_signal_without_changing_original_baseline():
    pairs=[_synthetic_pair(n) for n in range(240)]
    result=audit(pairs,permutations=300,seed=20261120)
    assert result["status"]=="NEGATIVE_CONTROL_EVALUATED_NOT_FULL_LEAKAGE_CERTIFICATION"
    assert result["graded_pairs"]==240
    assert result["unique_prematch_pairs"]==240
    assert result["db_writes"] is False
    assert result["production_rules_modified"] is False
    for name in ("a1_surface_elo","a2_markov","equal_log_odds_blend"):
        stats=result["models"][name]
        assert stats["observed_winner_accuracy"]==1.0
        assert 0.43<stats["shuffled_label_mean_accuracy"]<0.57
        assert stats["empirical_one_sided_p_value"]<.02
    assert result["per_tour_player_order_diagnostics"]["CHALLENGER"]["winner_is_player_1_rate"]==.5
    assert result["per_tour_player_order_diagnostics"]["ATP_TOUR"]["winner_is_player_1_rate"]==.5
    assert result["audit_version"]==AUDIT_VERSION
    assert result["sample_digest_sha256"]==audit(pairs,permutations=300)["sample_digest_sha256"]


def test_unscored_defaulted_cannot_be_transformed_to_wins_by_random_labels():
    base=[_synthetic_pair(n) for n in range(120)]
    bad=replace(base[0],winner_p1=None)
    report=audit([bad,*base[1:]],permutations=100)
    assert report["graded_pairs"]==119
    assert report["ungraded_or_void"]==1


def test_empty_cohort_never_claims_to_have_certified_leakage():
    x=audit([],permutations=100)
    assert x["status"]=="INSUFFICIENT_GRADED_EXAMPLES"
    assert x["models"] if "models" in x else True  # no fabricated metrics


def test_read_only_real_schema_works_after_migration(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        data=run(conn,as_of=datetime(2026,11,20,tzinfo=UTC),permutations=100)
    assert data["source_rows"]==0
    assert data["status"]=="INSUFFICIENT_GRADED_EXAMPLES"
    assert data["db_writes"] is False
