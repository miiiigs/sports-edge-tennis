"""A3 first-unique support diagnostics must not inflate by repeated runs."""
from datetime import UTC,datetime,timedelta

import psycopg
import pytest

from sports_edge_tennis.validation.a3_first_unique_support_frontier import (
    audit,classify,run,
)

AT=datetime(2026,11,18,2,tzinfo=UTC)


def row(mid=1,*,raw=5,eligible=0,q=0.,status="ABSTAINED",
        reason="INSUFFICIENT_MATCHUP_SUPPORT",surface="HARD",
        history1=30,history2=30,identity="RESOLVED",
        degree1=20,degree2=20,start=None,tour="CHALLENGER"):
    return {
        "match_id":mid,"evaluated_at":AT,
        "identity_status":identity,
        "direct_raw":0,"direct_ess":0.,
        "raw_shared":raw,"eligible_shared":eligible,
        "network_q":q,"support_path":"NONE","gate_failed":reason,
        "final_status":status,"degree_1":degree1,"degree_2":degree2,
        "history_1":history1,"history_2":history2,
        "match_start":start or AT+timedelta(hours=10),
        "tour":tour,"surface":surface,
    }


def test_first_abstention_identifies_shared_opponents_lost_in_support():
    x=classify(row())
    assert x["category"]=="RAW_SHARED_BUT_ZERO_ELIGIBLE_EDGES"
    assert x["raw_structural_soft_research_candidate_only"]
    assert x["true_counterfactual_a3_probability_available"] is False


def test_original_a3_prediction_is_control_not_extra_soft_research_pick():
    x=classify(row(status="PREDICTED",reason=None))
    assert x["category"]=="ORIGINAL_A3_PREDICTED"
    assert not x["raw_structural_soft_research_candidate_only"]


def test_unknown_surface_does_not_get_synthetically_filled():
    x=classify(row(surface=None))
    assert x["category"]=="UNKNOWN_SURFACE"
    assert not x["raw_structural_soft_research_candidate_only"]


def test_no_history_or_unresolved_identity_cannot_be_software_guessed():
    assert classify(row(history1=0))["category"]=="ONE_OR_BOTH_PLAYERS_ZERO_MATCH_HISTORY"
    assert classify(row(identity="LIVE_UNRESOLVED"))["category"]=="BOTH_HISTORY_IDENTITIES_UNRESOLVED"
    assert classify(row(raw=0))["category"]=="NO_RAW_SHARED_OPPONENTS"


def test_below_network_gate_and_strong_evidence_separate():
    assert classify(row(eligible=3,q=4))["category"]=="BELOW_NORMAL_NETWORK_SHARED_OR_Q_GATE"
    assert classify(row(eligible=5,q=11))["category"]=="POTENTIALLY_SUPPORTED_BUT_SIGNAL_OR_OTHER_GATE"


def test_outcomes_never_used_and_count_only_first_unique_match():
    d=audit([
        row(1,raw=4,eligible=0),row(2,raw=0),
        row(3,status="PREDICTED",reason=None),
    ],as_of=AT+timedelta(days=1))
    assert d["first_unique_match_diagnostics_count"]==3
    assert d["original_a3_predicted"]==1
    assert d["original_a3_abstained_logged"]==2
    assert d["raw_graph_soft_candidate_hypothesis_count"]==1
    assert d["candidate_sample_count"]==1
    assert d["no_outcomes_or_accuracy_computed"]
    with pytest.raises(ValueError,match="repeated A3"):
        audit([row(1),row(1)],as_of=AT+timedelta(days=1))


def test_future_and_late_original_observations_not_claimed_prematch():
    with pytest.raises(ValueError,match="future diagnostics"):
        audit([row()],as_of=AT-timedelta(minutes=1))
    assert classify(row(start=AT+timedelta(minutes=10)))["category"]=="LATE_OR_NON_FUTURE_EVALUATION"


def test_empty_disposable_db_safe_read_only(migrated_postgres):
    with psycopg.connect(migrated_postgres,autocommit=True) as conn:
        x=run(conn,start=AT-timedelta(days=3),end=AT+timedelta(days=1),
              as_of=AT+timedelta(days=1))
    assert x["first_unique_match_diagnostics_count"]==0
    assert x["no_provider_calls"] and x["no_db_writes"]
